# Troubleshooting Playbook

Ground rule: **read the response fields before acting**. Every failure names itself (`error` / `summary` / `escalation_path` / `metadata`). Identify the layer first (network → site → tool → config), then pick a fix. Fixes come in two tiers: **per-call** (call parameters, use freely) and **process-wide** (env vars — requires editing the host's MCP config and a restart; explain to the user before doing it).

## Search

### Sparse / empty / slow results

1. Read the response: how many engines in `engines_used`? `engines_consensus` x of what? A long `engine_preempted` / `engine_empty` list? — the pool is degraded (rate-limited / blocked / cooling)
2. Retry once as-is (transient jitter is common)
3. Still thin: CLI `dhole engines list` for each engine's verdict and remaining cooldown; `dhole engines probe` for one real query per engine (hits / latency / block reason)
4. Fix:
   - Per-call: re-search naming live engines (`engines=["baidu","bing","sogou"]` or a usable opt-in)
   - Urgent: `dhole engines reset` or `cache_clear(engine_state=true)` to clear cooldowns now
   - Process-wide (only if recurring): a search proxy via `DHOLE_SEARCH_PROXY` (or `dhole proxy add` for a rotating pool), or a keyed backend as fallback
5. Tell the user: "Search engines were rate-limiting me; I re-searched via X. If this keeps happening we can add a search proxy."

### Whole round empty, `fetch_hint` reports error

The entire pool is cooling. Read remaining seconds from `dhole engines list` and decide wait vs reset; probe failures never extend penalties (`DHOLE_ENGINE_HEARTBEAT` patrols cooling engines every 300s by default and lifts cooldowns on a successful probe).

### The same engine keeps cooling

This IP is being watched. The three cooldown tiers default to 60s / 600s / 600s (blocked / challenge x3 / unreachable x3) — raising `DHOLE_ENGINE_COOLDOWN` only makes the pool deader. The real fix is a different egress (proxy pool) or keyed engines. **Do not set `DHOLE_ENGINE_COOLDOWN` to hours** — that's a 16-hour dead pool; nobody wants that.

## Fetch

### Content is a shell / obviously wrong

1. Check `total_extracted_chars` and `page_type`: a few hundred chars plus a wrong `page_type` = placeholder
2. Retry with `force_fetcher='stealthy'`
3. Still thin: the site needs login (→ "Sites behind login" below) or runs DataDome / Akamai / interactive Turnstile (unbeatable) — **say so honestly**; never answer from the shell
4. Tell the user: "This site blocks plain scraping; what I got is the pre-login shell. For real content I'd need your session cookie, or we use a public source."

### `robots_disallowed`

Default compliance: **zero requests** were sent for that URL. A one-shot waiver is `ignore_robots=true` — the user's compliance decision, ask first. Process-wide: `DHOLE_IGNORE_ROBOTS=1`. Whether robots are honored right now: read the `metadata.robots` field on every response (it says `bypassed` when process-wide off). When robots.txt itself is unfetchable (5xx / timeout) the tool proceeds and retries at a 60s short TTL — don't read "couldn't check" as "compliance passed".

### Every public URL flagged as internal

A fake-IP TUN proxy (Clash / sing-box TUN mode) makes the DNS recheck classify public IPs as internal. Fix: `DHOLE_SSRF_DNS_RECHECK=0` in the MCP config, **then restart the host**. Plain HTTP proxies are unaffected.

### Fetch timeouts

`timeout` is the wall-clock budget for the whole call (default 30s, max 120s); document **download** time counts fully — for big PDFs raise `timeout`. The 50MB body cap gates BEFORE download; oversized bodies fail with `Response body too large` and raising `timeout` won't help.

### `proxy_unreachable`

The explicit `options.proxy` failed its 5s TCP precheck — no request was sent (no browser escalation, no snapshot fallback). Raising `timeout` is useless: check the proxy's address / port / liveness, or drop the proxy for a direct connection.

### Stale content / someone else's snapshot

When `source` shows an archive fallback, that is a dated snapshot (`archived_at`). Label citations with the date; proactively flag time-sensitive claims. Two guarantees: the server's own JSON/XML error bodies are never replaced by snapshots (4xx/5xx with json/xml content-type are preserved as-is); an unreachable proxy never falls back to a snapshot (the request never went out — answering from a snapshot would answer a question nobody asked).

### 401 / credential problems

The response distinguishes "credentials rejected" (supply a fresh token) from "no credentials sent" (add `options.auth`) — the fixes are opposite, so read which one it is. Also check: did a cross-origin redirect strip the credentials (same-host hops keep them, cross-domain hops strip them)? Expired tokens are simply replaced — the tool runs no refresh flow.

### Mojibake

`parse` with explicit `encoding=`. **Shift_JIS / EUC-KR decode into plausible-looking but wrong Chinese** and sit outside auto-detection — when the Chinese reads fluently and impossibly, suspect this first.

### Sites behind login

Four paths, cheapest first: (1) HTTP tier — user-supplied cookies (`options.cookies` + `options.session_id` to keep the jar) or an API token (`options.auth`: basic/bearer/header). (2) The **no-password browser fallback (17.0)**: a named `session_id` bridges the jar into that session's dedicated stealthy browser (and harvests back), so HTTP-tier login state rides browser calls; combine with `check_login` + snapshot actions + gated submit to operate the site without ever touching a password. (3) CDP attach (17.0) — `smart_fetch` with `options.cdp_url` (+ `check_login=true`) runs the fetch inside the user's real logged-in Chrome; requires one-time user setup (configuration.md "CDP attach"). (4) Still impossible: DataDome / Akamai / interactive Turnstile walls and 2FA — neither the bridge nor attach defeats challenges; say so honestly.

Attach-specific failures:
- `cdp_attach_disabled` — the gate is off by default. The user must add `DHOLE_CDP_ATTACH=1` + `DHOLE_CDP_HOSTS` to the dhole server env and restart the host. No request was sent; retrying changes nothing
- `cdp_host_not_allowed` — the host is outside `DHOLE_CDP_HOSTS`. Ask the user to allowlist it; never work around by fetching a different host with the user's login state
- `login_check.logged_in: false` — session expired in the attached Chrome; the user re-logs in their window, then re-run the call
- `logged_in: null` — the page's DOM shape wasn't enough to decide (pure-JSON endpoints, odd layouts); read `signals` and say what's uncertain instead of guessing

### Page-action failures (snapshot / index actions / submit)

- `pending_approval: true` in `metadata.actions` — not a failure: the submit PAUSED before acting, nothing was sent. Describe the submission to the user; on a clear yes re-run the **whole chain** with `confirm: true` on that submit (each call reloads the page, so prior fill/click steps must be redone)
- An error naming the element index ("beyond this page's N interactive elements", or an `expect` mismatch reporting what the element reads now) — the page changed under you. Re-run `{"snapshot": true}` and re-address; don't retry the stale index
- The snapshot came back empty / `could not enumerate` — the document was still loading; add `{"wait_selector": ...}` before the snapshot
- `screenshot` selector / decode options refused, error names `DHOLE_MULTIMODAL` — the multimodal gate is off. That flag declares the calling agent can see images; adding it is the user's env decision (constraint 9). `decode_qr` alone never needs the flag

### Focus behaves differently than expected

- `neural_focus_model_missing` — `DHOLE_NEURAL_FOCUS=1` is set but the reranker model isn't downloaded; no request was made. Either the user unsets the flag (plain BM25 focus) or downloads once: any neural `smart_search`, or `dhole model use bge-zh`
- A weak-hit note in `next_action` ("best block scored below the intent floor") — the fetch succeeded but the page likely doesn't answer the focus query; `content_ok` stays true by design. Pivot: search for a better source instead of re-reading this page

## Local services

### Unreachable / mysterious 502 on 127.0.0.1

With a proxy running, the HTTP tier's loopback traffic can be intercepted by the proxy — measured: even ports with no listener answer 502 in ~2s (the proxy answering), not connection-refused. `allow_private=true` only satisfies the SSRF guard; it does nothing about proxy interception. Fix: have the user add `127.0.0.1,localhost` to `NO_PROXY` (or pause the proxy). And **never probe local dead ports with `force_fetcher='stealthy'`** — the browser navigation waits out the full 30s timeout; the HTTP tier fails fast instead.

## Install and capability

### Tools missing / behaving oddly

1. `dhole -v`: read the capability panel line by line (browser tier / pdf / rerank / engine yield) — each missing row is a silently degraded tier
2. `dhole --doctor`: item-by-item diagnosis with copy-pasteable fix commands (exit code 1 on failure)
3. Missing browser dependencies auto-degrade to pure HTTP (usable on Termux / slim containers) — anti-bot sites start failing; that is degradation, not a bug

### Version / updates

This machine runs a source-installed personal fork with self-update off: `git pull && python -m pip install -e .` then restart the host. Any change to `env` in the MCP config also needs a host restart.

## Diagnostic command quick reference

| Command | Purpose |
|---|---|
| `dhole -v` | Version + capability panel |
| `dhole --doctor` | Install check; exit code 1 on failure |
| `dhole engines list` | Per-engine verdict, remaining cooldown, active backoff tiers |
| `dhole engines probe` | One real query per engine (hits / latency / block reason) |
| `dhole engines reset` | Clear all cooldowns now |
| `dhole proxy list / add / remove / clear` | Search proxy pool management (list masks credentials) |
| `dhole model` / `dhole model use <name>` | Rerank model: `bge-zh` (default, bilingual, ~279MB) / `zh-full` (cross-lingual ~450MB) / `ms-marco` (English-first ~91MB); recalibrate `min_raw_relevance` after switching |
| `cache_clear(engine_state=true)` | MCP-side equivalent of engines reset (wipes the body cache too) |
| `dhole skill install --force` | re-sync the installed agent skill to the one bundled with this dhole version (skill updates ship with dhole releases) |
