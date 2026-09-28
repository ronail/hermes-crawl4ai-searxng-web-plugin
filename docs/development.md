# Development

Contributor notes for `crawl4ai-searxng`. For installation and usage, see the
[README](../README.md).

## Setup

```bash
git clone https://github.com/ronail/hermes-crawl4ai-searxng-web-plugin.git
cd hermes-crawl4ai-searxng-web-plugin
pip install . pytest pyyaml
```

## Tests

```bash
python -m pytest tests/
```

The suite runs **without crawl4ai and without a SearXNG checkout installed**.
It stubs the HTTP and lifecycle boundaries and asserts contracts: capability
flags, response shape, the no-network availability probe, per-URL error
isolation. That is deliberate — the plugin must load and degrade cleanly when
its optional dependencies are absent, so a test run that installed them would
mask exactly the regressions worth catching.

`tests/conftest.py` also installs a minimal `agent` package carrying just the
`WebSearchProvider` ABC, because both providers subclass it and it only exists
inside a Hermes checkout. Without it the suite cannot even be collected in a
standalone clone — which is how CI found the omission. A real Hermes on
`sys.path` wins; the stub never shadows one.

### Guards worth knowing about

`TestDeclaredDependencies` asserts crawl4ai is declared *nowhere* — not a
dependency, not an extra, not in `pip_dependencies` — and that `httpx` *is*
declared. See [dependency policy](#dependency-policy) for why, and note that
the rule is deliberately narrow: "no crawl4ai", not "no dependencies". A
blanket rule once blocked adding `httpx`, which was itself a real omission
(searxng_provider imports it at call time, and the dev venv hid it).

## Validating

```bash
hermes plugins validate .
```

## Layout

The repository root **is** the plugin directory — `plugin.yaml` and
`__init__.py` sit side by side, which is what `hermes plugins validate`
requires, so there is no subdirectory for an installer to point at.

```
.
├── __init__.py              # register(ctx) — registers both providers
├── plugin.yaml              # manifest: kind: backend
├── crawl4ai_provider.py     # browser-backed extraction
├── searxng_provider.py      # on-demand SearXNG search
├── searxng_lifecycle.py     # subprocess lifecycle (start / idle-stop)
├── pyproject.toml           # deps + hermes_agent.plugins entry point
├── tests/
│   ├── conftest.py          # host stub + package registration
│   └── test_providers.py
└── docs/development.md
```

`pyproject.toml` maps the package name onto the repo root via
`[tool.setuptools.package-dir]`, so the optional pip route ships these same
files rather than a second copy.

## How it works

**crawl4ai** wraps `AsyncWebCrawler` (Playwright/Chromium). The crawler is
cached in a module-level singleton behind an `asyncio.Lock`, so several
`extract()` calls in one agent message share a single warm browser instead of
each starting their own.

**searxng-local** delegates process management to `searxng_lifecycle.py`, which
tracks the SearXNG subprocess with OS-level primitives (`os.kill`,
`os.waitpid`) rather than asyncio futures. That is what lets `stop()` work from
a different event loop than the one that started the process — relevant
because `web_search` dispatches synchronously but can be called from inside a
running loop. The provider's `search()` is sync for the same reason, and
`_run_async` bridges it safely when a loop is already running.

## Dependency policy

**crawl4ai must never appear in `pyproject.toml` or `plugin.yaml`** — not as a
dependency, not as an extra.

It pins `snowballstemmer~=2.2` (`<3`); Hermes core pins
`snowballstemmer==3.1.1` for `python>=3.14`, and PM provisions Python 3.14.
PM resolves the plugin member together with the rest of the candidate set, so
declaring it either way makes `hermes plugins enable` refuse the plugin:

```
crawl4ai-searxng[crawl4ai] depends on crawl4ai>=0.5.0, we can conclude that
hermes-agent and crawl4ai-searxng[crawl4ai] are incompatible
```

This regressed three times in a row — hard dependency, then optional extra,
then opt-in extra — each behind a green suite. `[tool.hermes] opt-in-extras`
does not help: it is only honoured on the `--all-extras` path, while the
default selection reaches plugin extras anyway
(`pm/install.py::_target_selection` → `_feature_policy`).

`httpx` *is* declared. Its tree is conflict-free with core, and
`searxng_provider` imports it at call time.

## Releasing

```bash
python -m build          # wheel + sdist
twine check dist/*
twine upload --repository testpypi dist/*   # rehearse
twine upload dist/*
```

Prefer GitHub Actions trusted publishing over a stored PyPI token. Note that
`hermes plugins install` is the primary distribution route; PyPI is secondary
because a pip-installed copy is not managed by PM and may be dropped on an
environment swap.
