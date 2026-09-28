"""Test bootstrap.

Two things have to be arranged before any test imports a provider.

**1. The repo root is the plugin package.** ``plugin.yaml`` and ``__init__.py``
sit side by side, which is what ``hermes plugins validate`` requires. But the
modules import each other relatively (``from .searxng_lifecycle import ...``),
and that relative import must stay — the plugin loader imports the root as a
package, so an absolute import would break the shipped layout.

So we mirror the loader: register the repo root in ``sys.modules`` under the
distribution's package name. The name matches ``pyproject.toml``'s
``package-dir`` mapping, so tests exercise the same module identity a real
install produces.

**2. The Hermes host is stubbed.** Both providers subclass
``agent.web_search_provider.WebSearchProvider``, which only exists inside a
Hermes checkout. Without it the suite cannot even be collected in a standalone
clone — this is what CI hit:

    ModuleNotFoundError: No module named 'agent'

Rather than clone all of Hermes into CI, we install a minimal ``agent``
package carrying just the ABC the providers subclass. The stub mirrors the
upstream contract (abstract ``name``; ``is_available``; capability flags
defaulting to search-only) so a provider that drifts from the real interface
fails here instead of at install time. If a real Hermes checkout is importable
it wins — nothing here shadows an installed Hermes.
"""

from __future__ import annotations

import abc
import importlib.util
import sys
import types
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parents[1]
_PKG = "crawl4ai_searxng"


def _install_host_stub() -> bool:
    """Provide a minimal ``agent.web_search_provider`` if Hermes is absent.

    Returns True when the stub was installed, False when a real Hermes is
    already importable (in which case we change nothing).
    """
    try:  # a real checkout on sys.path wins — never shadow it
        import agent.web_search_provider  # noqa: F401

        return False
    except ImportError:
        pass

    agent_pkg = types.ModuleType("agent")
    agent_pkg.__path__ = []  # mark as a package so submodule imports work

    base_mod = types.ModuleType("agent.provider_base")

    class ProviderBase(abc.ABC):
        @property
        @abc.abstractmethod
        def name(self) -> str:
            """Stable short identifier used as the provider's config-key value."""

        @property
        def display_name(self) -> str:
            return self.name

        def get_setup_schema(self) -> Dict[str, Any]:
            return {"name": self.display_name, "badge": "", "tag": "", "env_vars": []}

    wsp_mod = types.ModuleType("agent.web_search_provider")

    class WebSearchProvider(ProviderBase):
        @abc.abstractmethod
        def is_available(self) -> bool:
            """True when this provider can service calls. No network."""

        def supports_search(self) -> bool:
            return True

        def is_keyless_available(self) -> bool:
            return False

        def supports_extract(self) -> bool:
            return False

        def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
            raise NotImplementedError(f"{self.name} does not support search")

        def extract(self, urls: List[str], mode: str = "basic") -> Any:
            raise NotImplementedError(f"{self.name} does not support extract")

    base_mod.ProviderBase = ProviderBase
    wsp_mod.WebSearchProvider = WebSearchProvider

    sys.modules["agent"] = agent_pkg
    sys.modules["agent.provider_base"] = base_mod
    sys.modules["agent.web_search_provider"] = wsp_mod
    agent_pkg.provider_base = base_mod
    agent_pkg.web_search_provider = wsp_mod
    return True


_HOST_STUBBED = _install_host_stub()

if _PKG not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        _PKG,
        _ROOT / "__init__.py",
        submodule_search_locations=[str(_ROOT)],
    )
    assert _spec is not None and _spec.loader is not None
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_PKG] = _module
    _spec.loader.exec_module(_module)
