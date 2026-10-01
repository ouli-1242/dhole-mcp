# Configuration Guide

Users rarely read READMEs, but many problems (blocked engines, internal-host false positives, weak results, context bloat) are solved by **changing one setting**. This volume is organized by "when to touch what". All variables are optional; zero-config works by default.

## The rules of changing things

- **Single-call behavior** → call parameters (`ignore_robots` / `allow_private` / `proxy` / `cache_ttl` …). No config edit, no restart. Prefer this whenever it suffices
- **Process-wide behavior** → environment variables, in the host's MCP config under the `dhole` server's `env` block, **then restart the host**:

```json
{
  "mcpServers": {
    "dhole": {
      "command": "dhole",
      "env": {
        "DHOLE_SEARCH_PROXY": "http://127.0.0.1:7890",
        "DHOLE_SSRF_DNS_RECHECK": "0"
      }
    }
  }
}
```

- Values are strings; booleans are `"1"` / `"0"`
- Changing env vars edits the user's machine — present options and get consent first

## Variables by scenario

### Scenario: search engines blocked / rate-limited / should use a proxy

- `DHOLE_SEARCH_PROXY` — search-layer proxy; comma-separated to rotate; **also auto-reads** `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY`. Alternatively `dhole proxy add` writes to the pool (`dhole proxy list/add/remove/clear`; pool credentials are stored **in plaintext** in `~/.dhole/search_proxies.json`, masked in `list`)
- Division of labor: this variable covers the **search layer only**; fetches go through a proxy via per-call `options.proxy` (with a 5s precheck)

### Scenario: Clash / sing-box TUN proxy (fake-IP mode)

- `DHOLE_SSRF_DNS_RECHECK=0` — **required**, or the DNS recheck flags every public site as internal and nothing is fetchable. Do not set it for plain HTTP proxies

### Scenario: fetching intranet / local dev services

- `DHOLE_ALLOW_PRIVATE_HOSTS` — comma-separated allowlist (`localhost,my-service.local,192.168.1.50`) bypassing the SSRF guard for those hosts; default empty = reject all. Per-call: `options.allow_private=true` (loopback only) or a list
- **Cloud metadata endpoints (169.254.169.254, metadata.google.internal, …) are never allowed**, allowlist or not
- With a proxy running, loopback traffic may be intercepted by the proxy itself (502 instead of connection-refused) — `NO_PROXY` needs `127.0.0.1,localhost` (see troubleshooting)

### Scenario: pages behind a login wall — CDP attach (17.0)

One-time user setup, two steps. (1) Launch a **copy-profile** Chrome with remote debugging (Chrome 136+ refuses debugging on the default profile):

```
chrome.exe --user-data-dir=D:\dhole-chrome --remote-debugging-port=9222
```

Log into the target site in that window once. (2) Add the gate env vars to the dhole server's `env` block and restart the host:

- `DHOLE_CDP_ATTACH=1` — master switch, **default off**. Attaching = handing dhole read access to every site logged into that Chrome, so it is deliberately gated: without it every `cdp_url` call is refused (`cdp_attach_disabled`, no request sent)
- `DHOLE_CDP_HOSTS` — comma-separated host allowlist (e.g. `portal.example.com,mail.example.com`). Enforced at the route level (including page-JS subresource requests) and at the fetch entry; a non-listed host fails with `cdp_host_not_allowed`

The call itself is per-call, no restart needed afterwards:

```json
{"url": "https://portal.example.com/dashboard", "options": {"cdp_url": "http://127.0.0.1:9222", "check_login": true}}
```

- The response marks `cdp_attached: true`; attach events go to the audit log (`DHOLE_USAGE_LOG=1`). Every attach is visible
- `check_login` returns a three-state verdict from DOM shape + cookie **names** (values never leave the browser — no model involved). `false` = session expired, ask the user to re-login in that Chrome window
- `cdp_url` forces the browser tier, bypasses the public cache and HTTP tier, and cannot combine with `schema=`
- Attach reuses the user's existing session — it does **not** solve DataDome / Akamai / interactive Turnstile, and 2FA still needs the user

Related knobs (not the attach path itself): `DHOLE_BROWSER_PROFILE` — persist dhole's own stealthy-browser profile under `~/.dhole/profiles/<name>/` instead of a temp dir; `DHOLE_COOKIE_TTL_S` — HTTP-tier session-cookie lifetime (default 86400, clamped 60s–30d) so a working HTTP login outlives a day. Since 17.0 a **named** `session_id` also bridges the jar into that session's dedicated stealthy browser (seeded before navigation, harvested after) — no env var needed, just pass the same `session_id` to browser-tier calls; the shared auto session and CDP attach never bridge.

### Scenario: page operation, screenshots an agent can see, semantic focus (17.0)

- `DHOLE_MULTIMODAL=1` — declare that **the calling agent can see images** (dhole stays zero-model; recognition is yours). Unlocks `options.selector` element crops on the `screenshot` tool and `{"screenshot": {...}}` actions (element shots ride the response as image blocks), and appends the captcha discipline (≤3 tries then hand off; DataDome / Akamai / interactive Turnstile / 2FA / sliders always hand off) to the tool descriptions. Off by default; with it off those options are refused with an explanation. `decode_qr` (local OpenCV QR decode, in the `[all]` extra) is deterministic local work and needs no flag
- `DHOLE_NEURAL_FOCUS=1` — `focus` block-picking (`smart_fetch`) and focus page-filtering (`smart_crawl`) score semantically with the local reranker instead of BM25 keyword overlap; default off (BM25). Model missing = explicit error (`neural_focus_model_missing`), never a silent downgrade. Same model as search ranking: `dhole model use <name>` swaps it; `options.focus_mode="best"` (per call, no config) returns the single top block + heading

### Scenario: repeatable tasks and change monitoring (17.0 opt-in)

Two tools that persist state across sessions, off by default because their schemas add ~2.6k chars to every connection:

- `DHOLE_TASKS=1` — registers `recipe` (save a `url` + `actions` chain, replay it later) and `watch` (diff a page against its last snapshot). State lives in `~/.dhole/tasks.db`: action chains and page baselines, **no credentials**. Recipes deliberately store no `session_id` — pass it per run. Forget everything by deleting the file; per-item removal is `recipe(op='delete')` / `watch(op='remove')`
- Naming either tool in `DHOLE_TOOLS` enables it without the group flag

### Scenario: signing in with a credential the user already stores (17.0 opt-in)

- `DHOLE_VAULT=1` — registers `vault` (read item **metadata** from the user's own Bitwarden `bw` / 1Password `op`) and `login` (assisted sign-in). Off by default: it only does anything if the user actually runs one of those CLIs, and it is the one pair that touches their credentials
- Prerequisites are the user's: the CLI installed and its vault unlocked (`bw unlock` / `op signin`). `vault(op='status')` reports which manager answered, whether it is unlocked, and the fix when it is not — a missing manager is a note, not an error
- **Values never leave the process**: `vault` has no field for a password or a TOTP secret, and `login` reads the password server-side and reports the item name instead. Nothing reaches the transcript, the cache, or the log
- `login` is **gated on approval**: without `confirm=true` it only detects the form and returns the plan. Present that to the user before re-calling with `confirm=true`
- CAPTCHA / 2FA / DataDome-class walls remain hand-offs; this path logs in, it does not defeat challenges

### Scenario: poor search results / want a different pool

- `DHOLE_DEFAULT_ENGINES` — override the keyless default pool (default `baidu,bing,sogou,bing_global,yandex,brave`); opt-in names may be included
- `DHOLE_SEARCH_DEADLINE` — whole-search deadline in seconds (default 16)
- `DHOLE_SEARCH_FEEDBACK=1` — domain preference: domains that fetched well gain ranking weight (off by default)
- Keyed backends (per-call billing, off by default, only run when named): `DHOLE_BRIGHTDATA_API_KEY` (optional `DHOLE_BRIGHTDATA_ZONE` default `dhole` and `DHOLE_BRIGHTDATA_COUNTRY` default `us`, both read at import so a change needs a host restart) / `DHOLE_TAVILY_API_KEY` / `DHOLE_EXA_API_KEY` / `DHOLE_BOCHA_API_KEY`

### Scenario: cooldowns too frequent / pacing

Three cooldown tiers (defaults 60s / 600s / 600s; out-of-range values are clamped and explained):
- `DHOLE_ENGINE_COOLDOWN` — ordinary blocks (5–1800)
- `DHOLE_ENGINE_CHALLENGE_COOLDOWN` — 3 consecutive challenge pages (60–7200)
- `DHOLE_ENGINE_CONN_COOLDOWN` — 3 consecutive connection failures (60–7200)
- `DHOLE_ENGINE_HEARTBEAT` — patrol interval for cooling engines (default 300s, `0`=off); failed probes never extend penalties

### Scenario: context overhead too high

- `DHOLE_TOOLS` — register only the listed tools (`smart_fetch,smart_search` ≈ −49% connection overhead). Worth it for lightweight search-and-fetch sessions
- `DHOLE_DEFAULT_CONTENT_CHARS` — default body budget (default 40000, range 500–200000)
- `DHOLE_STRUCTURED_CONTENT=1` — also emit a structuredContent copy per call (doubles volume); only when a downstream parses structured fields
- `DHOLE_OUTPUT_SCHEMA=1` — declare outputSchema in tools/list (+14.6% connection overhead); only for validating clients or human-facing UIs
- `DHOLE_WIRE_FULL=1` — restore the pre-slim response shape (every field always present); for callers that read `result["field"]` directly and don't understand absence-means-default

### Scenario: compliance / fetch behavior

- `DHOLE_IGNORE_ROBOTS=1` — disable robots.txt compliance process-wide (on by default). **Compliance is the operator's responsibility**; per-call waiver is `ignore_robots=true`. Whether the process honors robots right now: read `metadata.robots` on each response

### Scenario: browser overhead

- `DHOLE_BROWSER_IDLE_TIMEOUT` — idle shutdown seconds (default 300, `0`=never)
- `DHOLE_NO_BROWSER_PREWARM=1` — skip prewarming the stealthy browser at startup (default prewarms, saving a 3–5s cold start on first fetch)

### Scenario: data location / privacy / diagnostics

- `DHOLE_HOME` — state directory (default `~/.dhole`: plaintext body cache, **cookie jar with credentials**, plaintext proxy-pool credentials; point elsewhere on shared machines; delete the directory for a full cleanup)
- `DHOLE_WORKDIR` — extra directory `parse` tries for relative paths (after the `cwd` parameter)
- `DHOLE_USAGE_LOG=1` — local call log (tool names / durations / redacted errors; no parameter values, no network)
- `DHOLE_HF_ENDPOINT` (or `HF_ENDPOINT`) — rerank-model download mirror; default tries huggingface.co then falls back to hf-mirror.com; setting it pins one source (set `https://hf-mirror.com` when downloads stall in CN)
- `DHOLE_NO_AUTO_REPAIR=1` — on ImportError, print the fix command instead of auto-reinstalling
- `DHOLE_UPDATE_PACKAGE` — the package name the self-update path installs (`dhole` and `dhole -u`). Defaults to `dhole-mcp`; a fork or a private mirror points it elsewhere. Read per call.
- `DHOLE_UPDATE_INDEX_URL` — pip index URL used by the self-update path, so a machine with no PyPI access can pull `dhole-mcp` from an internal mirror. Read per call.

## CLI management commands

| Command | Purpose |
|---|---|
| `dhole -v` | Version + capability panel (browser / pdf / rerank / engine yield — each missing row is a degraded tier) |
| `dhole --doctor` | Install check: item-by-item with copy-pasteable fixes; also runs the config audit + fake-ip resolver reality check on this machine |
| `dhole doctor` | Same output and exit code as `dhole --doctor` (W5: the two spellings are equivalent, use whichever matches the other subcommand you already typed) |
| `dhole init` | One-shot install + MCP client registration (W8): checks browser deps, offers `playwright install chromium`, detects Claude Desktop / Cursor / Windsurf / VS Code configs, prints the JSON it would add. **Dry-run by default**; only `--yes` writes, and it always leaves a `.bak-<timestamp>` next to every modified file. Exits 0 with manual instructions when no known client is on disk. |
| `dhole engines list` | Per-engine verdict, remaining cooldown, active backoff |
| `dhole engines probe` | One real query per engine (hits / latency / block reason) |
| `dhole engines reset` | Clear cooldowns now |
| `dhole proxy list/add/remove/clear` | Search proxy pool management |
| `dhole model` / `dhole model use <name>` | Rerank model: `bge-zh` (default, bilingual, ~279MB) / `zh-full` (cross-lingual ~450MB) / `ms-marco` (English-first ~91MB); recalibrate `min_raw_relevance` after switching |
| `dhole --http --host --port` | Serve over streamable HTTP instead of stdio. **No authentication**: `--host 0.0.0.0` exposes "fetch any URL on your behalf" to the whole network — put a reverse proxy / auth / firewall in front on shared networks |
| `dhole -u` | Self-update (off in the personal fork; update via `git pull && pip install -e .` then restart the host) |
