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

### Scenario: poor search results / want a different pool

- `DHOLE_DEFAULT_ENGINES` — override the keyless default pool (default `baidu,bing,sogou,bing_global,yandex,brave`); opt-in names may be included
- `DHOLE_SEARCH_DEADLINE` — whole-search deadline in seconds (default 16)
- `DHOLE_SEARCH_FEEDBACK=1` — domain preference: domains that fetched well gain ranking weight (off by default)
- Keyed backends (per-call billing, off by default, only run when named): `DHOLE_BRIGHTDATA_API_KEY` (optional `_ZONE` default `dhole`, `_COUNTRY` default `us`) / `DHOLE_TAVILY_API_KEY` / `DHOLE_EXA_API_KEY` / `DHOLE_BOCHA_API_KEY`

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

## CLI management commands

| Command | Purpose |
|---|---|
| `dhole -v` | Version + capability panel (browser / pdf / rerank / engine yield — each missing row is a degraded tier) |
| `dhole --doctor` | Install check: item-by-item with copy-pasteable fixes |
| `dhole engines list` | Per-engine verdict, remaining cooldown, active backoff |
| `dhole engines probe` | One real query per engine (hits / latency / block reason) |
| `dhole engines reset` | Clear cooldowns now |
| `dhole proxy list/add/remove/clear` | Search proxy pool management |
| `dhole model` / `dhole model use <name>` | Rerank model: `bge-zh` (default, bilingual, ~279MB) / `zh-full` (cross-lingual ~450MB) / `ms-marco` (English-first ~91MB); recalibrate `min_raw_relevance` after switching |
| `dhole --http --host --port` | Serve over streamable HTTP instead of stdio. **No authentication**: `--host 0.0.0.0` exposes "fetch any URL on your behalf" to the whole network — put a reverse proxy / auth / firewall in front on shared networks |
| `dhole -u` | Self-update (off in the personal fork; update via `git pull && pip install -e .` then restart the host) |
