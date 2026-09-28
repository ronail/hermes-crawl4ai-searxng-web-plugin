"""SearXNG on‑demand lifecycle manager.

Starts SearXNG as a subprocess lazily on the first use and keeps it
alive across multiple ``web_search`` calls in one Hermes session.

Uses OS-level process management (``os.kill``, ``os.waitpid``) instead of
asyncio subprocess Futures so that ``stop()`` works correctly even when
called from a different event loop than the one that created the process.

The process is stopped after ``idle_timeout`` seconds of inactivity
(``threading.Timer``), or on Hermes exit via ``atexit``.

Usage::

    from plugins.web.searxng.lifecycle import get_global_manager

    mgr = get_global_manager()
    url = await mgr.start()       # start on first call, reuse thereafter
    # ... multiple searches ...
    await mgr.stop()              # explicit stop (or idle timeout fires)
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import signal
import socket
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

def _default_searxng_dir() -> str:
    """Checkout location, overridable so this is not hardcoded to one machine.

    Resolution order: ``SEARXNG_DIR`` env var, then ``~/Projects/searxng``.
    """
    return os.path.expanduser(
        os.environ.get("SEARXNG_DIR") or "~/Projects/searxng"
    )


_DEFAULT_SETTINGS = "searx/settings_hermes.yml"
_DEFAULT_PORT = 8888
_STARTUP_TIMEOUT = 45
_IDLE_TIMEOUT = 60  # seconds — reset on every start() call


def _venv_python(searxng_dir: Path) -> Optional[str]:
    """Return the checkout's venv interpreter, or None to fall back to PATH.

    Handles the POSIX ``bin/python3`` layout and the Windows ``Scripts/python.exe``
    one; a checkout with no venv at all falls back to whatever python3 is on
    PATH (the caller reports the failure if that cannot import searxng).
    """
    for rel in (("venv", "bin", "python3"), ("venv", "Scripts", "python.exe")):
        candidate = searxng_dir.joinpath(*rel)
        if candidate.exists():
            return str(candidate)
    return None


def _find_free_port() -> int:
    """Return an available TCP port on loopback."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── Global singleton ──────────────────────────────────────────────────
_global_mgr: Optional[SearXNGProcessManager] = None
_global_lock = threading.Lock()


def _shutdown_global() -> None:
    """atexit hook — stops SearXNG when the Python process exits."""
    global _global_mgr
    if _global_mgr is not None:
        try:
            _global_mgr.stop_sync()
        except Exception:
            pass


atexit.register(_shutdown_global)


def get_global_manager() -> SearXNGProcessManager:
    """Return (or create) the module-level singleton ``SearXNGProcessManager``.

    The singleton is never stopped by the provider itself — it stays alive
    across the Hermes process lifetime and is cleaned up by :func:`atexit`.
    """
    global _global_mgr
    if _global_mgr is None:
        with _global_lock:
            if _global_mgr is None:
                _global_mgr = SearXNGProcessManager(
                    port=_get_searxng_port(),
                    idle_timeout=_IDLE_TIMEOUT,
                )
    return _global_mgr


def _get_searxng_port() -> int:
    """Return the port from SEARXNG_URL, or default 8888."""
    url = os.environ.get("SEARXNG_URL", "").strip()
    if url:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if parsed.port:
            return parsed.port
    return _DEFAULT_PORT


def _kill_process_on_port(port: int) -> None:
    """Kill any process listening on *port* on loopback (if lsof is available).

    This prevents a stale SearXNG from a previous Hermes session from
    holding the port and responding to startup probes while its ``/search``
    endpoint is actually dead.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            pids = [int(p) for p in result.stdout.strip().split()]
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                    logger.warning("Killed stale process on port %d (pid %d)", port, pid)
                except (ProcessLookupError, OSError):
                    pass
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass  # lsof not available or failed — proceed anyway


# ── Process manager ───────────────────────────────────────────────────


class SearXNGProcessManager:
    """Manages the SearXNG subprocess lifecycle.

    Uses OS-level ``os.kill`` / ``os.waitpid`` for process management so
    that ``stop()`` works from any event loop or thread.

    Parameters
    ----------
    idle_timeout:
        Seconds to keep the process alive after the last ``start()`` call.
        Set to 0 to disable the timer.
    """

    def __init__(
        self,
        searxng_dir: Optional[str] = None,
        settings_rel: str = _DEFAULT_SETTINGS,
        port: int = _DEFAULT_PORT,  # fixed port (8888 by default)
        startup_timeout: int = _STARTUP_TIMEOUT,
        idle_timeout: int = _IDLE_TIMEOUT,
    ):
        self.searxng_dir = Path(searxng_dir or _default_searxng_dir())
        self.settings_path = self.searxng_dir / settings_rel
        self.port = port
        self.startup_timeout = startup_timeout
        self.idle_timeout = idle_timeout

        # OS-level process tracking (not asyncio-bound)
        self._pid: Optional[int] = None
        self._base_url: Optional[str] = None

        # Idle timer using threading.Timer (not asyncio — survives loop changes)
        self._idle_timer: Optional[threading.Timer] = None
        self._idle_lock = threading.Lock()

    @property
    def base_url(self) -> str:
        return self._base_url or f"http://127.0.0.1:{self.port}"

    @property
    def is_running(self) -> bool:
        if self._pid is None:
            return False
        try:
            pid, status = os.waitpid(self._pid, os.WNOHANG)
            if pid == self._pid:
                # Process has exited — collect it
                self._pid = None
                self._base_url = None
                return False
            return True
        except ChildProcessError:
            self._pid = None
            return False
        except OSError:
            # Process doesn't exist or other error
            self._pid = None
            return False

    async def start(self) -> str:
        """Start SearXNG (or return its URL if already running).

        Before starting, kills any stale process on the target port so a
        new session never inherits a zombie from a previous one.

        Resets the idle timeout timer on every call.
        """
        if self.is_running:
            self._reset_idle_timer()
            return self.base_url

        self._cancel_idle_timer()

        # Kill any existing process on the target port — a stale SearXNG
        # from a previous Hermes session may still be bound to it, and
        # its / endpoint may respond to probes while /search fails.
        _kill_process_on_port(self.port)

        venv_python = _venv_python(self.searxng_dir)
        python_exe = venv_python or "python3"

        settings_yml = str(self.settings_path)
        if not self.settings_path.exists():
            raise FileNotFoundError(
                f"SearXNG settings not found at {settings_yml}. "
                f"Point SEARXNG_DIR at your checkout, or create the settings "
                f"file ({self.settings_path.name}) inside it."
            )

        logger.info(
            "Starting SearXNG on port %d (settings: %s)",
            self.port,
            settings_yml,
        )

        # Use asyncio to start the subprocess (we need the pid), but
        # track it via self._pid for cross-loop management.
        proc = await asyncio.create_subprocess_exec(
            python_exe,
            "-m",
            "searx.webapp",
            env={
                **os.environ,
                "SEARXNG_PORT": str(self.port),
                "SEARXNG_BIND_ADDRESS": "127.0.0.1",
                "SEARXNG_SECRET": "hermes-managed-secret",
                "SEARXNG_SETTINGS_PATH": settings_yml,
            },
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(self.searxng_dir),
        )
        self._pid = proc.pid

        # Wait for the server to become ready (use plain httpx, no asyncio binding)
        import httpx

        probe_url = f"http://127.0.0.1:{self.port}/"
        deadline = time.time() + self.startup_timeout

        while time.time() < deadline:
            try:
                async with httpx.AsyncClient(timeout=3) as client:
                    resp = await client.get(
                        probe_url,
                        headers={"X-Forwarded-For": "127.0.0.1"},
                    )
                    if resp.status_code < 500:
                        logger.info("SearXNG ready at %s", self.base_url)
                        self._base_url = self.base_url
                        self._reset_idle_timer()
                        return self.base_url
            except (httpx.ConnectError, httpx.TimeoutException):
                pass
            await asyncio.sleep(0.5)

        raise RuntimeError(
            f"SearXNG did not become ready within {self.startup_timeout}s "
            f"on port {self.port}"
        )

    async def stop(self) -> None:
        """Async stop — for use from within an event loop."""
        self._stop_process()

    def stop_sync(self) -> None:
        """Sync stop — for use from threads, atexit, etc."""
        self._cancel_idle_timer()
        self._stop_process()

    def _stop_process(self) -> None:
        """Internal: kill the process using OS signals (no asyncio binding)."""
        pid = self._pid
        if pid is None:
            return

        self._cancel_idle_timer()
        logger.info("Stopping SearXNG (pid %d)", pid)

        try:
            os.kill(pid, signal.SIGTERM)
            # Poll for up to 5 seconds
            for _ in range(10):
                try:
                    wpid, status = os.waitpid(pid, os.WNOHANG)
                    if wpid == pid:
                        break
                except ChildProcessError:
                    break
                time.sleep(0.5)
            else:
                # Timeout — force kill
                logger.warning("SearXNG did not exit gracefully, sending SIGKILL")
                try:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                except (ChildProcessError, OSError):
                    pass
        except (ProcessLookupError, ChildProcessError):
            # Already dead — nothing to do
            pass
        except OSError as exc:
            logger.warning("Failed to stop SearXNG (pid %d): %s", pid, exc)

        self._pid = None
        self._base_url = None

    # ── Idle timer (threading-based, survives event loop changes) ─────

    def _reset_idle_timer(self) -> None:
        """Restart the idle timeout countdown."""
        with self._idle_lock:
            self._cancel_idle_timer_locked()
            if self.idle_timeout > 0:
                self._idle_timer = threading.Timer(
                    self.idle_timeout, self._on_idle_timeout
                )
                self._idle_timer.daemon = True
                self._idle_timer.start()

    def _cancel_idle_timer(self) -> None:
        with self._idle_lock:
            self._cancel_idle_timer_locked()

    def _cancel_idle_timer_locked(self) -> None:
        if self._idle_timer is not None:
            self._idle_timer.cancel()
            self._idle_timer = None

    def _on_idle_timeout(self) -> None:
        """Called by the threading.Timer when idle timeout expires."""
        if self.is_running:
            logger.info(
                "SearXNG idle for %ds, stopping", self.idle_timeout
            )
            self._stop_process()

    # ── Context manager ──────────────────────────────────────────────

    async def __aenter__(self) -> SearXNGProcessManager:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.stop()