"""Test bootstrap.

The repository root *is* the plugin directory: ``plugin.yaml`` and
``__init__.py`` sit side by side, which is what ``hermes plugins validate``
requires. But the modules import each other relatively
(``from .searxng_lifecycle import ...``), and ``searxng_provider`` must keep
that relative import — the plugin loader imports the root as a package, so an
absolute import would break the shipped layout.

So we mirror the loader: register the repo root in ``sys.modules`` under the
distribution's package name before any test imports a provider. The package name
matches ``pyproject.toml``'s ``package-dir`` mapping, so the tests exercise the
same module identity that a real install produces.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_PKG = "crawl4ai_searxng"

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
