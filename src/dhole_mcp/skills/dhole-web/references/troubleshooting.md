# Troubleshooting Playbook

Ground rule: **read the response fields before acting**. Every failure names itself (`error` / `summary` / `escalation_path` / `metadata`). Identify the layer first (network → site → tool → config), then pick a fix. Fixes come in two tiers: **per-call** (call parameters, use freely) and **process-wide** (env vars — requires editing the host's MCP config and a restart; explain to the user before doing it).

## Search

### Sparse / empty / slow results

1. Read the response: how many engines in `engines_used`? `engines_consensus` x of what? Which of the three lists is long — `engine_blocked` (refused), `engine_empty` (answered with nothing usable), `engine_preempted` (never asked, because enough indexes had already answered)? Only the first two are a degraded pool
2. Retry once as-is **only when `engine_blocked` is the long list** — transient jitter and the cooldowns genuinely clear themselves. Two cases where a retry is a waste: an `engine_empty` name already answered (same query, same nothing → rephrase or change index), and an `engine_preempted` name was never asked (the same call reaches the same stop point → raise `max_results`, or ask it alone with `engines=["<name>"]`; naming it alongside others does not protect it). A third: if `cached=true`, no engine was asked at all — the stored round replays byte-identically until the TTL expires, so change part of the cache identity (`max_results`, `engines=`, the query) or pass `cache_ttl=0`
3. Still thin: CLI `dhole engines list` for each engine's verdict and remaining cooldown; `dhole engines probe` for one real query per engine (hits / latency / block reason)
4. Fix:
   - Per-call: re-search naming live engines (`engines=["baidu","bing","so360"]`)
   - Urgent: `dhole engines reset` or `sys_state(clear=["engine_state"])` to clear cooldowns now
   - Process-wide (only if recurring): a different egress via `DHOLE_SEARCH_PROXY` (one URL; `HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY` are picked up when it is unset), or a keyed backend as fallback
5. Tell the user: "Search engines were rate-limiting me; I re-searched via X. If this keeps happening we can add a search proxy."

### Whole round empty, `fetch_hint` reports error

The entire pool is cooling. Read remaining seconds from `dhole engines list` and decide wait vs reset; probe failures never extend penalties (a heartbeat patrol runs every 300s, probes the cooling engines, and lifts a cooldown on a successful probe).

### The same engine keeps cooling

This IP is being watched. The three cooldown tiers are fixed at 60s / 600s / 600s (blocked / challenge x3 / unreachable x3) — they are not exposed, because the only way to "fix" a watched IP with a longer timer is a deader pool. The real fix is a different egress (`DHOLE_SEARCH_PROXY`) or a keyed engine. There is no knob to set to hours; `dhole engines reset` is the way to drop a penalty early.

### A result you would not trust is ranked first, with `fetch_relevance: high`

Nothing is broken and nothing was filtered: **`fetch_relevance` / `relevance_score` answer "does this page address the query", never "is this site safe or reputable"**, and dhole deliberately keeps no domain allow-list or block-list. A reputation boost was measured out, not overlooked: on the frozen 67-query SERP corpus, removing it left P@5 at 0.373 unchanged, nDCG@10 0.654→0.652 and MRR 0.559→0.555 — all smaller than the movement of one manually re-labelled page — while the price was a list someone has to maintain and a permanent false-positive surface (today's content farm is tomorrow's canonical docs site, and a name on a list disappears from the results with no receipt).

What the row does carry, and what to do with it:

- `source_type` names the *kind* of site (`gov` / `edu` / `github` / `docs-site` / `qa` / `forum` / `blog` / `ecommerce` / `reference` / `paper` / `news` / `unknown`); `unknown` means "not on a known list", not "weak".
- `official` is true only in registry-controlled namespaces — a name that cannot be bought. It answers "who controls this domain", never "is this correct".
- `engines_consensus` is the one reputation-free authority signal: how many independent indexes agree this URL belongs to the query. Read its denominator (`x of N (…)`) first.
- If the decision is genuinely about safety rather than relevance — installing a package, clicking a payment link, running a command the page suggests — pick the source you would cite, `web_read` it directly, or ask the user. Narrowing `engines=[...]` changes which index answered, not whether the page is trustworthy.

## Fetch

### Content is a shell / obviously wrong

1. Check `total_extracted_chars` and `page_type`: a few hundred chars plus a wrong `page_type` = placeholder

   **But a long body is not the opposite answer.** Measured on a live site: 3,559 extracted characters, `status: 200`, every length gate passed — and not one sentence in it, because the page IS a packed JavaScript challenge (`var _0x5ebe1d=[…]`, `arg1="…"`). That shape gets its own verdict now: `error` starts with `obfuscated_payload`, `content_ok=false`, `page_type=js_shell`, and `metadata.obfuscated_body` carries which markers fired. The extractor was faithful — there were no words to lose — so do not re-fetch it hoping for better extraction, and do not count it in a batch's `successful`. Treat it as blocked and go to step 2 or a different source.
2. Retry with `force_fetcher='stealthy'` — **only if `fetcher_used` is not already `stealthy` and `escalation_path` does not say the tier was skipped.** The browser tier is one of three different "cannot help" cases and they take different actions: it was skipped/could not start (`dhole --doctor`), or **it ran and this shell is its answer** — in which case a re-fetch, forced or not, is the same bytes and a longer wait (measured: 31.5s to a timeout, following advice that named the tier that had just answered). `next_action` and `diagnosis.recover_with` both read the itinerary and stop recommending it; do not re-derive the recommendation from the failure name.
3. Still thin: the site needs login (→ "Sites behind login" below) or runs DataDome / Akamai / interactive Turnstile (unbeatable) — **say so honestly**; never answer from the shell
4. Tell the user: "This site blocks plain scraping; what I got is the pre-login shell. For real content I'd need your session cookie, or we use a public source."

### A crawled row is not the page it names

`web_crawl` measures every row with the same rulers `web_read` is calibrated on, and says so in that row's `summary` when the measurement contradicts the verdict:

- `extraction collapsed: … holds 1,374 visible characters while we returned 196 (14%)` — the site DID give text; dhole's main-content filter dropped it. The row is refused (`content_ok=false`) and the sentence is the reason. Nothing to re-fetch for rendering: `web_read` that one URL with `extraction_type='html'` (or `'text'`) and the words come back. The crawl-level `summary` counts how many rows carry it.
- `thin list: 2 item link(s)` — a row was rendered as a link list but only holds one or two entries, which is not an index. Also refused.
- `the same 12 item link(s) as 6 of these rows — that group is site chrome` — those rows' "content" is the site menu. This one is a disclosure only, and it is deliberately not a gate: inside a single page a menu and a list are structurally identical (measured: books.toscrape puts its whole product grid inside `<aside>`, so stripping nav/header/footer/aside leaves 2 links on a page that has 11 books). Treat those rows as navigation and read the pages they point at.

The first two are the same ruling `web_read` already made on those bytes — before this, one URL could come back `content_ok=false` from `web_read` and `content_ok=true` inside a `web_crawl`. A row that says nothing is a row that was measured and matched its verdict.

### `web_crawl` came back with only the start page

Four different runs look identical from `pages_crawled: 1`, and their fixes are mutually wrong, so `summary` names the one that happened and the number it was chosen from:

- `path_include=[…] scoped out every one of the 7 same-domain candidate link(s) (7 rejected, nothing was left queued)` — the scope really did it. Change the pattern (a pattern names a path subtree; `'/docs'` does not match `/docs-old`).
- `path_include=[…] is NOT the reason: max_depth=0 does no link discovery` — you asked for one page; the scope never ran on anything. Raise `max_depth`.
- `is NOT the reason: no page returned HTML to parse for links` — the fetch itself failed; read that row's `status` and `error`.
- `the HTML we parsed held 0 same-domain candidate links, so the scope never had anything to filter` — the page genuinely hands dhole no links to work with (script-rendered nav, or links that point off-domain). `web_read` it with `extraction_type='html'` to see the markup, or start from a page that carries links.
- `kept all 3 candidate link(s)` — the scope admitted them and they were dropped as already-known or by robots (a `N URL(s) skipped by robots.txt` bit appears when that is why).

Counts are unique candidates per normalized URL, so a menu that repeats one link 40 times is reported as 1, not 40.

### `nested_no_match` / `nested_empty` / fewer records than the page has

Both are failures to fix, not empty answers to report — and **no second-layer request was sent** for the first one, so retrying costs nothing until the selector changes.

- `nested_no_match` (0 usable hrefs): either the selector names something that doesn't carry links, or the page is a JS shell. **Read the tail of `next_action` first** — it names the link groups that are actually on the page (`Link groups actually on this page: a.tag (36 links, e.g. "change") | .quote (34 links…)`), counted the same way the fan-out counts (absolute, deduplicated), so the next call can carry a selector that is known to resolve. No group named means no repeated link exists in that HTML — that is the shell case, not a selector problem. Widen to the element that CONTAINS the `<a>` (`.card`, not `.card a`) — the href is read from the matched element, its first descendant `<a>`, or its nearest ancestor `<a>`. If `total_extracted_chars` is a few hundred and `page_type` is `shell`/`unknown`, no selector will help: re-call with `force_fetcher='stealthy'`.
- `nested_empty` (every second-layer page returned no field): `nested_fields` doesn't match that site's markup. Read ONE of those pages without `nested_*` (`css_selector` or a screenshot of the structure) and re-derive the selectors; `type: "array"` on a field that is a list on the page (a topic page's 20 works) or you get its first item.
- Fewer records than the page has: look for `truncation=nested_budget` in `summary` — the default expands 8 and the ceiling is 30, and that phrase is the one that raising `nested_max` actually fixes. A note like `links: 10 elements matched → 8 unique` is **deduplication** (the same author linked twice), so raising the cap recovers nothing. Say the number you got rather than reporting "all". Past 30, the job is `web_crawl`.
- A record carrying `"error": "robots_disallowed"`: that item was never requested (each second-layer link passes its own robots check); `robots_skipped` in `summary` counts them.

### `robots_disallowed`

Default compliance: **zero requests** were sent for that URL. A one-shot waiver is `ignore_robots=true` — the user's compliance decision, ask first. Process-wide: `DHOLE_IGNORE_ROBOTS=1`. Whether robots are honored right now: read the `metadata.robots` field on every response (it says `bypassed` when process-wide off). When robots.txt itself is unfetchable (5xx / timeout) the tool proceeds and retries at a 60s short TTL — don't read "couldn't check" as "compliance passed".

### Every public URL flagged as internal

A fake-IP TUN proxy (Clash / sing-box TUN mode) hands every public name an address in `198.18.0.0/15` / a `fc00::/7` AAAA, which the DNS recheck would read as internal. **This is detected — and what it changes is those two ranges, not the layer**: the recheck still runs on a tunnel host and still refuses loopback, RFC1918, link-local and metadata answers by hostname. The verdict needs two independently registered probe names to agree, so one poisoned or hosts-pinned name cannot relax the machine. The posture travels in `ssrf_recheck` (under `metadata` on a single fetch; once on the envelope of a `urls=[...]` batch or a crawl, because it is a fact about the process rather than about any one page) and `dhole --doctor` names the address and the network. If URLs are still being flagged internal, the proxy came online **after** this process first asked — and that now heals itself: a refusal of one of those two ranges re-probes the machine first (at most once per 60s), so call again a minute later. `dhole --doctor` names which process holds which verdict and when it measured. A restart re-judges immediately but is no longer the only way out. The refusal says which case it is — `diagnosis.code = "resolver_guard"`, `retryable: false`. Plain HTTP proxies are unaffected.

### The request carries headers you never set

Measured against a local site that prints back what arrived: one ordinary `web_read` with `anti_detect` unset puts **15** headers on the wire and none of them is yours — a generated `User-Agent` with its `sec-ch-ua` family, `accept`, `accept-encoding`, `priority`, and a `referer: https://www.google.com/` that dhole invents, because a navigation with no referrer is itself a tell that some walls score. Anything in that set can be named and overridden with `headers=` (the values you set win key by key, `sec-fetch-site` and `accept-language` included); the whole set is switched off with `anti_detect='off'`, which leaves 14 headers, all of them the client library's own, and no referrer. `summary` says which of those two ran, on the call that actually used the HTTP tier.

Three limits live under dhole and are written here instead of pretended away:

- **The synthesized set is not a self-consistent browser.** Measured over 16 requests from one process at the default rung: **13** carried `sec-fetch-site: "?1"` — the value real Chrome reserves for `Sec-Fetch-User` (its own `Sec-Fetch-Site` is `none` / `same-origin` / `cross-site`) — and **3** paired a phone `User-Agent` with `sec-ch-ua-platform: "Linux"`. All 16 carried the invented `referer`. So dhole's fingerprint is not one coherent machine, and that is a stated limitation rather than a claim: the ladder impersonates *a* browser, not *your* browser. A site that cross-checks those fields can score it, and no header you set fixes the fields you did not set — `anti_detect='off'` is the only setting that sends a set drawn from one place.
- **Two names come from the compiled impersonation layer beneath dhole's header dict** — `sec-purpose: prefetch;prerender` and `upgrade-insecure-requests`. Both step aside for a name you set yourself (measured: `headers=` with `sec-purpose` arrives as written). What no parameter reaches is that layer's own choice of which names to add when you named none.
- **`anti_detect='off'` can make a wall start failing.** That invented referrer is load-bearing on sites that pass today. When a site that used to answer begins to answer with a challenge, `summary` saying `no dhole-synthesized headers` is the evidence for what changed — go back to `auto` (or `aggressive`), do not go looking at the site.

### Fetch timeouts

`timeout` is the wall-clock budget for the whole call (default 30s, max 120s) and the HTTP tier's attempts share it — but **one attempt never exceeds 30s**, whatever you set. So a bigger `timeout=` buys more attempts, not a longer single wait: `timeout_applied` is the receipt that says the ceiling bit, and `summary` says the same in words. This is what a big file runs into — a document download is ONE attempt (a retry restarts it from byte zero), so a file that cannot arrive inside 30s of one connection is not fetchable over HTTP at any `timeout=`. When that happens the advice names the levers that do exist (another source, or fetch the file outside dhole and hand it to `doc_parse`); it does not tell you to raise a number that cannot move. The 50MB body cap gates BEFORE download; oversized bodies fail with `Response body too large` and raising `timeout` won't help.

### The summary says this call "ran on its own browser"

The rung you asked for is not what the shared browser was started with: canvas noise and the WebRTC policy are **launch-level**, so a call wanting `anti_detect='off'` while the warm browser is an `auto` one takes its own browser for that call. Slower by one cold start, and **without that browser's page state** — a named session's logins do not ride along. Not an error: the content came from the rung you asked for. To stop paying it, store the rung you actually want (`cred_defaults` `set={"anti_detect": "off"}`), which replaces the warm browser once. `basic` / `auto` / `aggressive` share one launch identity and never hit this.

### `proxy_unreachable`

`proxy_unreachable` means the stored `proxy` failed its 5s TCP precheck — no request was sent (no browser escalation, no snapshot fallback). Raising `timeout` is useless: check the proxy's address / port / liveness, or `unset=["proxy"]` for a direct connection.

### `dns_unresolvable`, `private_target_blocked`, and a name that resolved on the second try

A name that will not resolve arrives under one of two different labels, and they owe you different actions. The label is the token inside `error` (`network_error: <label> …`); `diagnosis.code` for this whole family is `network` — branch on `error`, not on a per-label `code`:

- **`dns_unresolvable`** — the cheap TCP probe dialled the name **twice, 150ms apart**, and neither answered. That is the strongest claim a 2-second socket is allowed to make, and it is still not the last word: the probe no longer has the power to skip the fetch tiers (see below), so `escalation_path` shows what HTTP and the browser each said. Fix the name or the resolver — resending the same URL changes neither. `retryable: false`.
- **`dns_failure`** — a fetch tier's own message (`net::ERR_NAME_NOT_RESOLVED`, `getaddrinfo failed`). Same advice; different author.
- **`private_target_blocked`** — nothing was dialled and no DNS was ever asked: the address is private / loopback / metadata and the waiver does not cover it. `Verify the URL is correct` would be a false errand here — the fix is `cred_defaults` `allow_private` naming that host, or the public URL. `retryable: false`, because a policy does not move on its own.
- **A note in `summary` saying the name failed on the first probe and resolved on the second** — the fetch went ahead normally. This machine's resolver is flaky for that name. It is reported because a fault absorbed inside one call is otherwise indistinguishable from a clean run, and it is the reason an earlier round of this tool told you `do NOT retry` about a site that answered 1.6 seconds later.

The TCP probe used to be able to end a call by skipping both fetch tiers on `connection_refused` and on DNS failures for **any** host. It does not anymore: on a public target a refused connection can come from a middlebox that answers first and fails after, and the tiers may route differently than a bare socket. The probe keeps that power only for **private / loopback** targets, where refused and timeout both mean "nothing is listening" and the browser would dial the same dead port (measured: 45s burned doing exactly that).

### Stale content / someone else's snapshot

When `source` shows an archive fallback, that is a dated snapshot (`archived_at`) — and the one-line `summary` says so too: `archived copy, <snapshot date> (via http→stealthy→archive.org)`. Do not answer from it as if it were today: label citations with the date, and check `metadata.snapshot_url` when you need to verify which copy you read. A relative `canonical` on the snapshot resolves to the archive host, so it is dropped and the drop is announced — `url` stays the page you asked for. Two guarantees: the server's own JSON/XML error bodies are never replaced by snapshots (4xx/5xx with json/xml content-type are preserved as-is); an unreachable proxy never falls back to a snapshot (the request never went out — answering from a snapshot would answer a question nobody asked).

### 401 / credential problems

The response distinguishes "credentials rejected" (store a fresh token) from "no credentials sent" (store `auth` for that host with `cred_defaults`) — the fixes are opposite, so read which one it is. Also check: did a cross-origin redirect strip the credentials (same-host hops keep them, cross-domain hops strip them)? Expired tokens are simply replaced — the tool runs no refresh flow.

### Mojibake

`doc_parse` with explicit `encoding=`. **Shift_JIS / EUC-KR decode into plausible-looking but wrong Chinese** and sit outside auto-detection — when the Chinese reads fluently and impossibly, suspect this first.

### Sites behind login

Four paths, cheapest first: (1) HTTP tier — credentials stored once with `cred_defaults` (`cookies` or `auth`: basic/bearer/header, each with its `host`; pass a `session_id` to keep the browser jar). (2) The **no-password browser fallback**: a named `session_id` bridges the jar into that session's dedicated stealthy browser (and harvests back), so HTTP-tier login state rides browser calls; combine with `check_login` + snapshot actions + gated submit to operate the site without ever touching a password. (3) CDP attach — `web_read` with `cdp_url` (+ `check_login=true`) runs the fetch inside the user's real logged-in Chrome; requires one-time user setup (configuration.md "CDP attach"). (4) Still impossible: DataDome / Akamai / interactive Turnstile walls and 2FA — none of these paths defeats challenges; say so honestly.

### CDP attach fails

- `cdp_authorization_required` — this is the FIRST attach **for this target host** (the site being read, not the Chrome debug endpoint), so nothing was executed. Attaching hands over the authenticated session of every site the user is logged into, and dhole asks once: present it as the user's decision, and only on a clear yes re-call with `confirm=true` (the approval persists to `~/.dhole/cdp_hosts.json`, so later attaches need no ask). Retrying without the user's yes changes nothing, and no parameter widens the fence
- `cdp_host_not_allowed` — the host is not in the authorized set, and the match is an **exact hostname**: `example.com` authorized does not let `www.example.com` through, and neither does it cover a CDN the page loads assets from. With the user's yes, attach to that host with `confirm=true`; never work around by fetching a different host with the user's login state. It also arrives when an attached page **tried to leave** the fence on its own (a redirect, a script-driven navigation): the out-of-fence page's content is never handed back as the answer you asked for. The refusal itself says which side you are on — "ended before it left the browser" means no request reached that host, "already left the browser" means it did but nothing was read back. Either way, read the host it names as the one the page was aiming at, and authorize it on purpose only if that is what the user wanted
- `cdp_no_logged_in_context` — the endpoint answered but exposed **no browser context**, so there was no logged-in profile to ride and **nothing was fetched**. dhole refuses instead of quietly creating an isolated context: an anonymous page fetched inside your Chrome looks exactly like your logged-in view except for one absent field. Re-call the same url once (a just-started endpoint can report its contexts late); if it repeats, that `cdp_url` is not the window holding the logins — the user starts the signed-in profile with `--user-data-dir` + `--remote-debugging-port`
- `cdp_endpoint_unreachable` — the endpoint did not answer at all, so **nothing was fetched and no cookie was read**. Remote debugging lives and dies with the browser process: a bigger `timeout` fixes nothing here. The browser holding the logins (Chrome **or** Edge) has to be running with `--remote-debugging-port=<that port>` plus the `--user-data-dir` that carries those logins; then re-call the same url. If the endpoint moved, `cdp_url=` is the thing to change - hosts approved earlier stay authorized
- `login_check.logged_in: false` — session expired in the attached Chrome; the user re-logs in their window, then re-run the call
- `browser_busy` — `headed=true` asked for a **visible** window while a hidden browser was already running on that profile, so **nothing was fetched**. dhole will not take that browser over (a profile appearing on screen is the user's decision) and will not close it (those tabs may be the ones they are standing in). Two honest ways out, both the user's call: hand the window back first (`sys_state()` → `sys_state(clear=['<session id>'])`) and re-ask, or drop `headed=true` and let this call ride the browser that is up — a visible one serving hidden work is normal, the other direction is what this error is
- `logged_in: null` — the page's DOM shape wasn't enough to decide (pure-JSON endpoints, odd layouts); read `signals` and say what's uncertain instead of guessing

### Page-action failures (snapshot / index actions / submit)

- `pending_approval: true` in `metadata.actions` — not a failure: the submit PAUSED before acting, nothing was sent. Describe the submission to the user; on a clear yes re-run the **whole chain** with `confirm: true` on that submit (each call reloads the page, so prior fill/click steps must be redone)
- An error naming the element index ("beyond this page's N interactive elements", or an `expect` mismatch reporting what the element reads now) — the page changed under you. Re-run `{"snapshot": true}` and re-address; don't retry the stale index
- The snapshot came back empty / `could not enumerate` — the document was still loading; add `{"wait_selector": ...}` before the snapshot
- `element_screenshot_disabled` — retired (element captures need no permission). Seeing it means the installed dhole is too old; say so instead of editing env

### Focus behaves differently than expected

- `neural_focus_model_missing` — `focus_engine='neural'` was asked but the reranker model isn't downloaded; no request was made. For this call pass `focus_engine='bm25'` (plain keyword focus, no model), or let the model download once: any neural `web_search` (one-time, resumable) or let a neural `web_search` fetch it
- `focus_engine_not_permitted` — retired (neural focus is a per-call choice now). Seeing it means the installed dhole is too old
- A weak-hit note in `next_action` ("best block scored below the intent floor") — the fetch succeeded but the page likely doesn't answer the focus query; `content_ok` stays true by design. Pivot: search for a better source instead of re-reading this page

- **`all_tiers_failed: unknown (HTTP status 0)`** — no tier ever received an HTTP response, so this is reachability, not a bot wall (a wall answers with a status and a challenge page). Check the URL spelling and DNS first; do not retry the same URL. A `198.18.x.x` address in the error text means the fake-IP TUN proxy could not reach the real network

## Local services

### Unreachable / mysterious 502 on 127.0.0.1

With a proxy running, the HTTP tier's loopback traffic can be intercepted by the proxy — measured: even ports with no listener answer 502 in ~2s (the proxy answering), not connection-refused. `cred_defaults` `allow_private` only satisfies the SSRF guard; it does nothing about proxy interception. Fix: have the user add `127.0.0.1,localhost` to `NO_PROXY` (or pause the proxy). And **never probe local dead ports with `force_fetcher='stealthy'`** — the browser navigation waits out the full 30s timeout; the HTTP tier fails fast instead.

## Install and capability

### Tools missing / behaving oddly

1. `dhole -v`: read the capability panel line by line (browser tier / pdf / rerank / engine yield) — each missing row is a silently degraded tier
2. `dhole --doctor`: the 12-item diagnosis with copy-pasteable fix commands (exit code 1 on failure). It really spawns a server, really launches the browser once (`chromium launch` is the only row that proves the binary on disk can start), and really runs one search + one fetch, so it takes a few seconds and sends traffic — say so before running it on someone's machine. Its `live search` / `live fetch` rows are the only evidence that this IP works today; a failure there is a network fact, not an install fault
3. Missing browser dependencies auto-degrade to pure HTTP (usable on Termux / slim containers) — anti-bot sites start failing; that is degradation, not a bug

### Version / updates

This machine runs a source-installed personal fork with self-update off: `git pull && python -m pip install -e .` then restart the host. Any change to `env` in the MCP config also needs a host restart.

- A code update reaches the **server** at that restart, but the **host may keep answering `tools/list` from its own cache**: a parameter added in this version can read as "does not exist" when you inspect the schema and still work when you call it. Behaviour is the authority - make the call. An unknown name is refused loudly and lists what that tool actually takes, so one probe settles it; concluding "this dhole lacks the capability" from a cached schema is how a working door gets walked past.

## Diagnostic command quick reference

| Command | Purpose |
|---|---|
| `dhole -v` | Version + capability panel |
| `dhole --doctor` | The 12-item install check (incl. one real browser launch + one real search + one real fetch); exit code 1 on failure |
| `dhole engines list` | Per-engine verdict, remaining cooldown, active backoff tiers |
| `dhole engines probe` | One real query per engine (hits / latency / block reason) |
| `dhole engines reset` | Clear all cooldowns now |
| `sys_state(clear=["engine_state"])` | MCP-side equivalent of engines reset, and the one that reaches a RUNNING server (it touches no cache) |
| `dhole skill install --force` | re-sync the installed agent skill to the one bundled with this dhole version (skill updates ship with dhole releases) |
