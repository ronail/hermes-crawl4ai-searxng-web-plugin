# crawl4ai-searxng

Two local web backends for [Hermes](https://hermes-agent.nousresearch.com) in
one plugin: **crawl4ai** for browser-backed page extraction and **SearXNG** for
on-demand local search. No API keys, no hosted service, no per-request billing —
both run entirely on your machine.

| Provider | Capability | What it does |
|---|---|---|
| `crawl4ai` | extract | Renders JS-heavy pages in headless Chromium and returns clean markdown. A zero-cost local stand-in for Firecrawl/Tavily. |
| `searxng-local` | search | Starts your SearXNG checkout on the first search, keeps it warm across calls, stops it when idle. |

They are ordinary web-provider plugins, so you configure them exactly like the
bundled backends via `web.search_backend` / `web.extract_backend`.

## Install

```bash
hermes plugins install ronail/hermes-crawl4ai-searxng-web-plugin
```

Hermes clones the repo and leaves the plugin disabled until you enable it:

```bash
hermes plugins enable crawl4ai-searxng
```

To pin an exact commit (what the Hermes plugin catalog does):

```bash
hermes plugins install ronail/hermes-crawl4ai-searxng-web-plugin --ref <40-char-sha>
```

That's it for search — `searxng-local` has no third-party dependency.

### Install crawl4ai separately (optional)

**The plugin declares no hard dependencies, on purpose.** `crawl4ai` pins
`snowballstemmer~=2.2` (`<3`) while Hermes core pins `snowballstemmer==3.1.1`
for Python ≥3.14, and Hermes' package manager provisions Python 3.14. Those are
incompatible, so declaring crawl4ai as a dependency makes the package manager
refuse the install outright — taking the working search provider down with the
broken extract one.

So install it out-of-band, only if you want extraction:

```bash
pip install crawl4ai
crawl4ai install     # one-time: downloads the Chromium binary
```

Until then the `crawl4ai` provider still registers and reports itself
unavailable; search and every other tool keep working. If your Hermes
environment can be rebuilt freely, `pip install "crawl4ai-searxng[crawl4ai]"`
pulls both from PyPI once published.

### Alternative: pip

```bash
pip install git+https://github.com/ronail/hermes-crawl4ai-searxng-web-plugin.git
```

This also works, and Hermes discovers it through the `hermes_agent.plugins`
entry point, but it is the *secondary* route: the package manager does not
manage dependencies installed this way, so an environment swap may drop it.
Prefer `hermes plugins install`, which admits the plugin transactionally.

## Configure

```yaml
# ~/.hermes/config.yaml
web:
  search_backend: "searxng-local"
  extract_backend: "crawl4ai"
```

With no `web.backend` set, Hermes auto-detects: whichever provider is available
for the requested capability. Both can coexist — search with SearXNG, extract
with crawl4ai.

Pick the backends any other way you like:

```bash
hermes tools        # interactive picker
```

### SearXNG setup

The manager looks for a checkout at `$SEARXNG_DIR`, falling back to
`~/Projects/searxng`. It expects `searx/settings_hermes.yml` inside that
checkout and uses its `venv/` interpreter when present (POSIX and Windows
layouts both work), otherwise whatever `python3` is on `PATH`.

```bash
export SEARXNG_DIR=~/src/searxng     # if not at ~/Projects/searxng
```

The process is started on the first search, reused for subsequent searches in
the same session, and stopped after 60s idle or on Hermes exit.

See the [SearXNG installation guide](https://docs.searxng.org/admin/installation-searxng.html)
for creating a checkout and its settings file.

## Behaviour worth knowing

- **`is_available()` never installs and never touches the network.** It runs on
  every `hermes tools` paint, so it only checks whether the package imports
  (crawl4ai) or a settings file exists (SearXNG).
- **Both providers register even when unavailable**, so `hermes tools` can list
  them and offer the install. `is_available()` gates dispatch, not registration.
- **A missing dependency degrades per-URL, not per-call.** crawl4ai returns one
  error entry per requested URL rather than discarding the batch, so one bad
  page never costs you the others.
- **No credentials.** `SEARXNG_DIR` is optional and only relocates a checkout.

## Development

```bash
git clone https://github.com/ronail/hermes-crawl4ai-searxng-web-plugin.git
cd hermes-crawl4ai-searxng-web-plugin
pip install pytest
```

Run the tests:

```bash
python -m pytest tests/
```

The suite runs **without crawl4ai or a SearXNG checkout installed** — it stubs
the HTTP and lifecycle boundaries and asserts the contracts (capability flags,
response shape, the no-network availability probe, per-URL error isolation).
That is deliberate: the plugin must load and degrade cleanly when its
dependencies are absent.

Validate the plugin the way Hermes does:

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
└── tests/test_providers.py
```

## How it works

`crawl4ai` wraps crawl4ai's `AsyncWebCrawler` (Playwright/Chromium). The crawler
is cached in a module-level singleton behind an `asyncio.Lock`, so several
`extract()` calls in one agent message share a single warm browser instead of
each starting their own.

`searxng-local` delegates process management to `searxng_lifecycle.py`, which
tracks the SearXNG subprocess with OS-level primitives (`os.kill`,
`os.waitpid`) rather than asyncio futures. That is what lets `stop()` work from
a different event loop than the one that started the process — relevant because
`web_search` dispatches synchronously but can be called from inside a running
loop.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `crawl4ai` never becomes available | It is an optional dependency: `pip install crawl4ai && crawl4ai install`. |
| `hermes plugins enable` reports a `snowballstemmer` conflict | You are on a version that declares crawl4ai as a hard dependency. Update to the current release, where it is an optional extra. |
| `SearXNG startup failed: ... settings not found` | Set `SEARXNG_DIR`, or create `searx/settings_hermes.yml` in your checkout. |
| Neither provider appears in `hermes tools` | Check `hermes plugins list` — the plugin installs disabled; run `hermes plugins enable crawl4ai-searxng`. |
| Plugin vanished after `hermes update` | It was pip-installed rather than added via `hermes plugins install`. |

## License

[MIT](LICENSE)
