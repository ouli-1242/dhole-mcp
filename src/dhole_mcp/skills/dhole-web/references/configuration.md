# Configuration Guide

17.0 大众化一刀切：`pip install dhole-mcp` 接上宿主就能用，**能力全部默认在线**。所以可配置的东西只剩 11 个 `DHOLE_*` 变量 —— 每一个都是"这台机器的事实"（代理、内网、密钥、状态目录），而不是"要不要开某个能力"。所有变量可选；零配置即默认全量行为。

## The rules of changing things

- **Single-call behavior** → call parameters (`ignore_robots` / `cache_ttl` / `focus_engine` / `anti_detect` …). No config edit, no restart. Prefer this whenever it suffices
- **Per-site defaults, the new third door** → `cred_defaults` (a tool, not a config file): proxy, browser profile, CDP endpoint, private-host waiver, anti-detect rung, and host-scoped `auth` / `cookies`. Written once to `~/.dhole/identity.db`, read back by every later call, shown redacted, revoked with `unset`. No restart, and no credential copied into the conversation each time
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
- **Retired knobs are silently ignored.** 17.0 deleted the old on/off switches (wire shape, tool groups, multimodal, neural-focus license, cooldown tuning, self-update…); their values are now fixed in code because 能算清的写死. A stale `DHOLE_…=1` in an env block does nothing and is not an error — `dhole --doctor` is the one place that names what a host still sets that no longer does anything

## Variables by scenario

### Scenario: search engines blocked / rate-limited / should use a proxy

- `DHOLE_SEARCH_PROXY` — the single egress every search engine goes through; **also auto-reads** `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` when unset (sandboxes and CI often already have one; `dhole --doctor` names which variable answered). 17.0 removed the proxy pool — there is no `dhole proxy` command and no `~/.dhole/search_proxies.json`, so a comma-separated value rotates nothing (first URL wins, the rest are dropped with a warning) and a value with an unrecognised scheme (`http`/`https`/`socks5`/`socks5h` only — usually a missing `http://`) is treated as unset, also with a warning. Credentials in the URL are never echoed back anywhere: doctor, errors and the usage audit all show `http://***@host:3128`
- Division of labor: this variable covers the **search layer only**; fetches go through a proxy via the stored `cred_defaults` `proxy` (credited as an explicit proxy, so it gets the 5s precheck)

### Scenario: Clash / sing-box TUN proxy (fake-IP mode)

- Nothing to set. A fake-IP resolver (every public name answering with an address in `198.18.0.0/15` / `fc00::/7`) is detected by a one-time startup probe, and only the SSRF DNS-recheck layer skips itself — the reason travels in `metadata.ssrf_recheck` on every affected response, and `dhole --doctor`'s "resolver reality" row names the address/network. IP-literal, hostname and cloud-metadata guards stay on; there is no manual override (放宽守卫需要证据，不需要猜测)

### Scenario: fetching intranet / local dev services

- `DHOLE_ALLOW_PRIVATE_HOSTS` — comma-separated allowlist (`localhost,my-service.local,192.168.1.50`) bypassing the SSRF guard for those hosts; default empty = reject all. Per-machine door; the tool-side equivalent is `cred_defaults` `set={allow_private: true}` (loopback only) or a host list, which the next call reads back
- **Cloud metadata endpoints (169.254.169.254, metadata.google.internal, …) are never allowed**, allowlist or not
- With a proxy running, loopback traffic may be intercepted by the proxy itself (502 instead of connection-refused) — `NO_PROXY` needs `127.0.0.1,localhost` (see troubleshooting)

### Scenario: pages behind a login wall — CDP attach

One-time user setup: launch a **copy-profile** Chrome with remote debugging (Chrome 136+ refuses debugging on the default profile) and log into the target site in that window once:

```
chrome.exe --user-data-dir=D:\dhole-chrome --remote-debugging-port=9222
```

Then just call — the first attach asks, the user answers once:

```json
{"url": "https://portal.example.com/dashboard", "cdp_url": "http://127.0.0.1:9222", "check_login": true}
```

- **先问再记**: attaching = handing dhole read access to every site logged into that Chrome, so the FIRST attach to a host is refused with `cdp_authorization_required` and **nothing is executed**. Re-call the same call with `confirm=true` to approve that one host; the approval is persisted to `~/.dhole/cdp_hosts.json` and every later attach is fenced to the authorized set. Revoke = delete that file
- The fence is enforced at the route level (including page-JS subresource requests) and at the fetch entry; a non-authorized host fails instead of navigating. **The match is an EXACT hostname**, not a domain or a suffix: `example.com` does not cover `www.example.com`, and a page that loads scripts from a CDN needs that CDN confirmed too. No wildcards
- The response marks `cdp_attached: true`; authorize/refuse events go to the audit log (`DHOLE_USAGE_LOG=1`). Every attach decision is visible
- `check_login` returns a three-state verdict from DOM shape + cookie **names** (values never leave the browser — no model involved). `false` = session expired, ask the user to re-login in that Chrome window
- `cdp_url` forces the browser tier, bypasses the public cache and HTTP tier, and cannot combine with `schema=`
- Attach reuses the user's existing session — it does **not** solve DataDome / Akamai / interactive Turnstile, and 2FA still needs the user

### Scenario: screenshots an agent can see / semantic focus

- Element captures are **always on**: `selector` crops on the `screenshot` tool and `{"screenshot": {...}}` inside `actions` (element shots ride the response as image blocks). The captcha discipline (≤3 tries then hand off; DataDome / Akamai / interactive Turnstile / 2FA / sliders always hand off) is part of the tool descriptions. `decode_qr` (local OpenCV QR decode, in the `[all]` extra) is deterministic local work
- `focus` block-picking scores with BM25 by default. `focus_engine="neural"` switches that ONE call to the local cross-encoder (~12.5s of CPU per page — budget timeout for bulk); model missing = explicit error (`neural_focus_model_missing`), never a silent download. The model arrives on the first neural web_search (one-time, resumable); `focus_mode="best"` returns the single top block + heading

### Scenario: repeatable tasks, change monitoring, stored credentials

- `web_recipe` (save a `url` + `actions` chain, replay it later) and `web_watch` (diff a page against its last snapshot) are **always registered**. State lives in `~/.dhole/tasks.db`: action chains and page baselines, **no credentials**. Recipes deliberately store no `session_id` — pass it per run. Forget everything by deleting the file; per-item removal is `web_recipe(op='delete')` / `web_watch(op='remove')`
- `cred_vault` (read item **metadata** from the user's own Bitwarden `bw` / 1Password `op`) and `cred_login` (assisted sign-in) are also always registered. Prerequisites are the user's: the CLI installed and its vault unlocked (`bw unlock` / `op signin`). `cred_vault(op='status')` reports which manager answered, whether it is unlocked, and the fix when it is not — a missing manager is a note, not an error
- **Values never leave the process**: `cred_vault` has no field for a password or a TOTP secret, and `cred_login` reads the password server-side and reports the item name instead. Nothing reaches the transcript, the cache, or the log
- `cred_login` is **gated on approval**: without `confirm=true` it only detects the form and returns the plan. Present that to the user before re-calling with `confirm=true`
- CAPTCHA / 2FA / DataDome-class walls remain hand-offs; this path logs in, it does not defeat challenges

### Scenario: poor search results / want a different pool

- `DHOLE_DEFAULT_ENGINES` — override the keyless default pool (default `baidu,bing,sogou,so360,bing_global,yandex,brave,duckduckgo`)
- Keyed backends (per-call billing, only run when named): `DHOLE_TAVILY_API_KEY` / `DHOLE_BOCHA_API_KEY`. Without a key a named backend answers with a diagnosable `no_key` error instead of silently vanishing
- The whole search has a fixed 16s deadline; per-engine cooldowns are fixed too (60s after a hard block, 600s after repeated challenge pages or connection failures, 300s patrol heartbeat) — `dhole engines list` shows what is cooling and why, `dhole engines reset` clears it

### Scenario: context overhead too high

- `DHOLE_TOOLS` — register only the listed tools; the connection payload is exactly the sum of what you keep. Measured 2026-10-03 with `python tests/tool_payload_measure.py`: all 17 = **40,010 chars** (~9.6k tokens), of which 3,082 is fixed (the `{"tools":[…]}` envelope plus the initialize-time instructions, which are not a knob). Per-tool cost on the wire (the delta in the total when that tool is registered): `web_read` 6,188 · `web_search` 3,781 · `web_act` 3,446 · `web_crawl` 3,263 · `web_screenshot` 2,635 · `web_request` 1,909 · `web_recipe` 1,817 · `cred_login` 1,798 · `sys_state` 1,583 · `web_watch` 1,561 · `web_discover` 1,554 · `cred_defaults` 1,541 · `feed_read` 1,505 · `doc_parse` 1,421 · `cred_vault` 1,209 · `sys_guide` 1,053 · `web_resolve` 665. Two useful shapes: read/search/request/crawl/local-files/feeds/state/guide (8 tools) = **23,784**; adding `web_resolve` + `cred_vault` (10) = **25,658**. Two traps worth naming: sorting by per-tool size and taking "the smallest N" drops `web_read` / `web_search` / `web_act` / `web_crawl` — an arithmetic artifact, not a usable surface; and **unregistering a tool saves exactly what deleting its code would save** (the wire only follows registration), so cutting characters is never a reason to remove code.
- `DHOLE_DEFAULT_CONTENT_CHARS` — default body budget (default 40000, range 500–200000). Lower = fewer tokens now, the rest stays reachable via `offset`/`next_offset`
- The wire shape is fixed: responses are compact (a field that is ABSENT is at its documented default), text-only, with no advertised outputSchema

### Scenario: compliance / fetch behavior

- `DHOLE_IGNORE_ROBOTS=1` — disable robots.txt compliance process-wide (on by default). **Compliance is the operator's responsibility**; per-call waiver is `ignore_robots=true`. Whether the process honors robots right now: read `metadata.robots` on each response

### Scenario: browser overhead

- Fixed in 17.0: the stealthy browser prewarms at startup and closes after 300s idle (relaunch ~2-5s on the next fetch). No knobs — a lightweight session that never touches browser-tier tools pays nothing anyway

### Scenario: data location / privacy / diagnostics

- `DHOLE_HOME` — state directory (default `~/.dhole`: plaintext body cache, **cookie jar with credentials**; point elsewhere on shared machines; delete the directory for a full cleanup)
- `DHOLE_USAGE_LOG=1` — local call log (tool names / durations / redacted errors; no parameter values, no network). CDP authorize/refuse events land here too
- `DHOLE_HF_ENDPOINT` (or `HF_ENDPOINT`) — rerank-model download mirror; default tries huggingface.co then falls back to hf-mirror.com; setting it pins one source (set `https://hf-mirror.com` when downloads stall in CN)

## CLI management commands

| Command | Purpose |
|---|---|
| `dhole -v` | Version + capability panel (browser / pdf / rerank / engine yield — each missing row is a degraded tier) |
| `dhole --doctor` | The one command every install should be checked with: 12 items in order — python+platform, the five dependency tiers, chromium binary, rerank model (+whether a publisher digest backs it), a real stdio handshake with the host's own command, the two config doors and which one shadows which, retired `DHOLE_*` names a host still sets, robots posture (complied / waived / **not readable — and "not checked" is not "complied"**), the search egress with an actual TCP connect, **one real search + one real fetch** (keyless engines have no SLA; this is the only proof it works from this IP today), what `~/.dhole` holds (sizes, ages, which files are credentials), and the connect-phase token cost. Every failure prints a command to run — and exits 1 |
| `dhole doctor` | Same output and exit code as `dhole --doctor` (the two spellings are equivalent, use whichever matches the other subcommand you already typed) |
| `dhole engines list` | Per-engine verdict, remaining cooldown, active backoff |
| `dhole engines probe` | One real query per engine (hits / latency / block reason) |
| `dhole engines reset` | Clear cooldowns now |
| reranker model | No command selects it: `bge-zh` (bilingual, ~279MB) is the default and a neural `web_search` fetches it on first use (resumable). `~/.dhole/config/reranker.json` (`{"model": "<name>"}`) is still honoured if you wrote one by hand — `min_raw_relevance` is calibrated per model, so recalibrate after switching. A later 17.0 step collapses the registry to one model and this file disappears |
| `dhole --http --host --port` | Serve over streamable HTTP instead of stdio. **No authentication**: `--host 0.0.0.0` exposes "fetch any URL on your behalf" to the whole network — put a reverse proxy / auth / firewall in front on shared networks |
| self-update | No command does it: 17.0 deleted `dhole -u` and the unattended reinstall (an any-ImportError trigger that killed every dhole process and force-reinstalled hundreds of MB). Update by hand — `git pull && python -m pip install -e ".[all]"` — then restart the host; `dhole -v` prints exactly this |
