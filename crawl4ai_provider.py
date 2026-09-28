"""crawl4ai web extraction — local, browser-backed, zero API cost.

Wraps crawl4ai's ``AsyncWebCrawler`` (Playwright/Chromium) so ``web_extract``
can render JavaScript-heavy pages and return clean markdown. Extract-only —
crawl4ai has no search capability, so pair it with any search backend.

Dependency handling follows the Hermes plugin contract: the package is declared
in ``pyproject.toml`` (``dependencies``), so PM installs it before enablement.
``is_available()`` stays read-only and never installs, because it runs on every
``hermes tools`` paint.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)

# Module-level caches: the crawler is expensive to build, and keeping it warm
# lets several extract() calls in one agent message share one browser session.
_crawler: Optional["AsyncWebCrawler"] = None  # type: ignore[name-defined]
_crawler_lock = asyncio.Lock()
_crawler_started = False

# Per-URL ceiling. A JS-heavy page behind a slow CDN can exceed this; the whole
# batch is bounded by web_tools' own dispatch timeout as well.
_EXTRACT_TIMEOUT_SECS = 60


def _crawl4ai_importable() -> bool:
    """True when the crawl4ai package imports. Read-only: never installs."""
    try:
        import crawl4ai  # noqa: F401
        return True
    except ImportError:
        return False


def _ensure_crawl4ai() -> None:
    """Import crawl4ai, installing it on demand.

    The dependency is declared in pyproject.toml, so PM normally has it already.
    This is the belt-and-braces path for a hand-built venv: ask PM to sync the
    extra rather than shelling out to pip ourselves. Raises ``pm.InstallError``
    (a restart may be required) or ``ImportError`` if the package still will not
    import.
    """
    if _crawl4ai_importable():
        return
    from pm import InstallError, ensure_import

    try:
        ensure_import("crawl4ai")
    except InstallError as exc:
        raise RuntimeError(
            f"crawl4ai is not installed and could not be installed automatically: {exc}"
        ) from exc
    if not _crawl4ai_importable():
        raise RuntimeError(
            "crawl4ai is still not importable after the install attempt. "
            "Install it with `pip install crawl4ai` and run `crawl4ai install` "
            "to provision the Chromium browser."
        )


async def _get_crawler():
    """Return the shared ``AsyncWebCrawler``, starting it on first use.

    Guarded by a lock: two concurrent extract() calls must not each build a
    browser.
    """
    global _crawler, _crawler_started  # noqa: PLW0603

    async with _crawler_lock:
        crawler = _crawler
        if crawler is None:
            from crawl4ai import AsyncWebCrawler

            crawler = AsyncWebCrawler()
            _crawler = crawler

        if not _crawler_started:
            await crawler.start()
            _crawler_started = True

        return crawler


class Crawl4aiWebSearchProvider(WebSearchProvider):
    """Extract page content locally via crawl4ai + Playwright."""

    @property
    def name(self) -> str:
        return "crawl4ai"

    @property
    def display_name(self) -> str:
        return "Crawl4AI (local browser)"

    def is_available(self) -> bool:
        # Must NOT install or touch the network — this runs on every
        # `hermes tools` paint and at tool-registration time.
        return _crawl4ai_importable()

    def supports_search(self) -> bool:
        return False

    def supports_extract(self) -> bool:
        return True

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "local · free",
            "tag": (
                "Local browser-based extraction via crawl4ai + Playwright. Renders "
                "JavaScript-heavy pages and returns clean markdown. No API key, no "
                "network egress. Requires the crawl4ai package and a one-time "
                "`crawl4ai install` to provision Chromium. Extract-only — pair with "
                "any search backend."
            ),
            "env_vars": [],
        }

    async def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        """Render each URL in the shared browser and return normalized entries.

        Returns a bare list (one entry per requested URL, in order) — the same
        contract the bundled tavily/firecrawl providers use. Per-URL failures
        become an ``error`` entry rather than raising, so one bad page never
        discards the whole batch.
        """
        try:
            _ensure_crawl4ai()
        except Exception as exc:  # noqa: BLE001 — report as a per-URL failure
            logger.warning("crawl4ai unavailable: %s", exc)
            return [
                {"url": u, "title": "", "content": "", "error": str(exc)} for u in urls
            ]

        crawler = await _get_crawler()
        results: List[Dict[str, Any]] = []

        for url in urls:
            try:
                result = await asyncio.wait_for(
                    crawler.arun(url=url), timeout=_EXTRACT_TIMEOUT_SECS
                )

                content = getattr(result, "markdown", "") or ""
                metadata = getattr(result, "metadata", {}) or {}
                title = metadata.get("title") if isinstance(metadata, dict) else ""
                if not title:
                    title = getattr(result, "title", "") or ""

                results.append(
                    {
                        "url": url,
                        "title": title,
                        "content": content,
                        # `raw_content` mirrors `content` for the legacy extract
                        # pipeline (see the response contract in the plugin docs).
                        "raw_content": content,
                        "metadata": {"source": "crawl4ai", "sourceURL": url},
                    }
                )

            except asyncio.TimeoutError:
                logger.warning("crawl4ai extraction timed out for %s", url)
                results.append(
                    {
                        "url": url,
                        "title": "",
                        "content": "",
                        "error": (
                            f"crawl4ai extraction timed out after "
                            f"{_EXTRACT_TIMEOUT_SECS}s"
                        ),
                    }
                )
            except Exception as exc:  # noqa: BLE001 — crawl4ai raises its own
                logger.warning("crawl4ai extraction failed for %s: %s", url, exc)
                results.append(
                    {"url": url, "title": "", "content": "", "error": str(exc)}
                )

        return results
