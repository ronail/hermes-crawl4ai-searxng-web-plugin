"""SearXNG search — local, on-demand, with cross-call caching.

Starts SearXNG lazily on the first ``search()`` call and keeps it alive for an
idle timeout, so several ``web_search`` calls in one agent message reuse the
same warm process. The process is stopped after the idle window or on process
exit (see ``searxng_lifecycle``).

Search-only: SearXNG aggregates upstream engines but does not fetch or extract
arbitrary URLs, so pair it with an extract backend (this plugin's crawl4ai
provider, or Firecrawl/Tavily).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from agent.web_search_provider import WebSearchProvider

from .searxng_lifecycle import get_global_manager

logger = logging.getLogger(__name__)

_SEARCH_TIMEOUT_SECS = 15


def _searxng_dir_configured() -> bool:
    """True when a SearXNG checkout/settings file is discoverable.

    Read-only and offline: it only inspects the resolved path, so it is safe to
    call from ``is_available()`` on every ``hermes tools`` paint.
    """
    from pathlib import Path

    from .searxng_lifecycle import _default_searxng_dir, _DEFAULT_SETTINGS

    return (Path(_default_searxng_dir()) / _DEFAULT_SETTINGS).is_file()


async def _search_single(base_url: str, query: str, limit: int) -> Dict[str, Any]:
    """POST one search to a running SearXNG and return a normalized dict."""
    import httpx

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": "Hermes/1.0",
    }

    try:
        async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT_SECS) as client:
            resp = await client.post(
                f"{base_url}/search",
                data={"q": query, "format": "json"},
                headers=headers,
            )

        if resp.status_code != 200:
            return {
                "success": False,
                "error": f"SearXNG returned HTTP {resp.status_code}",
            }

        raw_results = resp.json().get("results", [])
        # SearXNG may return a score field; sort descending and cap to limit.
        sorted_results = sorted(
            raw_results, key=lambda r: float(r.get("score", 0)), reverse=True
        )[:limit]

        return {
            "success": True,
            "data": {
                "web": [
                    {
                        "title": str(r.get("title", "")),
                        "url": str(r.get("url", "")),
                        "description": str(r.get("content", "")),
                        "position": i + 1,
                    }
                    for i, r in enumerate(sorted_results)
                ]
            },
        }

    except asyncio.TimeoutError:
        return {
            "success": False,
            "error": f"SearXNG search timed out after {_SEARCH_TIMEOUT_SECS}s",
        }
    except Exception as exc:  # noqa: BLE001 — httpx raises its own
        logger.warning("SearXNG search failed for %r: %s", query, exc)
        return {"success": False, "error": str(exc)}


def _run_async(coro):
    """Run a coroutine from a sync caller, tolerating an already-running loop.

    ``web_search`` dispatches synchronously, but it can be reached from inside a
    running event loop (async tools, delegate children). ``asyncio.run`` raises
    there, so fall back to a dedicated loop on a worker thread. The lifecycle
    manager tracks its process with OS-level primitives rather than loop-bound
    futures, so crossing loops is safe.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class SearXNGLocalWebSearchProvider(WebSearchProvider):
    """Search a locally-run SearXNG, started on demand."""

    @property
    def name(self) -> str:
        return "searxng-local"

    @property
    def display_name(self) -> str:
        return "SearXNG (local, on-demand)"

    def __init__(self) -> None:
        self._mgr = get_global_manager()

    def is_available(self) -> bool:
        # No env var and no network: just "is there a checkout with settings".
        return _searxng_dir_configured()

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    def get_setup_schema(self) -> Dict[str, Any]:
        from .searxng_lifecycle import _default_searxng_dir

        return {
            "name": "SearXNG (local, on-demand)",
            "badge": "local · free · search only",
            "tag": (
                "Runs a SearXNG checkout on this machine, starting it on the "
                "first search and stopping it after an idle timeout. No API key "
                "and no hosted service. Set SEARXNG_DIR if your checkout is not "
                f"at {self._mgr.searxng_dir}. Search-only — pair with an extract "
                "backend."
            ),
            "env_vars": [
                {
                    "key": "SEARXNG_DIR",
                    "prompt": "Path to your SearXNG checkout (optional)",
                    "url": "https://docs.searxng.org/admin/installation-searxng.html",
                }
            ],
        }

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Run one search, (re)using the shared SearXNG process."""
        return _run_async(self._async_search(query, limit))

    async def _async_search(self, query: str, limit: int) -> Dict[str, Any]:
        try:
            base_url = await self._mgr.start()
        except Exception as exc:  # noqa: BLE001 — surfaced to the model
            logger.warning("SearXNG startup failed: %s", exc)
            return {"success": False, "error": f"SearXNG startup failed: {exc}"}
        return await _search_single(base_url, query, limit)
