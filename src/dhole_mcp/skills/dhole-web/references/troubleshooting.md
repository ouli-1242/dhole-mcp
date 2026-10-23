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

### `extraction_collapsed` — the words are in the HTML, dhole's reader lost them

`error` opens with `extraction_collapsed` (and `diagnosis.code` says it) with both numbers in the sentence: how many characters the page's own markup holds vs. how many came back. That makes it a verdict about **dhole's reader**, not about the site — so the levers that fix a shell fix nothing here, and a collapsed page re-fetched the same way returns the same short body. What actually changes the answer:

- `extraction_type='html'` — skips the reader and hands the markup back as it is. This is the door.
- `schema=` — the other door: it extracts named fields without the reader.
- `extraction_type='text'` / `'markdown'` / `'article'` — **not doors.** Those three values all run through the reader that just lost the text (measured: a collapsed page re-fetched with `'text'` came back with 527 of the page's 1,998 characters, the same verdict and the same advice). Switching among them re-measures one failure and costs a round trip.
- `force_fetcher='stealthy'` — not a door either: the browser hands this same markup to this same reader.

### A crawled row is not the page it names

`web_crawl` measures every row with the same rulers `web_read` is calibrated on, and says so in that row's `summary` when the measurement contradicts the verdict:

- `extraction collapsed: … holds 1,374 visible characters while we returned 196 (14%)` — the site DID give text; dhole's main-content filter dropped it. The row is refused (`content_ok=false`) and the sentence is the reason. Nothing to re-fetch for rendering: `web_read` that one URL with `extraction_type='html'` and the words come back. The crawl-level `summary` counts how many rows carry it.
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

- `nested_no_match` (no link to expand): `error` tells you WHICH of the two it was — `matched 0 elements on this page - it names nothing here` (the selector is not a thing on this page) vs `matched N element(s) … none of them carries a usable http(s) link` (the selector found elements, and the href is one level up or down from them). Either way, **read the tail of `next_action` first** — it names the link groups that are actually on the page (`Link groups actually on this page: a.tag (36 links, e.g. "change") | .quote (34 links…)`), counted the same way the fan-out counts (absolute, deduplicated), so the next call can carry a selector that is known to resolve. No group named means no repeated link exists in that HTML — that is the shell case, not a selector problem. Widen to the element that CONTAINS the `<a>` (`.card`, not `.card a`) — the href is read from the matched element, its first descendant `<a>`, or its nearest ancestor `<a>`. If `total_extracted_chars` is a few hundred and `page_type` is `shell`/`unknown`, no selector will help: re-call with `force_fetcher='stealthy'`.
- `nested_empty` (every second-layer page returned no field): `nested_fields` doesn't match that site's markup. Read ONE of those pages without `nested_*` (`css_selector` or a screenshot of the structure) and re-derive the selectors; `type: "array"` on a field that is a list on the page (a topic page's 20 works) or you get its first item.
- Fewer records than the page has: look for `truncation=nested_budget` in `summary` — the default expands 8 and the ceiling is 30, and that phrase is the one that raising `nested_max` actually fixes. A note like `links: 10 elements matched → 8 unique` is **deduplication** (the same author linked twice), so raising the cap recovers nothing. Say the number you got rather than reporting "all". Past 30, the job is `web_crawl`.
- A record carrying `"error": "robots_disallowed"`: that item was never requested (each second-layer link passes its own robots check); `robots_skipped` in `summary` counts them.

### `robots_disallowed`

Default compliance: **zero requests** were sent for that URL. A one-shot waiver is `ignore_robots=true` — the user's compliance decision, ask first. Process-wide: `DHOLE_IGNORE_ROBOTS=1`. Whether robots are honored right now: read the `metadata.robots` field on every response (it says `bypassed` when process-wide off). When robots.txt itself is unfetchable (5xx / timeout) the tool proceeds and retries at a 60s short TTL — don't read "couldn't check" as "compliance passed".

### `Only http and https URLs are supported, got: …`

The characters after `got:` are **your own argument, quoted back** — bounded at 120 characters with the rest named (`…[N more chars not quoted]`), key-shaped runs replaced (`[API_KEY_REDACTED]`), and control characters escaped so nothing inside a refusal can forge a line. Two shapes mean two different things, and both say which:

- `no scheme in 'example.com/page'` — there was no scheme to read. A bare host, a protocol-relative `//host/x`, a path, or a sentence (anaphora like "the latest one from before" arrives here too, because the tool has no memory of previous calls). This is the case where the message used to end after the colon, which reads like the argument never got through.
- `'localhost' as read from 'localhost:8399/x'` — the parser took a leading `something:` for a scheme, which is exactly what a host-and-port written without `//` looks like to it. That sentence is about the parse, not a verdict on your string.

What to do is `next_action`'s sentence either way (`web_read takes an absolute http(s) URL (e.g. https://example.com)`), and this error does not duplicate it — one field, one prescription. Nothing was sent: this tier runs before the network. A **blocked** scheme is a different message and a different fact: `URL scheme 'file' is not allowed` names a scheme the security tier recognizes and refuses, so do not read it as "the parser was confused".

### Every public URL flagged as internal

A fake-IP TUN proxy (Clash / sing-box TUN mode) hands every public name an address in `198.18.0.0/15` / a `fc00::/7` AAAA, which the DNS recheck would read as internal. **This is detected — and what it changes is those two ranges, not the layer**: the recheck still runs on a tunnel host and still refuses loopback, RFC1918, link-local and metadata answers by hostname. The verdict needs two independently registered probe names to agree, so one poisoned or hosts-pinned name cannot relax the machine. The posture travels in `ssrf_recheck` (under `metadata` on a single fetch; once on the envelope of a `urls=[...]` batch or a crawl, because it is a fact about the process rather than about any one page) and `dhole --doctor` names the address and the network. If URLs are still being flagged internal, the proxy came online **after** this process first asked — and that now heals itself: a refusal of one of those two ranges re-probes the machine first (at most once per 60s), so call again a minute later. `dhole --doctor` names which process holds which verdict and when it measured. A restart re-judges immediately but is no longer the only way out. The refusal says which case it is — `diagnosis.code = "resolver_guard"`, `retryable: false`. Plain HTTP proxies are unaffected.

### The request carries headers you never set

Measured against a local site that prints back what arrived: one ordinary `web_read` with `anti_detect` unset puts **15** headers on the wire and none of them is yours — a generated `User-Agent` with its `sec-ch-ua` family, `accept`, `accept-encoding`, `priority`, and a `referer: https://www.google.com/` that dhole invents, because a navigation with no referrer is itself a tell that some walls score. Anything in that set can be named and overridden with `headers=` (the values you set win key by key, `sec-fetch-site` and `accept-language` included); the whole set is switched off with `anti_detect='off'`, which leaves 14 headers, all of them the client library's own, and no referrer. `summary` says which of those two ran, on the call that actually used the HTTP tier.

Four limits live under dhole and are written here instead of pretended away:

- **The set is one browser's now; it used to be three.** Measured against the same local site over 16 requests from one process at the default rung: **3** paired a phone `User-Agent` with `sec-ch-ua-platform: "Linux"`, and one request arrived with an iPhone `User-Agent` next to `"Google Chrome";v="149"` brands and `sec-ch-ua-platform: "iOS"`. Two causes, both closed: the generator was being asked for *any* of four families in either device shape (measured: 6 of 40 rolls Safari, 2 of 40 mobile) while the handshake was pinned to Chrome desktop, and dhole's names then step on that preset **key by key** — a partly-overridden coherent set is where the second machine comes from. The other cause was scope: a fresh identity per session, and the HTTP tier builds one session per call, so 8 sessions in one process presented **7** faces. Now the generator is asked for the family being impersonated, desktop only, once per process per family — **one face per process**: every retry, every URL in a `urls=[...]` batch and every escalated call inside one dhole process presents the same browser; restarting the process re-rolls. Measured after: 12 calls, 1 face, 0 phone shapes, `sec-ch-ua-platform` the same OS the `User-Agent` claims. (An older `browserforge` that refuses those two arguments is still used un-pinned — losing the pin beats losing the whole set, and `summary` on such a machine is unchanged.)
- **What stays is inside the generated set itself.** Re-measured after that fix: **12 of 12** requests still carried `sec-fetch-site: "?1"` — the value real Chrome reserves for `Sec-Fetch-User` (its own `Sec-Fetch-Site` is `none` / `same-origin` / `cross-site`) — and all of them carried the invented `referer`. So dhole's fingerprint is not one coherent machine at the field level, and that is a stated limitation rather than a claim: the ladder impersonates *a* browser, not *your* browser. A site that cross-checks those fields can score it, and no header you set fixes the fields you did not set — `anti_detect='off'` is the only setting that sends a set drawn from one place.
- **Two names come from the compiled impersonation layer beneath dhole's header dict** — `sec-purpose: prefetch;prerender` and `upgrade-insecure-requests`. Both step aside for a name you set yourself (measured: `headers=` with `sec-purpose` arrives as written). What no parameter reaches is that layer's own choice of which names to add when you named none.
- **`anti_detect='off'` can make a wall start failing.** That invented referrer is load-bearing on sites that pass today. When a site that used to answer begins to answer with a challenge, `summary` saying `no dhole-synthesized headers` is the evidence for what changed — go back to `auto` (or `aggressive`), do not go looking at the site.

**The browser leg adds three names that dhole never writes.** Measured against the same local echo: a `web_read` that ran on the stealthy browser put `connection: keep-alive`, `pragma: no-cache` and `cache-control: no-cache` on the wire, and the HTTP tier's fifteen did not carry them. None of the three comes from dhole's header dict — a case-insensitive search of dhole's source for `pragma` returns 15 lines and not one of them is an HTTP header (7 coverage markers, 7 SQLite statements, 1 license line), which is why that dict is the wrong place to look for them. They come from the browser's own `no-cache mode`, which the automation layer switches on for any page that has request interception installed, and dhole installs interception on **every** browser path: the resource block, the internal-target recheck and the credential host-binding all live in that one handler, so no rung (`anti_detect='off'` included) and no parameter leaves a page un-intercepted. Three consequences, each read off the echo: those two names ride **every** request that page makes, including one the page's own script issues with `fetch()`; they appear even when the registered route can never match the URL; and re-sending the request's own header list from inside the handler does not clear them. What does clear them is **naming the header yourself** — `headers={'cache-control': 'max-age=0'}` arrives as written and the pair is then not added, because the browser fills only a name that is absent (the same rule `sec-purpose` follows on the HTTP tier). So these two are a fact about the mechanism layer rather than a synthesized tell: on the wire they are what "this request passed through an interception layer" looks like, and the private-target waiver — the one that stops the headers dhole *invents* — leaves them alone on purpose.

### Fetch timeouts

`timeout` is the wall-clock budget for the whole call (default 30s, max 120s) and the HTTP tier's attempts share it — but **one attempt never exceeds 30s**, whatever you set. So a bigger `timeout=` buys more attempts, not a longer single wait: `timeout_applied` is the receipt that says the ceiling bit, and `summary` says the same in words. This is what a big file runs into — a document download is ONE attempt (a retry restarts it from byte zero), so a file that cannot arrive inside 30s of one connection is not fetchable over HTTP at any `timeout=`. When that happens the advice names the levers that do exist (another source, or fetch the file outside dhole and hand it to `doc_parse`); it does not tell you to raise a number that cannot move. That holds whichever clock cut the call — the tier's own per-attempt ceiling, or this call's whole budget arriving first — because the tier writes down what it has learned about the file (its size, from the preflight that runs BEFORE the download) and the message that replaces it reads from that note; a document cut mid-download names its size and the 30s ceiling in the same sentence, and `diagnosis.recover_with` names the same lever the prose names. The 50MB **extraction** cap gates BEFORE download; oversized bodies fail with `Response body too large` and raising `timeout` won't help. If what you want is the file rather than its text, that cap is the wrong ruler: `web_read(save_to=...)` is measured against `max_save_bytes` (default 256MB) and never extracts.

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

- `cdp_authorization_required` — **for this target host** (the site being read, not the Chrome debug endpoint) there is no approval the gate can accept, so nothing was executed. The sentence after the colon names which of the three cases you are in: never approved, approved more than 30 days ago (it quotes the date), or approved before approvals carried a date. Attaching hands over the authenticated session of every site the user is logged into, and dhole asks once per host per window: present it as the user's decision, and only on a clear yes re-call with `confirm=true`. That second press **refreshes** the timestamp — it does not stack a second approval. Retrying without the user's yes changes nothing, and no parameter widens the fence. Reading the roster: `sys_state(show=['cdp_hosts'])` returns the hosts approved right now, and its `summary`/`next_action` spell out each host's approval date and age in days plus the **name** of the file the roster lives in (the full path is machine information and stays off the reply — `dhole doctor` prints it) — a host sitting on disk outside the window is listed as not approved rather than quietly counted as one
- `cdp_host_not_allowed` — the host is not in the authorized set, and the match is an **exact hostname**: `example.com` authorized does not let `www.example.com` through, and neither does it cover a CDN the page loads assets from. With the user's yes, attach to that host with `confirm=true`; never work around by fetching a different host with the user's login state. It also arrives when an attached page **tried to leave** the fence on its own (a redirect, a script-driven navigation): the out-of-fence page's content is never handed back as the answer you asked for. The refusal itself says which side you are on — "ended before it left the browser" means no request reached that host, "already left the browser" means it did but nothing was read back. Either way, read the host it names as the one the page was aiming at, and authorize it on purpose only if that is what the user wanted
- `cdp_no_logged_in_context` — the endpoint answered but exposed **no browser context**, so there was no logged-in profile to ride and **nothing was fetched**. dhole refuses instead of quietly creating an isolated context: an anonymous page fetched inside your Chrome looks exactly like your logged-in view except for one absent field. Re-call the same url once (a just-started endpoint can report its contexts late); if it repeats, that `cdp_url` is not the window holding the logins — the user starts the signed-in profile with `--user-data-dir` + `--remote-debugging-port`
- `cdp_endpoint_unreachable` — the endpoint did not answer at all, so **nothing was fetched and no cookie was read**. Remote debugging lives and dies with the browser process: a bigger `timeout` fixes nothing here. The browser holding the logins (Chrome **or** Edge) has to be running with `--remote-debugging-port=<that port>` plus the `--user-data-dir` that carries those logins; then re-call the same url. If the endpoint moved, `cdp_url=` is the thing to change - hosts approved inside the window stay authorized, and an approval past it is asked again
- `login_check.logged_in: false` — session expired in the attached Chrome; the user re-logs in their window, then re-run the call
- `browser_busy` — `headed=true` asked for a **visible** window while a hidden browser was already running on that profile, so **nothing was fetched**. dhole will not take that browser over (a profile appearing on screen is the user's decision) and will not close it (those tabs may be the ones they are standing in). Two honest ways out, both the user's call: hand the window back first (`sys_state()` → `sys_state(clear=['<session id>'])`) and re-ask, or drop `headed=true` and let this call ride the browser that is up — a visible one serving hidden work is normal, the other direction is what this error is
- `logged_in: null` — the page's DOM shape wasn't enough to decide (pure-JSON endpoints, odd layouts); read `signals` and say what's uncertain instead of guessing

### Page-action failures (snapshot / index actions / submit)

- `pending_approval: true` in `metadata.actions` — not a failure: the submit PAUSED before acting, nothing was sent. Describe the submission to the user; on a clear yes re-run the **whole chain** with `confirm: true` on that submit (each call reloads the page, so prior fill/click steps must be redone)
- An error naming the element index ("beyond this page's N interactive elements", or an `expect` mismatch reporting what the element reads now) — the page changed under you. Re-run `{"snapshot": true}` and re-address; don't retry the stale index
- The snapshot came back empty / `could not enumerate` — the document was still loading; add `{"wait_selector": ...}` before the snapshot
- `element_screenshot_disabled` — retired (element captures need no permission). Seeing it means the installed dhole is too old; say so instead of editing env
- **a refusal naming a `screenshot` and a needed floor** ("a screenshot needs the page's real resources … and 3000ms cannot hold one") — the budget for this call is smaller than the cheapest possible capture, so **nothing was fetched and no browser was started**. This is arithmetic, not a slow site: a capture pays a browser start, the navigation with its idle wait, and the capture itself. Two doors, both real: raise `timeout=` to at least the number the sentence names, or drop the `{"screenshot": ...}` step (it is the only action whose product is the rendered page — the rest of the chain runs without the page's images and stylesheets and fits). Re-sending the same chain with the same `timeout` is not a retry, it is the same refusal

### Long calls and what you hear while waiting

A call that is going to spend seconds says so **before** it starts spending, on two channels: `notifications/progress` and `notifications/message` (logger `dhole.stage`). The three things that used to be silent are the three that speak now: the tier jump `http → stealthy`, a browser process starting, and the up-to-5s wait for the network to go quiet on a page that rendered nothing. Each sentence carries what it is waiting for, and — only when a budget exists to compare against — how much of this call's budget is left. Nothing was invented for the remainder: it is read from the deadline the call actually runs on, so a call with no named budget simply doesn't say that half.

Two caveats worth knowing before you build on it: your host may implement neither channel (many show nothing until the result lands), and a stage message is **not** a completion signal — the answer is still whatever the call returns. Per-step counts inside a long chain or a crawl arrive on the progress channel as before; those are the ones with a real denominator, and they are deliberately not duplicated onto the log channel.

### Focus behaves differently than expected

- `neural_focus_model_missing` — `focus_engine='neural'` was asked and the local reranker is not usable; no request was made. The message names WHICH of the two it is: `not downloaded yet` (the files are not on disk) — for this call pass `focus_engine='bm25'` (plain keyword focus, no model), or let the model download once: a plain `web_search` brings it down on its first run under its default `mode` (one-time, resumable) - `web_search` has no `focus_engine`, so naming one there is refused. Or `its files ARE on disk here, but the reranker would not load` — then nothing is missing to install or download; the same sentence carries what this process recorded (e.g. an ONNX runtime that is not there), and that is the thing to fix. `bm25` is the way out for THIS call either way
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
4. A local-document refusal says which of three things it is, and the wording is read off the failing import itself: `Missing dependency for .xlsx parsing: \`openpyxl\` cannot be imported here` names the package THIS parse asked for (the file's format, not dhole's install) and prints the one command that adds it; `the \`docx\` package is on disk, but what dhole imports out of it is not there` names a version disagreement inside a package you already have, where installing changes nothing; `dhole's own import failed` is neither — it is a broken build, and restarting the server is the only action that can change the outcome. A refusal that says the failing import names no module is claiming nothing about any package, whichever of the three it turns out to be. Paths inside these sentences follow the same rule as the `url` field: **a root this server's own machine owns arrives symbolic** — `<server cwd>` for the process's working directory (under a host that is the host's install directory, a path you never typed), `~` for the user's home — and that includes a parser quoting back the file it was handed. A path you supplied that is not one of those roots is echoed verbatim, so an absolute path in a refusal is either yours or already masked; it is not a read on the machine dhole runs on.

### A tool name this server does not have

Two labels, two different facts, and only one of them is about the name you typed. `unknown_tool` = this build's roster has no such door: `error` lists the names it does have, nothing ran, and `retryable: false` because re-sending the same name will say the same thing. A name that was retired arrives under the same label with the door it moved to spelled out in that same sentence — the sentence is carried whole (it is not cut to a transport limit), because the replaced call's argument shape is the part you have to copy. `tool_not_enabled` is not that: this build HAS the door and this process switched it off with `DHOLE_TOOLS`, so the lever is the environment plus a restart, and no other argument moves it.

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
