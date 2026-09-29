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

### SearXNG lifecycle (`searxng_lifecycle.py`)

A module-level singleton (`get_global_manager`) owns the subprocess so every
provider instance in a session shares one process.

- **Lazy start.** `start()` returns immediately if `is_running`, so the common
  path costs a dict lookup, not a subprocess.
- **Readiness probe.** After spawning, it polls `http://127.0.0.1:<port>/`
  every 0.5s until it answers `< 500`, up to a 45s deadline, then raises.
  A port that accepts TCP is not proof SearXNG is serving, hence the HTTP
  probe rather than a bare connect check.
- **Stale-port reclaim.** `_kill_process_on_port()` uses `lsof` to clear the
  port before starting. A crashed prior session can leave a process whose
  homepage answers while `/search` fails — a bare "is the port open?" check
  would adopt exactly that broken process.
- **Idle stop and atexit.** A `threading.Timer` fires 60s after the last
  `start()`; every call resets it. `atexit` stops it if Hermes exits first.
- **Cross-loop stop.** The process is tracked with `os.kill`/`os.waitpid`, not
  asyncio subprocess Futures, so `stop()` works from a different event loop
  than the one that started it. This matters because `web_search` dispatches
  synchronously and can be called from inside a running loop.

Measured on an M-series laptop, real checkout: cold start **5.99s**, warm reuse
**0.0002s** with a stable PID across three calls.

### crawl4ai provider

Wraps `AsyncWebCrawler` (Playwright/Chromium). The crawler is a module-level
singleton behind an `asyncio.Lock`, so several `extract()` calls in one agent
message share a single warm browser instead of each starting their own.

Failures are isolated per URL: each requested URL produces its own result
entry, so one unreachable page cannot discard the rest of the batch.

### Sync/async split

`web_search` dispatches synchronously, so `searxng-local.search()` is sync even
though the lifecycle is async. It reaches the loop through `_run_async`, which
runs the coroutine on a dedicated worker thread when a loop is already running
in the caller's thread — a bare `asyncio.run()` would raise
`RuntimeError: asyncio.run() cannot be called from a running event loop` there.
`crawl4ai.extract()` is genuinely async, and the dispatcher awaits it.

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

## Multi-profile installs

Hermes keeps **one shared dependency venv** per install. `pm/plugins_state.py::dependency_homes()`
returns the default home plus every *live* profile, and
`pm/workspace.py::enabled_plugin_entries` turns each home's `plugins.enabled`
into workspace members. A plugin carrying a `pyproject.toml` becomes a uv
member keyed by `_member_key(identity)` = `<dirname>-<sha256(identity.resolve())[:16]>`.

Consequences:

- **Same plugin in two profiles = two members with the same `[project] name`.**
  uv fails the lock: `Two workspace members are both named 'crawl4ai-searxng'`.
  Deduplication (`candidate_members`) compares unresolved paths, so it does not
  merge them.
- **Symlinking is worse, not better.** A symlink resolves to the *same* real
  path, so both entries compute the same member key, and the second
  `shutil.copytree` into `plugin-sources/<key>` raises `FileExistsError`
  (`pm/workspace.py:258`).
- **The escape hatch is `python_runtime: external` in `plugin.yaml`**, which
  makes `declaration.is_member` false (`pm/plugin_declarations.py:72`) so the
  plugin never joins the union at all. `pm/workspace.py:268` likewise treats a
  `pyproject.toml` with no `build-system` as *virtual* and renames it to
  `hermes-plugin-<key>`, which also survives a duplicate name.
- **The entry point avoids the problem entirely.** A pip-installed plugin is
  discovered as `source="entrypoint"` with `path=<module>`, never a directory,
  so it is not a workspace member and one distribution serves every profile.
  This plugin already declares `[project.entry-points."hermes_agent.plugins"]`.

Opt-in stays per profile either way: `plugins.enabled` lives in each home's
`config.yaml`, and `pm/workspace.py:297` gates on `names & enabled`.

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
