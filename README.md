# crawl4ai-searxng

**Web access for your Hermes agent, with no API key, no account, and no
per-call bill.**

| Provider | Capability | What it does |
|---|---|---|
| `crawl4ai` | extract | Renders JS-heavy pages in headless Chromium, returns clean markdown. A local stand-in for Firecrawl/Tavily. |
| `searxng-local` | search | Starts your SearXNG on first search, keeps it warm, stops it when idle. |

## Why

Without a search or extract backend, an agent is limited to its training
cutoff plus whatever tools you attach yourself. Web access is what lets it
check a library's current API, read a bug report, or look up an error
message. This makes that work without a paid account.

Hermes ships eleven web backends; six (Brave, Exa, Keenable, Parallel,
Perplexity, Tavily) need an API key and bill per call — roughly $5–8 per
1,000 searches and ~$1 per 1,000 pages. An agent loop reading a dozen pages
per turn exhausts a free tier in an afternoon. Locally it costs nothing, has
no rate limit to throttle against, and nothing leaves your machine.

**Tradeoffs:** slower (a real browser per page), search relevance is whatever
your SearXNG's upstream engines return, costs RAM and a checkout to maintain,
and upstream page changes can break extraction. For speed, best-in-class
relevance, or bulk scale, the hosted providers are the better tool.

## Install

```bash
hermes plugins install ronail/hermes-crawl4ai-searxng-web-plugin
hermes plugins enable crawl4ai-searxng
```

That is all search needs. For extraction, install crawl4ai out-of-band:

```bash
pip install crawl4ai
python -m playwright install chromium   # one-time: downloads the browser
```

crawl4ai is deliberately not declared as a dependency or extra — it pins
`snowballstemmer~=2.2` while Hermes core pins `==3.1.1` on the Python 3.14
its package manager provisions, which makes `hermes plugins enable` refuse the
whole plugin. [Why, in detail](docs/development.md#dependency-policy).

Until you install it, `crawl4ai` still registers and reports itself
unavailable; search and everything else keep working.

Pin a commit the way the Hermes catalog does:

```bash
hermes plugins install ronail/hermes-crawl4ai-searxng-web-plugin --ref <40-char-sha>
```

## Configure

```yaml
# ~/.hermes/config.yaml
web:
  search_backend: "searxng-local"
  extract_backend: "crawl4ai"
```

With nothing set, Hermes auto-detects whichever provider is available. Both
can coexist. `hermes tools` opens an interactive picker.

**SearXNG:** the manager looks for a checkout at `$SEARXNG_DIR`, falling back
to `~/Projects/searxng`, and expects `searx/settings_hermes.yml` inside it. It
uses the checkout's own venv when present, else `python3` from `PATH`. Started
on the first search, stopped after 60s idle or on exit. See the
[SearXNG install guide](https://docs.searxng.org/admin/installation-searxng.html).

## Notes

- `is_available()` never installs and never touches the network — it runs on
  every `hermes tools` paint, so it only checks whether a package imports or
  a settings file exists.
- Both providers register even when unavailable, so `hermes tools` can list
  them. Availability gates dispatch, not registration.
- A crawl4ai failure degrades per-URL: you get one error entry per bad page
  rather than losing the whole batch.
- No credentials. `SEARXNG_DIR` is optional and only relocates a checkout.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `crawl4ai` never becomes available | `pip install crawl4ai && python -m playwright install chromium` |
| `hermes plugins enable` reports a `snowballstemmer` conflict | You're on a version that declares crawl4ai; update to current |
| `SearXNG startup failed: ... settings not found` | Set `SEARXNG_DIR`, or create `searx/settings_hermes.yml` |
| Neither provider in `hermes tools` | `hermes plugins list` — it installs disabled; enable it |
| Plugin vanished after `hermes update` | It was pip-installed, not added via `hermes plugins install` |

## Development

[docs/development.md](docs/development.md) — tests, layout, internals, and the
dependency policy.

## License

[MIT](LICENSE)
