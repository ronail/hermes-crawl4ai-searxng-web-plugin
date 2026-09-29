# crawl4ai-searxng

**Web access for your Hermes agent. No API key, no account, no per-call bill.**

| Provider | Capability | What it does |
|---|---|---|
| `crawl4ai` | extract | Renders JS-heavy pages in Chromium, returns markdown. Local stand-in for Firecrawl/Tavily. |
| `searxng-local` | search | Runs your own SearXNG. Starts on first search, stops when idle. |

## Why

Without a web backend, an agent is limited to its training cutoff. Web access
lets it check a library's current API, read a bug report, or look up an error
message — with no paid account.

Hermes ships eleven web backends. Six (Brave, Exa, Keenable, Parallel,
Perplexity, Tavily) need an API key and bill per call: ~$5–8 per 1,000
searches, ~$1 per 1,000 pages. An agent loop reading a dozen pages per turn
burns a free tier in an afternoon. Local costs nothing, has no rate limit, and
keeps your queries on your machine.

**Tradeoffs:** slower (a real browser per page), relevance is whatever your
SearXNG's upstream engines return, costs RAM and a checkout to maintain. For
speed, relevance, or bulk scale, use a hosted provider.

## Managed lifecycle

You never start or stop either backend.

SearXNG starts on the first search, ~6s cold. After that it reuses the running
process — ~0.2ms, same PID. It exits 60s after you stop searching, and on
Hermes exit. A stale process from a crashed session is cleared first, so you
never adopt one whose homepage answers but whose search endpoint doesn't.

crawl4ai keeps one Chromium behind a lock, so six URLs in a turn share a warm
browser. One unreachable URL returns one error; the rest still come back.

## Install

```bash
hermes plugins install ronail/hermes-crawl4ai-searxng-web-plugin
hermes plugins enable crawl4ai-searxng
```

Pin a commit, as the Hermes catalog does:

```bash
hermes plugins install ronail/hermes-crawl4ai-searxng-web-plugin --ref <40-char-sha>
```

Search works now. For extraction:

```bash
pip install crawl4ai
python -m playwright install chromium   # one-time
```

crawl4ai is intentionally not a declared dependency or extra: it pins
`snowballstemmer~=2.2`, Hermes core pins `==3.1.1` on the Python 3.14 its
package manager provisions, and the conflict makes `hermes plugins enable`
refuse the whole plugin. Until you install it, extraction reports itself
unavailable and everything else works. [Details](docs/development.md#dependency-policy).

Run the same commands once per profile. This plugin is safe to install
everywhere. [Multiple profiles](#multiple-profiles).

`pip install` also works and the `hermes_agent.plugins` entry point makes the
package visible to every profile — but only in the interpreter the CLI itself
runs in, which is not a virtualenv you pip into by hand.
`hermes plugins install` is the route that keeps working.

## Configure

```yaml
# ~/.hermes/config.yaml
web:
  search_backend: "searxng-local"
  extract_backend: "crawl4ai"
```

Unset, Hermes auto-detects. Both can coexist. `hermes tools` opens a picker.

**SearXNG:** set `$SEARXNG_DIR` to a checkout (default
`~/Projects/searxng`) holding `searx/settings_hermes.yml`. No service to
install, no port to reserve, no venv to activate — the manager runs the
checkout's own interpreter. See the
[SearXNG guide](https://docs.searxng.org/admin/installation-searxng.html).

## Multiple profiles

Install it in as many profiles as you like. Each profile has its own plugin
directory and its own `plugins.enabled`, so install and enable once per profile:

```bash
hermes plugins install ronail/hermes-crawl4ai-searxng-web-plugin   # default home
hermes -p dev plugins install ronail/hermes-crawl4ai-searxng-web-plugin
hermes plugins enable crawl4ai-searxng
hermes -p dev plugins enable crawl4ai-searxng
```

This works because `plugin.yaml` declares `python_runtime: external` — the
plugin manages no dependencies of its own, so Hermes leaves it out of the
shared venv entirely. Check any profile with `hermes -p <name> plugins list`.

Why it works: Hermes keeps **one shared dependency venv**, unioned over the
default home and every live profile. A plugin that *does* declare dependencies
becomes a uv workspace member keyed by its install path, so two copies would
declare one name twice and enable would fail with `Two workspace members are
both named crawl4ai-searxng`. A plugin owning no dependencies never joins that
union, so any number of profiles can hold it. Do not symlink one install into
another profile's directory — the member key hashes the *resolved* path, so
both would compute one key and the second copy fails with `FileExistsError`.
[Details](docs/development.md#multi-profile-installs).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `crawl4ai` never available | `pip install crawl4ai && python -m playwright install chromium` |
| `enable` reports a `snowballstemmer` conflict | Version declares crawl4ai — update to current |
| `enable` reports two members named `crawl4ai-searxng` | Pre-`python_runtime: external` version — update |
| `SearXNG startup failed: settings not found` | Set `SEARXNG_DIR`, or create `searx/settings_hermes.yml` |
| No providers in `hermes tools` | It installs disabled — `hermes plugins enable crawl4ai-searxng` |
| Vanished after `hermes update` | Was pip-installed into a venv PM does not manage |

## Notes

`is_available()` never installs and never touches the network — it runs on
every `hermes tools` paint, so it only checks whether a package imports or a
settings file exists. Both providers register even when unavailable;
availability gates dispatch, not registration. No credentials.

## Development

[docs/development.md](docs/development.md) — tests, layout, internals, dependency policy.

## License

[MIT](LICENSE)
