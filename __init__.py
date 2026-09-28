"""Local-first web backends for Hermes.

Two providers, both running entirely on the local machine with no API key and
no hosted service:

- ``crawl4ai`` — browser-backed page extraction (Playwright/Chromium), for
  JavaScript-heavy pages the plain HTTP extractors cannot render.
- ``searxng-local`` — on-demand SearXNG, started on the first search and stopped
  after an idle timeout.

Both are registered through the standard web-provider plugin surface
(``ctx.register_web_search_provider``), so they are configurable exactly like
the bundled backends via ``web.search_backend`` / ``web.extract_backend``.
"""

from __future__ import annotations

__version__ = "1.0.0"

__all__ = ["register", "__version__"]


def register(ctx) -> None:
    """Plugin entry point — called once at load time.

    Registration never raises on an unavailable backend: a provider whose
    dependency is missing still registers, so ``hermes tools`` can list it and
    offer to install. ``is_available()`` is what gates actual dispatch.
    """
    from .crawl4ai_provider import Crawl4aiWebSearchProvider
    from .searxng_provider import SearXNGLocalWebSearchProvider

    ctx.register_web_search_provider(Crawl4aiWebSearchProvider())
    ctx.register_web_search_provider(SearXNGLocalWebSearchProvider())
