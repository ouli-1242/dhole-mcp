# Configuration Guide

大众化一刀切：`pip install dhole-mcp` 接上宿主就能用，**能力全部默认在线**。所以可配置的东西只剩很少的一批 `DHOLE_*` 变量 —— 每一个都是"这台机器的事实"（代理、内网、密钥、状态目录），而不是"要不要开某个能力"。这里不抄总数（抄一次就会烂一次）：`dhole --doctor` 报当前注册表里有几颗、你设了几颗，`dhole config list` 连着值的来源一起列。所有变量可选；零配置即默认全量行为。

## The rules of changing things

- **Single-call behavior** → call parameters (`ignore_robots` / `cache_ttl` / `focus_engine` / `anti_detect` …). No config edit, no restart. Prefer this whenever it suffices
- **Per-site defaults, the new third door** → `cred_defaults` (a tool, not a config file): proxy, browser profile, CDP endpoint, private-host waiver, anti-detect rung, and host-scoped `auth` / `cookies`. Held in memory, read back by every later call, shown redacted, revoked with `unset`; `DHOLE_PERSIST_CREDENTIALS=1` moves them to `~/.dhole/identity.db` (values DPAPI-encrypted). No restart, and no credential copied into the conversation each time
- **Process-wide behavior, two doors.** `dhole config set DHOLE_DEFAULT_ENGINES baidu,bing` writes `~/.dhole/config.json`, which is re-read when the file changes — knobs read per call take effect on the next call, no restart. `dhole config list` shows what the file sets, whether env shadows it, and which values are secret without printing them. The host's `env` block still works and **always wins** (an operator's existing env configuration must never be silently overridden by a file); editing it needs a host restart
- **Process-wide, the old way** → environment variables, in the host's MCP config under the `dhole` server's `env` block, **then restart the host**:

```json
{
  "mcpServers": {
    "dhole": {
      "command": "dhole",
      "env": {
        "DHOLE_SEARCH_PROXY": "http://127.0.0.1:7890"
      }
    }
  }
}
```

- Values are strings; booleans are `"1"` / `"0"`
- Changing env vars edits the user's machine — present options and get consent first
- **Retired knobs are silently ignored.** The old on/off switches were deleted (wire shape, tool groups, multimodal, neural-focus license, cooldown tuning, self-update…); their values are now fixed in code because 能算清的写死. A stale `DHOLE_…=1` in an env block does nothing and is not an error — `dhole --doctor` is the one place that names what a host still sets that no longer does anything

## Variables by scenario

### Scenario: search engines blocked / rate-limited / should use a proxy

- `DHOLE_SEARCH_PROXY` — the single egress every search engine goes through; **also auto-reads** `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` when unset (sandboxes and CI often already have one; `dhole --doctor` names which variable answered). There is no proxy pool — no `dhole proxy` command and no `~/.dhole/search_proxies.json`, so a comma-separated value rotates nothing (first URL wins, the rest are dropped with a warning) and a value with an unrecognised scheme (`http`/`https`/`socks5`/`socks5h` only — usually a missing `http://`) is treated as unset, also with a warning. Credentials in the URL are never echoed back anywhere: doctor, errors and the usage audit all show `http://***@host:3128`
- Division of labor: this variable covers the **search layer only**; fetches go through a proxy via the stored `cred_defaults` `proxy` (credited as an explicit proxy, so it gets the 5s precheck)

### Scenario: Clash / sing-box TUN proxy (fake-IP mode)

- Nothing to set. A fake-IP resolver (every public name answering with an address in `198.18.0.0/15` / `fc00::/7`) is detected by a probe of two independently registered names — both must answer inside those ranges, so one poisoned or hosts-pinned name cannot relax the machine. What it relaxes is exactly those two ranges: the SSRF DNS recheck itself keeps running and still refuses loopback / RFC1918 / link-local / metadata answers, by hostname as well as by literal. The reason travels in `ssrf_recheck` on every affected response — under `metadata` for a single URL, once on the envelope for a `urls=[...]` batch or a crawl (it is a property of the process, so repeating it per page would only repeat it), and `dhole --doctor`'s "resolver reality" row names the address/network. A tunnel that arrives mid-session re-probes itself before it can refuse one of those ranges (once per 60s). There is no manual override (放宽守卫需要证据，不需要猜测)

### Scenario: fetching intranet / local dev services

- `DHOLE_ALLOW_PRIVATE_HOSTS` — comma-separated allowlist (`localhost,my-service.local,192.168.1.50`) bypassing the SSRF guard for those hosts; default empty = reject all. Per-machine door; the tool-side equivalent is `cred_defaults` `set={allow_private: true}` (loopback only) or a host list, which the next call reads back
- That stored waiver is read by **the guard**, not by each tool: every face that passes `validate_url` gets the same answer — `web_read`, the crawl/discover legs (seed, robots.txt, every sitemap entry, each page), `feed_read`, `web_request`'s pre-send check, and `web_search`'s `find_similar` source leg. It is one door, not one door per tool with a list of which tools remember
- **Cloud metadata endpoints (169.254.169.254, metadata.google.internal, …) are never allowed**, allowlist or not
- dhole drops its own synthesized browser header template when the target is loopback/private, and the door covers both legs: on the HTTP tier no fabricated `referer: https://www.google.com/` and no generated UA; on the browser tier no invented referer either - what that Chromium sends of its own (UA, `sec-ch-ua*`, `sec-fetch-*`, and the `pragma: no-cache` / `cache-control: no-cache` pair that its interception layer adds on every browser request - troubleshooting) is untouched, because dropping those makes the request look less like a browser, not more. That template exists to pass walls that do not know dhole; against your own service it is only pollution in your request log. Headers you name still arrive verbatim, and the drop is announced in `summary`. A cache replay carries the same disclosure, in the past tense (`the fetch that stored this answer dropped dhole's own synthesized header template … this replay sent no request`): the page being handed back IS the page that was fetched without those headers, and that sentence is evidence about what the site saw — while the replay itself sent nothing, so it must not be described as though a packet went out now. An answer cached before this receipt was stored says nothing at all: silence here means "not recorded", never "the headers were kept". Public targets are untouched (`anti_detect` rungs behave exactly as documented) — the way to send yours alone everywhere is `anti_detect='off'` / the stored rung
- With a proxy running, loopback traffic may be intercepted by the proxy itself (502 instead of connection-refused) — `NO_PROXY` needs `127.0.0.1,localhost` (see troubleshooting)

### Scenario: pages behind a login wall — CDP attach

One-time user setup: launch a **copy-profile** Chrome with remote debugging (Chrome 136+ refuses debugging on the default profile) and log into the target site in that window once. Edge works the same way - it is the same protocol, so swap the executable for `msedge.exe` and dhole only sees a different port:

```
chrome.exe --user-data-dir=D:\dhole-chrome --remote-debugging-port=9222
```

Then just call — the attach asks once per host, and that answer is good for 30 days:

```json
{"url": "https://portal.example.com/dashboard", "cdp_url": "http://127.0.0.1:9222", "check_login": true}
```

- **先问再记**: attaching = handing dhole read access to every site logged into that Chrome, so an attach **for a target host** (the site you are reading — not the `127.0.0.1` debug endpoint) that has no live approval is refused with `cdp_authorization_required` and **nothing is executed**. "No live approval" has three causes and the refusal names which one it is: never approved, approved more than 30 days ago, or approved before approvals carried a date. Re-call the same call with `confirm=true` to approve that one host; the approval is persisted to `~/.dhole/cdp_hosts.json` **with its timestamp**, and later requests to another origin are fenced: a sub-resource or page script from outside it is aborted, and a page that redirects out of the fence comes back refused rather than as an answer — that redirect is ended inside the browser, before the request leaves it (see the next line). That same fence blocks an authorized page's third-party assets (CDN / analytics / fonts), so a logged-in page can render partially — by design. A pass that ages out is **asked again**, not silently dropped: a working setup turning into a mystery failure is the worse cost. Revoke = `sys_state(clear=['cdp:host=<name>'])` for one host or `clear=['cdp_hosts']` for all (both take `confirm=true`); `show=['cdp_hosts']` lists who is approved right now and `summary`/`next_action` name each host's approval date, its age in days, the roster's **file name** and the revoke command (the full path is machine information, so it is not carried on the reply — `dhole doctor` prints where that file is) — the age is what makes a stale approval discoverable instead of inherited (deleting `cdp_hosts.json` by hand works too)
- Three enforcement points: a CDP request hook on main-document navigations (a redirect to a host off the roster is ended before it leaves the browser), the route level for sub-resources and page-JS requests, and the result boundary — a landing outside the roster is never returned as content. When the request hook cannot be installed the answer is still refused, and that refusal says the request already left the browser; read the sentence you got instead of assuming the stronger one. **The match is an EXACT hostname**, not a domain or a suffix: `example.com` does not cover `www.example.com`, and a page that loads scripts from a CDN needs that CDN confirmed too. No wildcards
- The response marks `cdp_attached: true`; authorize/refuse events go to the audit log (`DHOLE_USAGE_LOG=1`). Every attach decision is visible
- `check_login` returns a three-state verdict from DOM shape + cookie **names** (values never leave the browser — no model involved). `false` = session expired, ask the user to re-login in that Chrome window
- `cdp_url` forces the browser tier, bypasses the public cache and HTTP tier, and cannot combine with `schema=`
- Attach reuses the user's existing session — it does **not** solve DataDome / Akamai / interactive Turnstile, and 2FA still needs the user

### Scenario: the user wants to watch, or to log a site in by hand

There is no operator knob for this, on purpose. "When does a human need to be in the room" is known only by whoever is doing the task, and freezing it into machine config means walking to a command line every time it changes. It is one parameter on one call:

- `web_act(headed=true)` — run this chain in a **visible** browser window so the user can watch and step in (type a password, solve one CAPTCHA, finish 2FA). Searches and reads stay hidden by default, and `web_read` has no such parameter — passing it there is refused as an unknown name, because the public door is `web_act` only
- The ask is about the browser that **runs this call**, and it is asymmetric: a visible browser happily serves hidden work, so a later plain call rides the same window. A **hidden** browser already running on that profile is neither taken over (someone's profile suddenly appearing on screen is magic, not behaviour) nor closed to make room (the tabs thrown away may be the ones the user is standing in). That case answers `browser_busy` and names the step that hands the window back
- A login typed here survives only on a **stored** profile: `cred_defaults(set={'profile': '…'})` first, or it dies with the throwaway temp dir when that browser exits. Every row fetched on a visible window carries `metadata.browser` saying which of the two it ran on — the sentence comes from the browser instance itself, not from this call's wish, and a cache hit drops it rather than replaying a claim about a window that is long gone
- One profile is ONE browser, so while a visible window is open, later work on that profile opens tabs in front of the user. That is the point of pairing `headed=true` with a stored profile
- A visible browser is exempt from the idle reaper (`AUTO_SESSION_IDLE_TIMEOUT=300` closes only the hidden auto one — killing a window mid-typing would look like the site's fault). Hand it back when done: `sys_state()` lists session ids, `sys_state(clear=['<session id>'])` closes one, `clear=['sessions']` with `confirm=true` closes all
- Which browser binary runs is not a question anyone has to answer: detection is chrome → msedge → bundled chromium, so a machine gets what it has. Reading pages **inside** an already-logged-in Edge or Chrome is CDP attach (the scenario above) — there the browser is the user's own, window and all

### Scenario: screenshots an agent can see / semantic focus

- Element captures are **always on**: `selector` crops on the `screenshot` tool and `{"screenshot": {...}}` inside `actions` (element shots ride the response as image blocks). The captcha discipline (≤3 tries then hand off; DataDome / Akamai / interactive Turnstile / 2FA / sliders always hand off) is part of the tool descriptions. `decode_qr` (local OpenCV QR decode, in the `[all]` extra) is deterministic local work
- `focus` block-picking scores with BM25 by default. `focus_engine="neural"` switches that ONE call to the local cross-encoder (~12.5s of CPU per page — budget timeout for bulk); model unusable = explicit error (`neural_focus_model_missing`), never a silent download, and it says which half: files not on disk - the model then arrives on the first plain web_search (its default `mode` already ranks with it, and web_search has no `focus_engine`; one-time, resumable); files on disk but the reranker would not load - the message carries what this process recorded, and re-downloading changes nothing; `focus_mode="best"` returns the single top block + heading

### Scenario: poor search results / want a different pool

- `DHOLE_DEFAULT_ENGINES` — override the keyless default pool (default `baidu,bing,sogou,so360,bing_global,yahoo,yandex,brave,duckduckgo,marginalia`)
- Keyed backends (per-call billing, only run when named): `DHOLE_TAVILY_API_KEY` / `DHOLE_BOCHA_API_KEY`. Without a key a named backend answers with a diagnosable `no_key` error instead of silently vanishing
- `DHOLE_RERANK_MODEL` — which local cross-encoder scores this process's searches. `bge-zh` (default, bilingual int8, ~279MB, best Chinese) or `mmarco` (mMARCO MiniLM int8, ~119MB, ~1.8x faster; same 67-query frozen corpus: P@5 0.552 vs 0.549, MRR 0.749 vs 0.737, Chinese slightly lower and English higher). First neural search downloads it (resumable). Read at startup — **a host restart is required to switch**, because the ONNX session is one per process. The active model's name is on each search response as `rerank_model`; `sys_state(show=['models'])` lists both with their on-disk state
- The whole search has a fixed 16s deadline; per-engine cooldowns are fixed too (60s after a hard block, 600s after repeated challenge pages or connection failures, 300s patrol heartbeat) — `dhole engines list` shows what is cooling and why, `dhole engines reset` clears it
- `DHOLE_SEARCH_ROW_FILTER=off` — stop dhole from dropping SEO-farm result rows. On by default. The two signatures are shape and wording, never a domain list: a host carrying two letter+digit labels (`1za7kjca.aass341.sbs`) or a long leftmost label with digits inside it (`d3gbed2ley04jq.cloudfront.net`), or adult/gambling spam wording in the title/snippet. A row only becomes droppable below the relevance line (`relevance_raw < 0.5`), and with no reranker verdict on the round a row must match BOTH signatures — so a row the model called relevant is never dropped. Measured on the 799 real result rows in `evals/corpus/serp`: both shape rules and the wording rule hit 0. Streaming phrasing ("在线观看" / "免费观看") is deliberately NOT a signature — it hits 11 real rows, all of them legitimate platforms' own titles. What was dropped is counted in `fetch_hint` (`N dropped as SEO-farm rows ...`), never silently

### Scenario: context overhead too high

- `DHOLE_TOOLS` — register only the listed tools; the connection payload is exactly the sum of what you keep. Measured on the wire (a real server answering `initialize` + `tools/list` over stdio): all 11 = **34,357 chars** (~8k tokens) = 31,882 of tools/list + 2,475 of instructions. The instructions are not a fixed charge either: a tool-agnostic core (dhole's identity + the trust rules) of 1,262 chars always ships, and each registered tool adds its own routing line — so the 8-tool set below pays 2,116 of instructions and `sys_guide` alone pays 1,371. What keeping one tool costs (measured by unregistering it from the full set: its tools/list row, the comma in front of it, and its routing line): `web_read` 7,419 · `web_act` 4,415 · `web_search` 4,166 · `web_crawl` 3,552 · `web_request` 2,954 · `web_discover` 2,322 · `doc_parse` 1,819 · `cred_defaults` 1,668 · `sys_state` 1,914 · `feed_read` 1,555 · `sys_guide` 1,142. Note `web_crawl` and `web_discover`: they share one routing line, so unregistering either one alone saves no instructions at all — the line leaves only when neither remains. Two useful shapes: read/search/request/crawl/local-files/feeds/state/guide (8 tools) = **25,952**; adding `web_discover` (9) = **28,274**, which is the eighth-tool set plus `web_discover`'s own row and no second routing line. Two traps worth naming: sorting by per-tool size and taking "the smallest N" drops `web_read` / `web_search` / `web_act` / `web_crawl` — an arithmetic artifact, not a usable surface; and **unregistering a tool saves exactly what deleting its code would save** (the wire only follows registration), so cutting characters is never a reason to remove code.
- `DHOLE_DEFAULT_CONTENT_CHARS` — default body budget (default 40000, range 500–200000). Lower = fewer tokens now, the rest stays reachable via `offset`/`next_offset`
- The wire shape is fixed: responses are compact (a field that is ABSENT is at its documented default), text-only, with no advertised outputSchema

### Scenario: compliance / fetch behavior

- `DHOLE_IGNORE_ROBOTS=1` — disable robots.txt compliance process-wide (on by default). **Compliance is the operator's responsibility**; per-call waiver is `ignore_robots=true`. Whether the process honors robots right now: read `metadata.robots` on each response

### Scenario: browser overhead

- The stealthy browser prewarms at startup and closes after 300s idle (relaunch ~2-5s on the next fetch). No knobs — a lightweight session that never touches browser-tier tools pays nothing anyway. The one exemption is stated where it is asked: a browser started **visible** (`web_act(headed=true)`) is never reaped, because killing a window while someone is typing into it would look like the site's fault — hand it back with `sys_state(clear=['<session id>'])` instead

### Scenario: serving dhole over HTTP beyond loopback

- `DHOLE_HTTP_TOKEN` — bearer token for the `--http` transport: when set, every `/mcp` call must carry it (`Authorization: Bearer …` or `X-Dhole-Token`, constant-time compare); unset keeps the open-door behavior on whatever `--host` says (default 127.0.0.1), with a startup warning on any no-token bind — loopback included, since two instances in one process are two clients either way. The token gates the port, it does not separate principals: every client on the port shares one credential store, one session state and one cache — to isolate principals, run one instance per principal (separate port/process). Read at startup — changing it means restarting the server. The transport also validates Host/Origin headers against an allowlist built from `--host` (DNS-rebinding guard); a `0.0.0.0` bind cannot predict client Host headers, which is exactly why the token exists

### Scenario: data location / privacy / diagnostics

- `DHOLE_HOME` — state directory (default `~/.dhole`: plaintext body cache; point elsewhere on shared machines; delete the directory for a full cleanup)
- `DHOLE_PERSIST_CREDENTIALS=1` — persist session cookies and the stored identity to disk, encrypted. The default is **memory only**: `sessions.db` / `identity.db` are never created, credentials end with the process, and a legacy plaintext file is absorbed into memory and deleted on first use. With the flag set, every value is encrypted with Windows DPAPI before it touches disk — the key is bound to the Windows user account, which protects a copied or synced file but NOT another process running as the same user. Non-Windows platforms have no DPAPI: the flag is refused there (stays in memory, `dhole --doctor` says so) rather than falling back to plaintext. Read at startup — changing it means restarting the server
- `DHOLE_USAGE_LOG=1` — local call log (tool names / durations / redacted errors; no parameter values, no network). CDP authorize/refuse events land here too
- `DHOLE_HF_ENDPOINT` (or `HF_ENDPOINT`) — rerank-model download mirror; default tries huggingface.co then falls back to hf-mirror.com; setting it pins one source (set `https://hf-mirror.com` when downloads stall in CN)

## CLI management commands

| Command | Purpose |
|---|---|
| `dhole -v` | Version + capability panel (browser / pdf / rerank / engine yield — each missing row is a degraded tier) |
| `dhole --doctor` | The one command every install should be checked with: 12 items in order — python+platform, the five dependency tiers, chromium binary **and one real launch of it**, rerank model (+whether a publisher digest backs it), a real stdio handshake with the host's own command, the two config doors and which one shadows which, retired `DHOLE_*` names a host still sets, robots posture (complied / waived / **not readable — and "not checked" is not "complied"**), the search egress with an actual TCP connect, **one real search + one real fetch** (keyless engines have no SLA; this is the only proof it works from this IP today), what `~/.dhole` holds (sizes, ages, which files are credentials), and the connect-phase token cost. Every failure prints a command to run — and exits 1 |
| `dhole doctor` | Same output and exit code as `dhole --doctor` (the two spellings are equivalent, use whichever matches the other subcommand you already typed) |
| `dhole engines list` | Per-engine verdict, remaining cooldown, active backoff |
| `dhole engines probe` | One real query per engine (hits / latency / block reason) |
| `dhole engines reset` | Clear cooldowns now |
| reranker model | Two registered local cross-encoders, selected by `DHOLE_RERANK_MODEL` (startup read; restart to switch). Default `bge-zh` (bilingual int8, ~279MB); `mmarco` (multilingual MiniLM int8, ~119MB, ~1.8x faster) is the measured fast profile. A plain `web_search` fetches the active one on first use under its default `mode` (resumable) - `web_search` has no `focus_engine`. A `~/.dhole/config/reranker.json` left over from an older install selects nothing; the first model lookup logs one line saying where the selector moved, rather than silently switching the scorer your `min_raw_relevance` was calibrated against |
| `dhole --http --host --port` | Serve over streamable HTTP instead of stdio. **No authentication**: `--host 0.0.0.0` exposes "fetch any URL on your behalf" to the whole network — put a reverse proxy / auth / firewall in front on shared networks |
| self-update | No command does it: `dhole -u` and the unattended reinstall are gone (an any-ImportError trigger that killed every dhole process and force-reinstalled hundreds of MB). Update by hand — `git pull && python -m pip install -e ".[all]"` — then restart the host; `dhole -v` prints exactly this. When the launcher cannot import the server it names the piece that is missing and prints the command that fixes THAT one: a missing module gets the `.[all]` dependency install, while `cannot import name X from Y` is dhole's own files disagreeing — it names the symbol and says to restore the code (`git pull`, or uninstall + reinstall dhole itself with `--no-deps`), because installing dependencies would change nothing |
