# Troubleshooting Playbook

Ground rule: **read the response fields before acting**. Every failure names itself (`error` / `summary` / `escalation_path` / `metadata`). Identify the layer first (network → site → tool → config), then pick a fix. Fixes come in two tiers: **per-call** (call parameters, use freely) and **process-wide** (env vars — requires editing the host's MCP config and a restart; explain to the user before doing it).

## Search

### Sparse / empty / slow results

1. Read the response: how many engines in `engines_used`? `engines_consensus` x of what? A long `engine_preempted` / `engine_empty` list? — the pool is degraded (rate-limited / blocked / cooling)
2. Retry once as-is (transient jitter is common)
3. Still thin: CLI `dhole engines list` for each engine's verdict and remaining cooldown; `dhole engines probe` for one real query per engine (hits / latency / block reason)
4. Fix:
   - Per-call: re-search naming live engines (`engines=["baidu","bing","so360"]`)
   - Urgent: `dhole engines reset` or `sys_state(clear=["engine_state"])` to clear cooldowns now
   - Process-wide (only if recurring): a different egress via `DHOLE_SEARCH_PROXY` (one URL; `HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY` are picked up when it is unset), or a keyed backend as fallback
5. Tell the user: "Search engines were rate-limiting me; I re-searched via X. If this keeps happening we can add a search proxy."

### Whole round empty, `fetch_hint` reports error

The entire pool is cooling. Read remaining seconds from `dhole engines list` and decide wait vs reset; probe failures never extend penalties (a heartbeat patrol runs every 300s, probes the cooling engines, and lifts a cooldown on a successful probe).

### The same engine keeps cooling

This IP is being watched. The three cooldown tiers are fixed at 60s / 600s / 600s (blocked / challenge x3 / unreachable x3) — 17.0 does not expose them, because the only way to "fix" a watched IP with a longer timer is a deader pool. The real fix is a different egress (`DHOLE_SEARCH_PROXY`) or a keyed engine. There is no knob to set to hours; `dhole engines reset` is the way to drop a penalty early.

## Fetch

### Content is a shell / obviously wrong

1. Check `total_extracted_chars` and `page_type`: a few hundred chars plus a wrong `page_type` = placeholder
2. Retry with `force_fetcher='stealthy'`
3. Still thin: the site needs login (→ "Sites behind login" below) or runs DataDome / Akamai / interactive Turnstile (unbeatable) — **say so honestly**; never answer from the shell
4. Tell the user: "This site blocks plain scraping; what I got is the pre-login shell. For real content I'd need your session cookie, or we use a public source."

### `nested_no_match` / `nested_empty` / fewer records than the page has

Both are failures to fix, not empty answers to report — and **no second-layer request was sent** for the first one, so retrying costs nothing until the selector changes.

- `nested_no_match` (0 usable hrefs): either the selector names something that doesn't carry links, or the page is a JS shell. Widen it to the element that CONTAINS the `<a>` (`.card`, not `.card a`) — the href is read from the matched element, its first descendant `<a>`, or its nearest ancestor `<a>`. If `total_extracted_chars` is a few hundred and `page_type` is `shell`/`unknown`, no selector will help: re-call with `force_fetcher='stealthy'`.
- `nested_empty` (every second-layer page returned no field): `nested_fields` doesn't match that site's markup. Read ONE of those pages without `nested_*` (`css_selector` or a screenshot of the structure) and re-derive the selectors; `type: "array"` on a field that is a list on the page (a topic page's 20 works) or you get its first item.
- Fewer records than the page has: look for `truncation=nested_budget` in `summary` — the default expands 8 and the ceiling is 30, and that phrase is the one that raising `nested_max` actually fixes. A note like `links: 10 elements matched → 8 unique` is **deduplication** (the same author linked twice), so raising the cap recovers nothing. Say the number you got rather than reporting "all". Past 30, the job is `web_crawl`.
- A record carrying `"error": "robots_disallowed"`: that item was never requested (each second-layer link passes its own robots check); `robots_skipped` in `summary` counts them.

### `robots_disallowed`

Default compliance: **zero requests** were sent for that URL. A one-shot waiver is `ignore_robots=true` — the user's compliance decision, ask first. Process-wide: `DHOLE_IGNORE_ROBOTS=1`. Whether robots are honored right now: read the `metadata.robots` field on every response (it says `bypassed` when process-wide off). When robots.txt itself is unfetchable (5xx / timeout) the tool proceeds and retries at a 60s short TTL — don't read "couldn't check" as "compliance passed".

### Every public URL flagged as internal

A fake-IP TUN proxy (Clash / sing-box TUN mode) hands every public name an address in `198.18.0.0/15` / a `fc00::/7` AAAA, which the DNS recheck reads as internal. **This is detected and the recheck layer skips itself** — the response then says so in `metadata.ssrf_recheck` and `dhole --doctor` names the address and the network. If URLs are still being flagged internal, the probe missed an unusual resolver that fake-ips only some names: have the user restart dhole once so the startup probe re-judges — 17.0 has no manual override (the knob is retired, and the only historical reason to want one IS the fake-ip case) — and re-check with `dhole --doctor`. Plain HTTP proxies are unaffected.

### Fetch timeouts

`timeout` is the wall-clock budget for the whole call (default 30s, max 120s); document **download** time counts fully — for big PDFs raise `timeout`. The 50MB body cap gates BEFORE download; oversized bodies fail with `Response body too large` and raising `timeout` won't help.

### The summary says this call "ran on its own browser"

The rung you asked for is not what the shared browser was started with: canvas noise and the WebRTC policy are **launch-level**, so a call wanting `anti_detect='off'` while the warm browser is an `auto` one takes its own browser for that call. Slower by one cold start, and **without that browser's page state** — a named session's logins do not ride along. Not an error: the content came from the rung you asked for. To stop paying it, store the rung you actually want (`cred_defaults` `set={"anti_detect": "off"}`), which replaces the warm browser once. `basic` / `auto` / `aggressive` share one launch identity and never hit this.

### `proxy_unreachable`

`proxy_unreachable` means the stored `proxy` failed its 5s TCP precheck — no request was sent (no browser escalation, no snapshot fallback). Raising `timeout` is useless: check the proxy's address / port / liveness, or `unset=["proxy"]` for a direct connection.

### Stale content / someone else's snapshot

When `source` shows an archive fallback, that is a dated snapshot (`archived_at`). Label citations with the date; proactively flag time-sensitive claims. Two guarantees: the server's own JSON/XML error bodies are never replaced by snapshots (4xx/5xx with json/xml content-type are preserved as-is); an unreachable proxy never falls back to a snapshot (the request never went out — answering from a snapshot would answer a question nobody asked).

### 401 / credential problems

The response distinguishes "credentials rejected" (store a fresh token) from "no credentials sent" (store `auth` for that host with `cred_defaults`) — the fixes are opposite, so read which one it is. Also check: did a cross-origin redirect strip the credentials (same-host hops keep them, cross-domain hops strip them)? Expired tokens are simply replaced — the tool runs no refresh flow.

### Mojibake

`doc_parse` with explicit `encoding=`. **Shift_JIS / EUC-KR decode into plausible-looking but wrong Chinese** and sit outside auto-detection — when the Chinese reads fluently and impossibly, suspect this first.

### Sites behind login

Five paths, cheapest first: (1) HTTP tier — credentials stored once with `cred_defaults` (`cookies` or `auth`: basic/bearer/header, each with its `host`; pass a `session_id` to keep the browser jar). (2) The **no-password browser fallback (17.0)**: a named `session_id` bridges the jar into that session's dedicated stealthy browser (and harvests back), so HTTP-tier login state rides browser calls; combine with `check_login` + snapshot actions + gated submit to operate the site without ever touching a password. (3) **Stored-credential login (17.0)** — `cred_vault(op='match')` then `cred_login`; see "Stored-credential login" below. (4) CDP attach (17.0) — `web_read` with `cdp_url` (+ `check_login=true`) runs the fetch inside the user's real logged-in Chrome; requires one-time user setup (configuration.md "CDP attach"). (5) Still impossible: DataDome / Akamai / interactive Turnstile walls and 2FA — none of these paths defeats challenges; say so honestly.

### Stored-credential login (cred_vault / login) fails

- **`cred_vault` / `cred_login` not in `tools/list`** — 17.0 registers them by default, so this means the operator set an explicit `DHOLE_TOOLS` subset that excludes them. Widening it is the user's edit, not a workaround
- **`cred_vault` `notes` says a manager is not installed** — the `bw` / `op` CLI is missing. Relay the note's install step; this is a note, not an error, and the other manager may still work
- **`notes` says the vault is locked / not signed in** — the user must unlock it (`bw unlock` / `op signin`). Never ask for a master password
- **`cred_login` error: no password field found** — the page did not render a `type=password` input (a two-step email-then-password flow, a JS gate, or the page never settled). Pass `username_index` / `password_index` / `submit_index` from a snapshot, or raise `timeout`; if the form is genuinely multi-page, do the steps with `actions` instead
- **`cred_login` returned `pending_approval: true`** — not a failure: nothing was filled or submitted. Show the user `plan` + `fields`; re-call the **same arguments** with `confirm=true` only on their yes
- **`cred_login` `logged_in: false`** — the credential was submitted but the session did not take: wrong item, or the site wants 2FA. Check `signals`; do not retry blindly (a second wrong submit can lock the account). `logged_in: null` = the probe could not answer — not "failed"
- **`cred_login` ran but later fetches are anonymous** — pass the **same `session_id`** to those fetches; the cookies were kept under it. `sys_state` shows what is held

### Recipe / web_watch misbehave

- **`web_recipe` / `web_watch` not in `tools/list`** — same as cred_vault/cred_login above: only an explicit `DHOLE_TOOLS` subset removes them in 17.0
- **`web_recipe(op='run')` with `actions_failed` > 0** — the page changed under the chain. Re-snapshot, re-derive the steps, `op='save'` again. An `op='save'` that is *rejected* means the chain is invalid (validation runs at save time) — fix the chain, don't retry the save
- **`web_watch(op='check')` returns `changed: null`** — that is the FIRST check storing the baseline, not an error and not "no change". The next check answers `true`/`false`
- **`web_watch` `error` set, `changed` absent** — the fetch failed; the baseline is **untouched** (by design, so an outage cannot silently re-baseline). Fix the fetch and re-check
- **`web_watch` keeps reporting noise** — the page's chrome changes every load; re-add with `focus=` to diff only the relevant block, or raise `max_chars` if `baseline_cut` is true

Attach-specific failures:
- `cdp_authorization_required` — this is the FIRST attach to this host, so nothing was executed. Attaching hands over the authenticated session of every site the user is logged into, and dhole asks once: present it as the user's decision, and only on a clear yes re-call with `confirm=true` (the approval persists to `~/.dhole/cdp_hosts.json`, so later attaches need no ask). Retrying without the user's yes changes nothing
- `cdp_host_not_allowed` — the host is not in the authorized set, and the match is an **exact hostname**: `example.com` authorized does not let `www.example.com` through, and neither does it cover a CDN the page loads assets from. With the user's yes, attach to that host with `confirm=true`; never work around by fetching a different host with the user's login state
- `login_check.logged_in: false` — session expired in the attached Chrome; the user re-logs in their window, then re-run the call
- `logged_in: null` — the page's DOM shape wasn't enough to decide (pure-JSON endpoints, odd layouts); read `signals` and say what's uncertain instead of guessing

### Page-action failures (snapshot / index actions / submit)

- `pending_approval: true` in `metadata.actions` — not a failure: the submit PAUSED before acting, nothing was sent. Describe the submission to the user; on a clear yes re-run the **whole chain** with `confirm: true` on that submit (each call reloads the page, so prior fill/click steps must be redone)
- An error naming the element index ("beyond this page's N interactive elements", or an `expect` mismatch reporting what the element reads now) — the page changed under you. Re-run `{"snapshot": true}` and re-address; don't retry the stale index
- The snapshot came back empty / `could not enumerate` — the document was still loading; add `{"wait_selector": ...}` before the snapshot
- `element_screenshot_disabled` — 17.0 removed this error (element captures need no permission). Seeing it means the installed dhole predates 17.0; say so instead of editing env

### Focus behaves differently than expected

- `neural_focus_model_missing` — `focus_engine='neural'` was asked but the reranker model isn't downloaded; no request was made. For this call pass `focus_engine='bm25'` (plain keyword focus, no model), or let the model download once: any neural `web_search` (one-time, resumable) or let a neural `web_search` fetch it
- `focus_engine_not_permitted` — 17.0 removed this error (neural focus is a per-call choice now, no license). Seeing it means the installed dhole predates 17.0
- A weak-hit note in `next_action` ("best block scored below the intent floor") — the fetch succeeded but the page likely doesn't answer the focus query; `content_ok` stays true by design. Pivot: search for a better source instead of re-reading this page

## Local services

### Unreachable / mysterious 502 on 127.0.0.1

With a proxy running, the HTTP tier's loopback traffic can be intercepted by the proxy — measured: even ports with no listener answer 502 in ~2s (the proxy answering), not connection-refused. `cred_defaults` `allow_private` only satisfies the SSRF guard; it does nothing about proxy interception. Fix: have the user add `127.0.0.1,localhost` to `NO_PROXY` (or pause the proxy). And **never probe local dead ports with `force_fetcher='stealthy'`** — the browser navigation waits out the full 30s timeout; the HTTP tier fails fast instead.

## Install and capability

### Tools missing / behaving oddly

1. `dhole -v`: read the capability panel line by line (browser tier / pdf / rerank / engine yield) — each missing row is a silently degraded tier
2. `dhole --doctor`: the 12-item diagnosis with copy-pasteable fix commands (exit code 1 on failure). It really spawns a server and really runs one search + one fetch, so it takes a few seconds and sends traffic — say so before running it on someone's machine. Its `live search` / `live fetch` rows are the only evidence that this IP works today; a failure there is a network fact, not an install fault
3. Missing browser dependencies auto-degrade to pure HTTP (usable on Termux / slim containers) — anti-bot sites start failing; that is degradation, not a bug

### Version / updates

This machine runs a source-installed personal fork with self-update off: `git pull && python -m pip install -e .` then restart the host. Any change to `env` in the MCP config also needs a host restart.

## Diagnostic command quick reference

| Command | Purpose |
|---|---|
| `dhole -v` | Version + capability panel |
| `dhole --doctor` | The 12-item install check (incl. one real search + one real fetch); exit code 1 on failure |
| `dhole engines list` | Per-engine verdict, remaining cooldown, active backoff tiers |
| `dhole engines probe` | One real query per engine (hits / latency / block reason) |
| `dhole engines reset` | Clear all cooldowns now |
| `sys_state(clear=["engine_state"])` | MCP-side equivalent of engines reset, and the one that reaches a RUNNING server (it touches no cache) |
| `dhole skill install --force` | re-sync the installed agent skill to the one bundled with this dhole version (skill updates ship with dhole releases) |
