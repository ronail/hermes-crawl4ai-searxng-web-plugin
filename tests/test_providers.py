"""Behaviour tests for the local web providers.

These assert contracts (capability flags, response shape, the no-network rule
for ``is_available``, per-URL error isolation) rather than current values, and
they run without crawl4ai or a SearXNG checkout installed — the whole point is
that the plugin loads and degrades cleanly when its dependencies are absent.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

# conftest.py registers the repo root in sys.modules as `crawl4ai_searxng`
# before collection, mirroring how the Hermes plugin loader imports it.
from crawl4ai_searxng.crawl4ai_provider import (  # noqa: E402
    Crawl4aiWebSearchProvider,
    _crawl4ai_importable,
)
from crawl4ai_searxng.searxng_provider import (  # noqa: E402
    SearXNGLocalWebSearchProvider,
    _run_async,
)

_ROOT = Path(__file__).resolve().parents[1]


# ── crawl4ai ───────────────────────────────────────────────────────────


class TestCrawl4aiCapabilities:
    def test_is_extract_only(self):
        p = Crawl4aiWebSearchProvider()
        assert p.name == "crawl4ai"
        assert p.supports_extract() is True
        assert p.supports_search() is False

    def test_is_available_tracks_importability(self, monkeypatch):
        p = Crawl4aiWebSearchProvider()
        monkeypatch.setattr(
            "crawl4ai_searxng.crawl4ai_provider._crawl4ai_importable",
            lambda: False,
        )
        assert p.is_available() is False
        monkeypatch.setattr(
            "crawl4ai_searxng.crawl4ai_provider._crawl4ai_importable",
            lambda: True,
        )
        assert p.is_available() is True

    def test_availability_probe_makes_no_network_call(self, monkeypatch):
        """is_available() runs on every `hermes tools` paint — no I/O allowed."""
        import socket

        def _boom(*a, **kw):  # pragma: no cover - only runs on violation
            raise AssertionError("is_available() must not touch the network")

        monkeypatch.setattr(socket.socket, "connect", _boom)
        Crawl4aiWebSearchProvider().is_available()

    def test_setup_schema_declares_no_credentials(self):
        schema = Crawl4aiWebSearchProvider().get_setup_schema()
        assert schema["env_vars"] == []


class TestCrawl4aiExtract:
    def _crawler(self, pages):
        crawler = AsyncMock()
        results = []
        for url, md, title in pages:
            r = AsyncMock()
            r.markdown = md
            r.metadata = {"title": title}
            results.append(r)
        crawler.arun.side_effect = results
        return crawler

    def test_returns_entry_per_url_in_request_order(self):
        pages = [
            ("https://a.example.com", "# A", "Alpha"),
            ("https://b.example.com", "# B", "Beta"),
        ]
        with patch(
            "crawl4ai_searxng.crawl4ai_provider._ensure_crawl4ai"
        ), patch(
            "crawl4ai_searxng.crawl4ai_provider._get_crawler",
            AsyncMock(return_value=self._crawler(pages)),
        ):
            out = asyncio.run(
                Crawl4aiWebSearchProvider().extract([u for u, _, _ in pages])
            )

        assert [e["url"] for e in out] == [u for u, _, _ in pages]
        # `raw_content` mirrors `content` for the legacy extract pipeline.
        assert all(e["raw_content"] == e["content"] for e in out)
        assert out[0]["content"] == "# A"
        assert out[0]["title"] == "Alpha"
        assert "error" not in out[0]

    def test_one_failing_url_does_not_discard_the_batch(self):
        good = AsyncMock()
        good.markdown = "# ok"
        good.metadata = {"title": "Fine"}

        crawler = AsyncMock()
        # First call raises, second succeeds: side_effect entries that are
        # exception instances are raised, anything else is returned.
        crawler.arun.side_effect = [RuntimeError("navigation failed"), good]

        with patch(
            "crawl4ai_searxng.crawl4ai_provider._ensure_crawl4ai"
        ), patch(
            "crawl4ai_searxng.crawl4ai_provider._get_crawler",
            AsyncMock(return_value=crawler),
        ):
            out = asyncio.run(
                Crawl4aiWebSearchProvider().extract(
                    ["https://bad.example.com", "https://good.example.com"]
                )
            )

        assert len(out) == 2
        assert "navigation failed" in out[0]["error"]
        assert out[1]["content"] == "# ok"

    def test_missing_dependency_yields_error_entries_not_an_exception(self):
        """A provider that cannot start must still return one entry per URL."""
        with patch(
            "crawl4ai_searxng.crawl4ai_provider._ensure_crawl4ai",
            side_effect=RuntimeError("crawl4ai is not installed"),
        ):
            out = asyncio.run(
                Crawl4aiWebSearchProvider().extract(["https://a.example.com"])
            )

        assert len(out) == 1
        assert "not installed" in out[0]["error"]


# ── searxng ────────────────────────────────────────────────────────────


class _FakeResponse:
    """Minimal stand-in for an httpx response.

    Deliberately NOT an AsyncMock: httpx's ``.json()`` and ``.status_code`` are
    synchronous, and an AsyncMock would return a coroutine for ``json()``.
    """

    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def _search_with_response(response) -> dict:
    """Drive one search against a stubbed HTTP client and lifecycle start()."""

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return response

    with patch(
        "crawl4ai_searxng.searxng_lifecycle.SearXNGProcessManager.start",
        return_value="http://127.0.0.1:8888",
    ), patch("httpx.AsyncClient", _Client):
        return SearXNGLocalWebSearchProvider().search("hello", limit=5)


class TestSearxngCapabilities:
    def test_is_search_only_with_a_distinct_name(self):
        p = SearXNGLocalWebSearchProvider()
        assert p.name == "searxng-local"
        assert p.supports_search() is True
        assert p.supports_extract() is False

    def test_availability_tracks_a_local_checkout(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SEARXNG_DIR", str(tmp_path))
        # No settings file yet -> unavailable, without any network call.
        assert SearXNGLocalWebSearchProvider().is_available() is False

        settings = tmp_path / "searx" / "settings_hermes.yml"
        settings.parent.mkdir(parents=True)
        settings.write_text("use_default_settings: true\n")
        assert SearXNGLocalWebSearchProvider().is_available() is True

    def test_setup_schema_offers_the_dir_env_var(self):
        schema = SearXNGLocalWebSearchProvider().get_setup_schema()
        assert [e["key"] for e in schema["env_vars"]] == ["SEARXNG_DIR"]


class TestSearxngSearch:
    def test_startup_failure_is_typed_not_raised(self):
        with patch(
            "crawl4ai_searxng.searxng_lifecycle.SearXNGProcessManager.start",
            side_effect=RuntimeError("no checkout"),
        ):
            out = SearXNGLocalWebSearchProvider().search("hello")

        assert out["success"] is False
        assert "startup failed" in out["error"]

    def test_results_are_normalized_and_score_sorted(self):
        payload = {
            "results": [
                {"title": "Low", "url": "https://low.example.com", "content": "c", "score": 0.1},
                {"title": "High", "url": "https://high.example.com", "content": "c", "score": 0.9},
            ]
        }
        out = _search_with_response(_FakeResponse(200, payload))

        assert out["success"] is True
        web = out["data"]["web"]
        assert [r["title"] for r in web] == ["High", "Low"]
        assert [r["position"] for r in web] == [1, 2]
        assert all({"title", "url", "description", "position"} <= set(r) for r in web)

    def test_http_error_is_reported_as_failure(self):
        out = _search_with_response(_FakeResponse(503, {}))

        assert out["success"] is False
        assert "503" in out["error"]


class TestRunAsync:
    def test_runs_coroutine_without_a_loop(self):
        async def _work():
            return "ok"

        assert _run_async(_work()) == "ok"

    def test_runs_coroutine_from_inside_a_running_loop(self):
        """search() is sync but reachable from async callers — must not raise."""

        async def outer():
            async def _work():
                return "nested"

            return _run_async(_work())

        assert asyncio.run(outer()) == "nested"


class TestDeclaredDependencies:
    """crawl4ai must never be declared, as a dependency or an extra.

    crawl4ai pins snowballstemmer~=2.2 (<3); Hermes core pins
    snowballstemmer==3.1.1 for python>=3.14 and PM provisions 3.14. PM resolves
    the plugin member with the rest of the candidate set, so crawl4ai must not
    appear as a dependency *or* as an extra — PM's default selection reaches
    declared extras, and [tool.hermes] opt-in-extras is only honoured on the
    --all-extras path.

    This regressed three times in a row (hard dependency, then extra, then
    opt-in extra). Every version passed a green suite, because nothing asserted
    the manifest. These tests are the guard.

    Narrow rather than blanket: httpx IS declared (searxng_provider imports it
    at call time and its tree is conflict-free). The rule is "no crawl4ai",
    not "no dependencies" — a blanket rule would forbid a legitimate fix for a
    genuinely missing runtime dep, and that is how a real omission
    (``httpx``) would be reintroduced.
    """

    @staticmethod
    def _pyproject() -> dict:
        import tomllib

        with (_ROOT / "pyproject.toml").open("rb") as fh:
            return tomllib.load(fh)

    @staticmethod
    def _requirement_names() -> list[str]:
        import re

        project = TestDeclaredDependencies._pyproject()["project"]
        declared = list(project.get("dependencies", []))
        for group in project.get("optional-dependencies", {}).values():
            declared.extend(group)
        return [re.split(r"[<>=!~\[ ]", d, 1)[0].strip().lower() for d in declared]

    def test_crawl4ai_is_not_a_required_dependency(self):
        deps = self._pyproject()["project"].get("dependencies", [])

        assert "crawl4ai" not in self._requirement_names(), (
            "crawl4ai pins snowballstemmer~=2.2, which conflicts with Hermes core's "
            f"snowballstemmer==3.1.1 on the Python 3.14 PM provisions: {deps}"
        )

    def test_crawl4ai_is_not_an_extra(self):
        extras = self._pyproject()["project"].get("optional-dependencies", {})

        assert "crawl4ai" not in self._requirement_names(), (
            f"PM's default selection reaches every declared extra: {extras}"
        )

    def test_crawl4ai_is_not_a_pip_dependency(self):
        manifest = yaml.safe_load((_ROOT / "plugin.yaml").read_text())

        assert manifest.get("pip_dependencies") in (None, []), manifest.get("pip_dependencies")

    def test_declares_httpx_which_the_provider_imports(self):
        """searxng_provider does `import httpx` at call time — it must be declared.

        Omitting it produced a green local suite (the dev venv has httpx) and
        an ImportError in a clean environment.
        """
        assert "httpx" in self._requirement_names(), self._pyproject()["project"].get("dependencies")




class TestExternalPythonRuntime:
    """plugin.yaml must declare `python_runtime: external`.

    Hermes keeps ONE shared dependency venv, unioned over the default home and
    every live profile. A plugin that declares dependencies becomes a uv
    workspace member keyed by sha256(identity.resolve()), so installing this one
    in two profiles produced two members both named `crawl4ai-searxng` and
    `hermes plugins enable` failed with:

        error: Two workspace members are both named `crawl4ai-searxng`

    `python_runtime: external` makes PluginDeclaration.is_member false
    (pm/plugin_declarations.py:72), so the plugin never joins the union and any
    number of profiles can hold it. Removing it re-breaks multi-profile
    installs, and nothing else in the suite would notice.
    """

    @staticmethod
    def _manifest() -> dict:
        import yaml

        with (_ROOT / "plugin.yaml").open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)

    def test_declares_external_python_runtime(self):
        assert self._manifest().get("python_runtime") == "external", (
            "plugin.yaml must declare python_runtime: external or the plugin "
            "collides in the shared venv when installed in two profiles"
        )
