<div align="center">

<img src="https://raw.githubusercontent.com/ouli-1242/dhole-mcp/main/assets/cover.png" alt="Dhole — give an AI hands that actually reach the web" width="100%">

<p><strong>Dhole gives AI agents hands that actually reach the web.</strong></p>

<p>Fetch · search · crawl · read PDFs · act on pages · watch for changes<br>
13 tools in one local process · zero API keys · zero models · zero telemetry</p>

<p>
<a href="https://pypi.org/project/dhole-mcp/"><img src="https://img.shields.io/pypi/v/dhole-mcp" alt="PyPI"></a>
<a href="https://pepy.tech/projects/dhole-mcp"><img src="https://static.pepy.tech/personalized-badge/dhole-mcp?period=total&amp;units=INTERNATIONAL_SYSTEM&amp;left_color=BLACK&amp;right_color=GREEN&amp;left_text=downloads" alt="PyPI Downloads"></a>
<img src="https://img.shields.io/pypi/pyversions/dhole-mcp" alt="Python 3.11+">
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT"></a>
</p>

<p>简体中文版：<a href="README.md">README.md</a></p>

</div>

A models can think, but it cannot see today's web. Training data has a cut-off date. Search APIs want a key and a quota. Fetch one page and you may get back a 403. Dhole reconnects that broken wire: one [MCP](https://modelcontextprotocol.io) server, dropped into any host, where the AI picks the tools and you never memorise a command.

## Install

```bash
pip install dhole-mcp   # full tier: browser / PDF / OCR / parsers / reranker, all in one
dhole doctor            # checks it end to end: server up, browser up, one real search, one real fetch
```

Python 3.11+. One thing pip cannot hand you in this step: Chromium itself (a separate ~193MB download that is not in the wheel).

- Do nothing: the first time a page genuinely needs rendering, dhole installs the build it uses.
- Want it pre-downloaded, or a previous attempt stopped halfway: run `python -m patchright install chromium`. It is repeatable and only fills in what is missing.
- If it will not install, nothing else breaks: without a browser dhole degrades to plain HTTP, and degrading is not a failure. `dhole doctor` names the missing piece and prints a copy-paste fix.

Wire it into a host (Claude Desktop / Cursor / Codex / Cherry Studio — field names differ, look for `command`):

```json
{"mcpServers": {"dhole": {"command": "dhole", "args": []}}}
```

No arguments, no keys, no environment variables. Restart the host and just ask.

### Command line only, no host

The CLI and the MCP server ship in the same package: the `pip install dhole-mcp` above already puts the `dhole` command on your PATH — skip the `mcpServers` block and there is no host to start:

```bash
dhole read https://example.com
dhole search "python asyncio"
```

To keep it as an isolated global tool instead of mixing it into a project environment, install the same package with pipx or uv (the entry point is still `dhole`):

```bash
pipx install dhole-mcp        # or: uv tool install dhole-mcp
dhole doctor
```

Every CLI call is its own process: nothing stays resident, no port is opened, no host is needed. The on-demand browser / PDF / OCR notes above apply unchanged. Upgrade with `pip install -U dhole-mcp` (`pipx upgrade dhole-mcp` / `uv tool upgrade dhole-mcp` for the isolated installs).

<details>
<summary>Or let the agent install it itself</summary>

```
Install the Dhole MCP server on this machine. Go step by step; stop and report on failure.

1. Identify which host you are in. Read its MCP config location and the format it expects
   for a local server. Read the docs — do not guess.
2. pip install dhole-mcp (this is already the full tier).
3. Back up the config file, then add a server named "dhole": command "dhole",
   no args, no keys, no env.
4. Ask the user to restart the host, then run dhole doctor to confirm the check passes.
   If it reports a missing browser, run python -m patchright install chromium (repeatable).
```

</details>

For a long-lived or remote setup, switch to streamable HTTP:

```bash
export DHOLE_HTTP_TOKEN="$(openssl rand -hex 32)"   # set it and Bearer checks become mandatory
dhole --http                                        # 127.0.0.1:8765/mcp
```

Put a TLS reverse proxy in front before exposing it anywhere. Dhole has no built-in OAuth: the token is a port gate, not identity isolation. Need per-person isolation — one process per person.

## Demo

From a model that cannot see today's web to one that fetches its own evidence.

<video controls width="100%" poster="https://raw.githubusercontent.com/ouli-1242/dhole-mcp/main/assets/consent.png" src="https://github.com/user-attachments/assets/b59a7b13-fab2-41df-a029-cbc41d8a378e"></video>

Player refuses to start (a login state occasionally gets caught by the browser's CSP)? [Same file, direct link](https://github.com/user-attachments/assets/b59a7b13-fab2-41df-a029-cbc41d8a378e). (720p, 86 seconds, narrated.)

## What it does

All 13 tools register by default. Install it and everything is already reachable — no capability is hiding behind an extra switch.

| Tool | What it does |
|---|---|
| `web_read` | Fetches any URL: the anti-bot ladder, PDF / OCR, batching, focused extraction; returns text, **bytes** (`save_to`) or **evidence** (status line / headers / the 302 chain); `check` answers "did this page change" and attaches a unified diff |
| `web_act` | Works on the page: click, fill, **select a dropdown**, press keys, drag, scroll, screenshot, **upload files**, **answer dialogs**, **multi-tab**, **back through history**, **print to PDF**; a submission pauses by default until you say yes |
| `web_request` | Sends one non-GET (POST / PUT / PATCH / DELETE / HEAD), exactly once: no retry, no cache, no browser escalation |
| `web_search` | 12 keyless engines in parallel + a local reranker, optionally fetching the full text of the top three; plus seven vertical sources (news / papers (arXiv + Crossref) / Q&A / code / community / package metadata / GitHub repositories) |
| `web_research` | One question, one call, one evidence pack with `[n] URL` citations; a source that fails explains why instead of dragging the pack down. `cross_check=true` adds a source-agreement report (claims, supporting/opposing domains, `agreement_score`) — an agreement signal, not a fact-check |
| `web_recall` | Searches **the pages this machine already read**: zero network, zero model; returns pointers, excerpts and staleness flags |
| `web_crawl` | Same-site crawling: sitemaps, keyword filters, path subtrees |
| `web_discover` | A map of the site's outbound URLs — see what is there before choosing what to read |
| `doc_parse` | Local docx / xlsx / pdf / pptx / odt / csv / html / json / yaml → Markdown |
| `feed_read` | Batch RSS / Atom, `since=` for incremental polls only |
| `cred_defaults` | An identity set once: proxy, browser profile, CDP endpoint, cookies, auth |
| `sys_state` | Inspect and clear local state: response cache, session cookies, engine cooldowns |
| `sys_guide` | Dhole's own handbook: which tool to pick, how to write the call, what each field means, how to fix a failure |

Once installed, just talk:

- "Get the prices off this page and put them in a table" → `web_read`
- "Give me a cited answer to this question" → `web_research`
- "Read everything under `/guide`" → `web_crawl`
- "Bring back that article on attention mechanisms I read earlier" → `web_recall`
- "Delete this record in the admin panel" → `web_act`
- "Tell me when this page changes" → `dhole watch`

## How it differs from other fetchers

- **Zero keys**: 12 keyless search engines, seven vertical sources, the anti-bot ladder and the reranker — none of them needs an account or a top-up. The optional `tavily` / `bocha` / `exa` / `deepseek` / `firecrawl` / `brave_api` backends are called only when you configured a key **and** name them.
- **Zero models**: judgement belongs to the outer agent; dhole stays a deterministic tool layer. The same page fetched twice gives the same result. No small model quietly decides "this page is probably about X".
- **Fully local**: the response cache, cookies and reranker live in `~/.dhole`. Apart from the sites you ask it to fetch, the process sends nothing anywhere.
- **Honest failures**: every call answers with the same JSON — how many characters were extracted, which tier ran, what to call next. An empty extraction says it was empty; a grey anti-bot page is never packaged as "content".

## Cost comparison against peer MCPs (measured 2026-10-26)

**Methodology first**: connect cost = the server instructions sent at `initialize` + the compact JSON of `tools/list` in characters (without prompt caching this is re-paid on every model request; with caching it costs roughly a tenth). Each product was measured against the npm/PyPI latest of that day. Dhole is converted with its own measured 4.16 characters/token (use a flat 4.0 and every figure scales equally — the ranking does not change). **All cross-product multiples are character ratios.** The dhole row can be re-run any time with `python tests/tool_payload_measure.py`; the competitor rows are pinned to the 2026-10-26 version numbers and are not re-measured per release.

| MCP | Tools | connect compact chars | ≈tokens | Billing |
|---|---|---|---|---|
| Brave Search | 8 | 110,719 | ~27.7k | API key, metered |
| Firecrawl | 28 | 94,937 | ~23.7k | credits |
| Playwright (all caps) | 72 | 45,602 | ~11.4k | free |
| **dhole-mcp** | **13** | **33,612** (30,885 tools + 2,727 instructions) | **~8.1k** | free, keyless |
| Playwright (default) | 25 | 20,286 | ~5.1k | free |
| Tavily | 5 | 7,694 | ~1.9k | API key, metered |
| Exa (hosted, tools/list only) | 2 | 2,419 | ~0.6k | keyless, rate-limited |
| Fetch (official reference) | 1 | 1,150 | ~0.3k | free |

- Fourth cheapest of seven products (third if Exa is excluded; fifth if Playwright's two tiers are counted separately) — **2.9–3.3× cheaper** than the two most popular options, Brave and Firecrawl (character ratios of 3.35 and 2.87).
- **Fewer tools does not mean a cheaper description**: Brave's 8 tools cost 27.7k (~3.5k per tool); dhole's 13 average ~0.6k each and cover fetch / search / crawl / act / research / feed / PDF / recall / local documents.
- Those 2,727 characters (~656 tokens) of instructions are the only usage guide paid at connect time. Deep documentation never enters the context — whoever asks pays for it (`sys_guide`, or `dhole skill install`). Trimming to `DHOLE_TOOLS=web_read,web_search` brings the connect cost down to 11,664 characters (~2.8k).

**Variable cost** — one call against the same dense documentation page (`docs.python.org/3/library/asyncio-task.html`), measuring how much comes back:

| Approach | chars returned | ≈tokens |
|---|---|---|
| dhole `web_read` @`max_content_chars=8000` | 9,839 | 2.5k |
| dhole `web_read` + `focus=` | 23,131 | 5.8k |
| dhole `web_read` default | 41,860 (truncated + `next_offset` to continue) | 10.5k |
| Tavily `tavily_extract` default | 62,523 | 15.6k |
| Firecrawl `scrape` default | 80,824 | 20.2k |
| Playwright `browser_snapshot` | 137,246 | 34.3k |

Same search term (`model context protocol`): Tavily basic 4,605 chars (the leanest) · dhole `web_search` 7,916 (the fastest, 3.6s; rows carry `relevance` and `engines_consensus`) · Firecrawl summaries 11,201 · Tavily advanced+raw 60,486 · Firecrawl with inline markdown 152,696. Same research question in one call: dhole `web_research` 10,130 chars / a 3-source evidence pack, Tavily `research` 10,014 chars / 30.5s / a written report — **a tie on a single sample**, in different shapes (citable evidence vs a finished answer), and the reruns vary enough that this is not a stable conclusion.

**The parts we are not polishing over**: truncation and pagination are not unique to dhole (the official Fetch has `max_length` / `start_index`; Tavily has `query` re-ranking, Firecrawl has `queryOptions` and `includeTags`/`excludeTags`). What is unique is the combination — free + keyless + truncation/pagination + lexical focus. On pure token count Tavily basic's search rows really are cheaper than dhole's. Playwright's snapshot is the whole accessibility tree and usually has to be retaken after every action, but what it buys is automation depth dhole does not have.

**When dhole is the wrong tool**: front-end E2E, network interception/mocks, recorded evidence → Playwright. Managed large-scale crawling and scheduled monitoring → Firecrawl. An SLA or an enterprise index → Tavily / Brave / Exa. You just need one URL → the official Fetch.

## Configuration

Two doors: the `env` block in the host config (needs a restart), or `dhole config set NAME value`, which writes to `~/.dhole/config.json` and takes effect on the next call. **env always wins.** The defaults are usually what you want — if you are unsure, set nothing.

<details>
<summary>All 21 knobs</summary>

| Variable | When to set it |
|---|---|
| `DHOLE_SEARCH_PROXY` | When the search engines are rate-limited or blocked (also reads `HTTPS_PROXY` automatically) |
| `DHOLE_IGNORE_ROBOTS` | Turns off process-wide `robots.txt` compliance; for a single call use `ignore_robots=true` |
| `DHOLE_ALLOW_PRIVATE_HOSTS` | Allows private / loopback addresses, which are refused by default |
| `DHOLE_HTTP_TOKEN` | The bearer gate for the `--http` transport; unset means an open port |
| `DHOLE_PERSIST_CREDENTIALS` | Writes session cookies and credentials to disk (DPAPI-encrypted on Windows). Default: memory only, cleared on exit |
| `DHOLE_USAGE_LOG` | Local log of calls and writes; never records argument values, never phones home |
| `DHOLE_QUERY_LOG` | Opt-in local record of the queries you actually searched with (mode / result count only, no results, URLs or snippets, no network) for real-query evaluation |
| `DHOLE_TOOLS` | Registers only the listed tools — **naming any disables the rest** |
| `DHOLE_DEFAULT_ENGINES` | Overrides the default search pool |
| `DHOLE_SEARCH_ROW_FILTER` | `off` disables the SEO-farm row filter (on by default) |
| `DHOLE_DEFAULT_CONTENT_CHARS` | Default content budget, 40000 (500–200000) |
| `DHOLE_TAVILY_API_KEY` / `DHOLE_BOCHA_API_KEY` / `DHOLE_EXA_API_KEY` / `DHOLE_DEEPSEEK_API_KEY` / `DHOLE_FIRECRAWL_API_KEY` / `DHOLE_BRAVE_API_KEY` | Keys for the six keyed engines; enabled only if configured, used only if named |
| `DHOLE_HF_ENDPOINT` | Reranker download source; defaults to huggingface.co with a mirror fallback |
| `DHOLE_RERANK_MODEL` | `bge-zh` (default, stronger for Chinese) or `mmarco` (the fast tier, roughly 1.8×) |
| `DHOLE_HOME` | State directory, defaults to `~/.dhole` |

</details>

<details>
<summary>Search engines and reranking</summary>

The default pool is 12 keyless engines and **the pool order is a trust tier** (the reliably reachable workhorses first, then the ones that hit per-IP limits / anti-bot / regional blocks in the middle, small niche indexes with shallow coverage last; the merged ranking is layered by the same table, so the workhorses speak first): `baidu` / `bing` / `bing_global` / `duckduckgo` (`ddg`) / `yahoo` / `brave` / `sogou` / `so360` (`360`) / `shenma` / `yandex` / `seznam` / `marginalia`. `engines=` can only narrow the pool, never add to it. There are 9 index families: `yahoo` belongs to Bing along with `bing` / `bing_global` / `duckduckgo` (more entry points, not more families); `shenma` is Alibaba's mobile-owned index (Quark runs on the same infrastructure); `seznam` is a Czech portal's own index; `marginalia` indexes the independent small web. Five engines work from a domestic China connection without a proxy: `baidu` / `bing` / `sogou` / `so360` / `shenma`. Six keyed backends exist alongside them — `tavily` / `bocha` / `exa` / `deepseek` / `firecrawl` / `brave_api` (`brave_api` is the paid stable entry for the same index as the keyless `brave`).

Reranking uses a local cross-encoder, downloaded on first use (`bge-zh` ~279MB / `mmarco` ~119MB); switching tiers needs a host restart. To finish the download before the first search, run `dhole models fetch` (optionally `--model mmarco`; progress on stderr, a cut download resumes on the next run, and the finished file is checked against the publisher sha256); the search path itself never waits for it — with no model it buckets and sorts by query-term coverage and never drops a row. Public benchmarks on a frozen corpus: `python evals/vertical_search_baseline.py` and `python evals/ranking_baseline.py --tiers` — method and both samples in [evals/README.md](evals/README.md).

**We do not promise an SLA for keyless public APIs.** What we promise is that failures land in the report honestly and never get dressed up as "no results".

</details>

<details>
<summary>Context cost</summary>

Full connect overhead is 33,612 characters (~8.1k tokens across 13 tools, measured). Trimmed by usage: read / search / request / crawl / documents / feeds / state / guide — those 8 tools — are 23,727 characters, and 9 tools with `web_discover` are 25,861; a trimmed tool leaves the wire together with its own routing line. Do not pick "the smallest N tools" by size alone — that is how you end up excluding `web_read` and `web_act`.

</details>

## Boundaries

- **`robots.txt` is honoured by default**: a `Disallow`ed URL gets zero requests. Going around it is `DHOLE_IGNORE_ROBOTS` or a single-call `ignore_robots=true` — that is your compliance decision, and the target site's ToS and local law are the user's responsibility.
- **`content_ok=true` does not mean the content is true**: it means 2xx, no error, non-empty body. A few hundred characters of "success" is usually a login wall.
- **Writes are sent exactly once**: `POST` / `PUT` / `DELETE` are not retried, not cached, not escalated to a browser. A timeout's answer is "unknown", not "it failed".
- **Submissions pause by default**: `web_act`'s `submit` does nothing without `confirm=true`; a CAPTCHA gets at most 3 attempts before it is handed over, and 2FA and slider puzzles are always handed over.
- **Credentials never cross the line**: cookies and `Authorization` go only to the origin, and never survive a cross-origin redirect; a credential without a host is refused outright; private and loopback addresses are refused by default, and cloud metadata endpoints are never allowed.
- **Fetched page text is untrusted data**: instructions inside it are treated as prompt injection, and a suspicious response is marked `untrusted_content`. The mark is not a block — the verdict belongs to the outer agent.

## Commands you will actually use

| Command | What it is for |
|---|---|
| `dhole read <url> [url2 ...]` | Fetch without an MCP host: stdout is the page text (`--max-chars` / `--offset` / `--timeout S`) |
| `dhole search <query>` | Keyword search: one ranked row per result on stdout (`--max-results N` / `--site` / `--freshness`; `--sources package,repo,academic,…` for the seven vertical sources, packages and repositories by exact name) |
| `dhole crawl <url>` | Crawl one site: one section per page on stdout (`--max-pages N` / `--max-depth N`) |
| `dhole discover <url>` | Site map: one URL per line on stdout (`--sitemap`) |
| `dhole research <query>` | A cited evidence pack on stdout, with `[n]` + URL per block (`--max-sources N`; `--cross-check` adds a source-agreement report) |
| `dhole recall <query>` | Find pages this machine already read: URL + snippet (local cache only) |
| `dhole request <url> --method ...` | One non-GET request (writes need `--confirm`; `--body-file` keeps secrets out of shell history) |
| `dhole act <url> --actions ...` | Run an actions chain (JSON / `@file` / `-`); each action's receipt goes to stderr |
| `dhole doc <file>` | Local file as markdown (pdf / docx / xlsx / csv / pptx / odt …) |
| `dhole feed <url> [url2 ...]` | RSS/Atom entries, one section per feed (`--since` polls incrementally) |
| `dhole state` | Local-state census; `--show cache,models`, `--clear cache`, `--clear sessions --confirm` |
| `dhole models fetch [--model NAME]` | Download the reranker in full: progress on stderr, a cut download resumes, publisher sha256 verified (`--model bge-zh\|mmarco`, default follows `DHOLE_RERANK_MODEL`) |
| `dhole creds [show\|set\|unset]` | The redacted identity store (`set` reads JSON / `@file` / stdin) |
| `dhole guide [topic] [section]` | Read dhole's own handbook |
| `dhole call <tool> [--arg ...]` | Generic entry for any MCP tool (`--args` takes JSON; JSON by default, `--text` prints the content channel) |
| `dhole tools [--json]` | The live parameter surface of all 13 tools (`call` accepts these names) |
| `dhole help [command]` / `dhole <command> --help` | Full help: every flag, example and exit code |
| `dhole commands [--json]` | Machine-readable command catalog (flags, positionals, tool mapping) |
| `dhole completion bash\|zsh\|fish\|powershell` | Print the completion script for that shell |
| `dhole doctor [--json]` | Install health check, with a fix command per failing item |
| `dhole config list` / `get` / `set` / `unset` | Change knobs, see the current value and where it came from (all accept `--json`) |
| `dhole engines list` / `probe` / `reset` | Engine cooldown state, measured health, clear it (all accept `--json`) |
| `dhole watch add` / `list` / `run` | Page-change watchlist; `run` exits 0 unchanged / 1 changed / 2 check failed, `--notify` raises a desktop notification |
| `dhole skill status` / `install` | Installs the packaged handbook as a native skill (defaults to `~/.agents/skills`; `--target DIR` for a host's own root) |
| `dhole wire ledger` / `lock` | Connect-face cost ledger and its lock (`--json`) |
| `dhole version` / `dhole -v` | Version plus the capability panel (`version --json` is the machine form) |

All 13 content commands mirror the 13 MCP tools: stdout carries content, stderr carries metadata, so both pipe cleanly. Exit codes: 0 = success with content, 1 = fetched but not usable (`content_ok=false` or a non-empty `error`), 2 = usage error. With `--json`, stdout is the full receipt (the same bytes MCP carries unless `--pretty` is used); `dhole guide` is prose, so there `--json` wraps the whole document as one JSON string. Any tool argument not listed on the command goes through `--arg NAME=VALUE` (JSON is parsed as JSON, e.g. `--arg include_links=true`).

**Output and automation**: `--ndjson` turns a batch read, crawl, search, feed, research or local recall into one JSON object per line (each carries a `type`; a failed vertical source is a `source_error` row, while research emits `evidence` / `source_failed` / `cross_check` rows); `--pretty` indents JSON; `--quiet` drops stderr metadata (errors still print); `--verbose` adds summaries and the next hop; `--progress` shows elapsed time on a TTY; `--no-color` / `--color=auto|always|never` control colour; `--dry-run` prints the bound call as `{dry_run, tool, args}` and exits 0 without sending a request, so you can see how flags map onto tool parameters before spending one. Global flags work before or after the command (`dhole --json read URL`). `--urls-file FILE` or a positional `-` reads a URL list from a file / stdin for batch reads and `feed`. Under `--json`, usage errors emit `{"error": "...", "exit_code": 2}` instead of prose only.

Vertical sources and cross-check report to stderr as well: `dhole search --sources package` names each backend's `empty` / `blocked` status plus `requested/delivered`, and tags rows with backend and publish date; `dhole research --cross-check` prints claim verdicts, `cross_check_basis`, `agreement` and warnings (an agreement signal, not a fact-check). When the reranker is not in place yet, the CLI takes the same cold-start path as MCP: this round is ranked by cross-engine consensus and stderr says why; `dhole state --show models --verbose` shows download state.

`dhole call` is the escape hatch for the argument surface: named flags stay preferred; use it when a tool argument has no flag yet. For example, `dhole call web_read --arg url=https://example.com --arg nested_max=3`.

**Every CLI call is its own process**: session cookies and `cred_defaults` live in memory by default, so using them across commands needs `DHOLE_PERSIST_CREDENTIALS=1` (`dhole creds set/unset` refuses outright when it is off, rather than pretending to write). Writes in `dhole request` / `dhole act` still go through the tools' own `confirm` gates.

Monitoring is polling, not push: the schedule is your cron / systemd timer. There is no webhook egress — that is a decision, not a gap: dhole is a read-only local tool layer and will not become a process that POSTs page content somewhere else.

## Local data

Everything lives in `~/.dhole/` (`DHOLE_HOME` relocates it; on POSIX directories are 0700 and files 0600). The response cache `cache.db` is a plaintext SQLite with a 1-hour TTL (6h for articles) — that is exactly what `web_recall` searches. Sessions and credentials are memory-only by default; `profiles/` holds persistent browser profiles, **including login state** — delete the directory and you are logged out. There is no telemetry at all.

## Documentation

- [README.md](README.md) — 简体中文版（完整手册）
- [CONTRIBUTING.md](CONTRIBUTING.md) — dev environment, tests, engine contract
- [evals/README.md](evals/README.md) — benchmark method and readings
- [CHANGELOG.md](CHANGELOG.md) — what each release changed, and why
- The handbook ships through two doors over one corpus: `sys_guide(topic=...)` or `dhole skill install`

## Acknowledgement

This project is a derivative of [dondai1234/master-fetch](https://github.com/dondai1234/master-fetch), published upstream under the MIT licence; the original copyright text is preserved in full in [LICENSE](LICENSE).

## License

[MIT](LICENSE)
