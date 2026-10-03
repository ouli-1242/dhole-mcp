# Tool Deep Behavior

Tool descriptions cover parameters; this volume covers **how the tools actually behave** — tiers, limits, and the parts most likely to be misread. Value ranges still live in `tools/list`.

## web_read: the fetch chain

**Three-tier escalation**: direct HTTP (~1s) → stealthy browser when blocked or facing a JS shell → archive.org snapshot on final failure. `escalation_path` records where the call actually landed. Two no-escalation exceptions (since 16.0): 500/502 go to archive fallback or an honest error instead of the browser; data documents (XML / images / PDF) are never sent to the browser. On strongly anti-bot sites, send subsequent calls with `force_fetcher='stealthy'` and skip re-walking the chain. **The archive tier costs 10-30s, and no parameter disables it** — it is the last answer, not an opt-in.

**Shell-page judgment**: `content_ok=true` only means 2xx + non-empty. Check `total_extracted_chars` and `page_type` first — a few-hundred-char "success" is a placeholder. Retry true shells with stealthy; login-walled sites (out of scope) and DataDome / Akamai / interactive Turnstile cannot be beaten — say so honestly.

**Trimming and pagination**: `focus` returns only relevant blocks (header notes "showing N of M blocks"; `focus:''` gets the full page); after truncation, continue with `offset=next_offset`, re-passing the same focus. `max_content_chars` spans 500–200000. The 50MB body cap gates BEFORE download — oversized bodies fail immediately and raising `timeout` won't help. Default block picking is BM25 keyword overlap; `focus_engine="neural"` scores blocks **semantically** instead (model missing = an explicit error, never a silent download; `focus_mode="best"` returns the single top block plus its heading — the "which paragraph answers this" mode).

**`focus_engine` picks the scorer per call** (`"bm25"` | `"neural"`). 17.0: there is no machine-wide license anymore — omitted or `"bm25"` always runs the cheap keyword focus, `"neural"` runs the local cross-encoder for this call only (~12.5s of CPU per page plus a ~280MB model, which is exactly why it is never the default). `"neural"` with the model missing is **refused** (`neural_focus_model_missing`, no request sent, no silent download): offer the one-time download (any neural `web_search` fetches it, resumable) or re-run with BM25. It applies to `web_crawl` too, where the per-page cost multiplies by `max_pages`.

**PDF**: `pages=` only trims **extraction** — the file still downloads whole, so raise `timeout` (default 30s, max 120s; it is the wall-clock budget for the whole call) for big files. Responses carry `table_of_contents` (titles + page ranges) — pick pages from it. Scanned or CID-broken PDFs auto-OCR.

**`css_selector` applies to HTML only**: on XML/JSON/plain text it matches nothing — the whole document comes back and `summary` states the selector was not applied. Don't mistake the full payload for your slice.

## Page interaction (actions, via the stealthy browser)

The chain is **`web_act`'s** since 17.0 §4: `web_read(url=, actions=)` is refused and the refusal names the door (`web_read` never touches what it fetches, and a chain does). Actions run in order; each leaves a receipt in `metadata.actions`.
- `{scroll:n}` or `{scroll:{steps,selector,ms_per_step}}`: scrolls to the bottom, then polls the DOM until the page **stops growing** (chases the new bottom, two quiet rounds ends it), capped at a 20s budget — and it dispatches a real `scroll` event, because when the viewport is tall and content short, `scrollTo` fires nothing and event-only lazy-loaders never wake
- Sites gating lazy-load on `event.isTrusted` ignore synthetic scrolls — use `{click:'<css of the load-more button>'}`; forms take `{fill:{selector,text}}` + `{press:'Enter'}`; `{wait:ms}` / `{wait_selector:'css'}` (or `{selector,count,state,timeout_ms}`) hold for elements
- **No selector? snapshot first**: `{"snapshot": true}` returns a numbered list of interactive elements (index/tag/role/name, capped at 80 with `elements_truncated` + `elements_total` when cut). Then address by index: `{click:{index:12, expect:'登录'}}`, `{fill:{index:3, text:'...'}}`, `{press:{index:7, key:'Enter'}}`. Snapshot and actions share ONE interactive-element selector in document order, so indices reproduce across page loads; `expect` verifies the element's visible name before acting — a stale index fails loudly naming what sits there now, instead of clicking the wrong thing
- **Submit is gated**: `{"submit": {selector|index}}` is the explicit irreversible step (clicking a submit button is NOT it). Without `"confirm": true` it PAUSES: a `pending_approval` receipt, nothing submitted, the response's summary/next_action tell you to describe the submission to the user and re-run the same actions chain with `{"confirm": true` on that submit after a yes. Paused and confirmed runs both land in the write audit. Never set `confirm: true` yourself without the user's explicit yes
- **Element screenshots** are always available: `{"screenshot": {selector|index, decode_qr?}}` rides the response as image blocks (not base64 in JSON); `decode_qr: true` decodes QR codes locally (deterministic OpenCV work) and lists the texts in the receipt. Captcha discipline is part of the tool description: ≤3 attempts on image captchas then hand off to the user; DataDome / Akamai / interactive Turnstile / 2FA / sliders are always hand-offs — dhole is a tool layer, cracking is not its business, and **dhole ships no vision model**: the reading happens in you, so an element capture is for the image you can read yourself.

## Structured extraction (schema)

Repeated records REQUIRE a "container selector + sub-schema" — sub-selectors evaluate **inside** each container, one record per container. A bare top-level selector returns the page-wide union; "which tag belongs to which quote" simply doesn't exist in that output. A property without `"type"` returns the FIRST match; `"type": "array"` returns all of them. Max 200 records per field, 8 nesting levels; only `selector` / `attribute` / `type` / `properties` / `items` are read — any other JSON-Schema keyword is ignored; sub-selectors pass the same injection validation. Example:

```json
{"properties": {"quotes": {"type": "array", "selector": ".quote",
  "properties": {"text": {"selector": ".text"}, "tags": {"type": "array", "selector": ".tag"}}}}}
```

## Two layers in one call (`nested_*`)

`schema` nesting scopes selectors **inside one document**; it cannot reach a second page. When the items you want each live on their own page — 8 featured collections and the works inside each, a result list and its detail pages — that is a fan-out over URLs, and `nested_selector` + `nested_fields` do it in one call instead of nine:

```json
{"url": "https://quotes.toscrape.com/", "nested_selector": "div.quote",
 "nested_fields": [{"name": "author", "selector": "h3"},
                   {"name": "born", "selector": "span.author-born-date"},
                   {"name": "description", "selector": "div.author-description"}]}
```

Measured on that call: 8 records (10 containers, 8 unique authors), 8.2s, `summary` ending `nested: 8 links expanded, rungs=2, ok 8 / empty 0 / failed 0`. Read the four things the fan-out owes you:

- **The href is taken from the matched element, its first descendant `<a>`, or its nearest ancestor `<a>`** — in that order. So `.topic-card` works even when the card is a `<div>` holding the link, which is the common shape.
- **`nested_fields` runs on the second-layer page only.** Nothing from the list row rides along — if you need the card's own text next to its detail page's fields, extract it on the first page with `schema=` in the same call (the two results sit side by side in one JSON object). A field that matches nothing on that page comes back as `""`; a page where *every* field missed is `nested_empty`, which is a failure and not an answer. Name the element that holds the value (`div.author-description` for the bio above), not the bare tag: on that author page `{"selector": "p", "type": "array"}` returns Login / copyright / credit paragraphs — a hit that is not the answer.
- **`truncation=nested_budget` means "there were more, and raising the cap gets them"** — the default expands 8 links and the ceiling is 30. Duplicates and href-less matches are counted in a separate note (`links: 5 elements matched → 2 unique, 2 without a usable href`), because re-raising `nested_max` recovers neither. A list page with 12 sections answered by 8 records is only correct if you raised the cap or said so out loud.
- **Each second-layer link passes its own robots.txt check before any request** — the fan-out took that job off your hands, so a disallowed item comes back as `{"error": "robots_disallowed"}` with `robots_skipped` counted in `summary`, and nothing was sent for it. If you would have checked per URL by hand, the tool now does it as part of the call.
- **A column that comes back the same everywhere is named in `summary`** (`nested fields: 介绍 came back identical on all 4 second-layer pages - either those pages really share that value or the selector is grabbing page chrome`). `ok N / empty 0` counts whole pages, so a run where every page yielded *something* — but the wrong something — used to read as a complete success; the bio-selector mistake in the bullet above is exactly that shape, and it is now reported rather than left for you to notice. Same for a column that is empty on every page while the others fill. Both are `ok` deliveries with a warning, not errors: the values you got are still the values, and re-deriving one selector is one call, not a rerun.

**More than 30 second-layer pages** is no longer one read: take the link list with `schema={"properties":{"href":{"type":"array","selector":"a.card","attribute":"href"}}}` on the first page, then `web_read(urls=[...])` in batches — `schema=` combines with `urls=` (one schema applied to each URL, so a batch of detail pages comes back as grouped records), and 30+ URLs is where `web_crawl` / `web_discover` become the cheaper map.

Second-layer pages are read as whole documents (like `schema=`), so a field's `type: "array"` is what separates "the one title on that page" from "the 20 works listed there". Concurrency is 6 lanes. Not combinable with `urls=`, `cdp_url=` or `actions=` — each of those would silently drop one side, so the call is refused before any request goes out. A call is cache-addressable like any other, **including its fetcher tier**: pinning `force_fetcher='stealthy'` within the TTL can be answered by the copy that tier already produced, so add `cache_ttl=0` when you need that tier to re-visit the site (after a login, or after the first read looked like a shell).

Second-layer pages are read as whole documents (like `schema=`), so a field's `type: "array"` is what separates "the one title on that page" from "the 20 works listed there". Concurrency is 6 lanes. Not combinable with `urls=`, `cdp_url=` or `actions=` — each of those would silently drop one side, so the call is refused before any request goes out.

`nested_no_match` (0 usable hrefs) and `nested_empty` (every page returned no field) are both **failures, not empty answers**: the first is a selector problem (widen it to the container, or the page is a JS shell — retry with `force_fetcher='stealthy'`), the second is a `nested_fields` problem. Neither is fixed by re-sending the same call.

## web_request: one write, sent once

Since 17.0 a non-GET is its own tool: `web_request(url=, method=, body=, content_type=)`. `web_read` refuses `method=` — reading and writing are different jobs, and the write's rules used to live in a branch of the read.

- **Never retried**, for every verb that changes state: POST, PUT, PATCH and DELETE all go out exactly once, because the previous attempt may already have committed and re-sending writes twice. That is a rule dhole sets for itself, not a reading of the spec — RFC calls PUT and DELETE idempotent, which is a claim about the endpoint, and a client cannot check it. (GET and HEAD remain retryable: asking twice changes nothing.) A timeout therefore means "unknown", not "failed" — check with a read before you send again.
- **Never cached, never escalated, never answered from a snapshot**: the answer comes from the server's reply to this request. A browser replay would answer about a GET to that URL, and an archive snapshot is one GET from some past day — neither has seen your write.
- **`HEAD`** is here rather than in web_read because it is a request shape, not a content read: status + declared length + type, no body.
- **Bodies cap at 256KB**: this retrieves, it does not upload. A dict becomes a form (urlencoded, or JSON when `content_type` says json); a string goes out as-is.
- **Credentials are not a parameter** — `cred_defaults` stores them per host and the request carries them; `identity_applied` names what ran. Same rule as every other tool (§3-17).
- **No `anti_detect` here**, deliberately (§4 lists 7 names for this tool; the 7th does nothing): a write never renders, so there is no ladder to choose — the HTTP tier is the whole request.

Unknown verbs are refused with the list of the five, before anything is sent; `method=GET` is refused with a pointer to web_read — GET stays out because **web_read owns** the cache, the escalation ladder and the archive fallback, and a write must not be read through those. Sending a write is the one call worth checking first: `web_resolve` answers where a URL actually goes.

## Credentials, cookies and sessions
- **`auth` and `cookies` are stored, not passed** (17.0 §3-17): `cred_defaults` `set={auth: ..., host: "api.example.com"}`. `auth` shapes are `{type:'basic',username,password}` / `{type:'bearer',token}` / `{type:'header',name,value}` (for `X-API-Key`-style headers — a one-off custom header is not a thing any more; the stored `auth` of type `header` is how such a site is reached). It only places the header — **no OAuth flows, no token refresh**; when a token expires, store a fresh one. Credential **values** are never echoed anywhere; bodies fetched with credentials are cached per-credential (an admin view is never replayed to anonymous requests)
- **Credentials do not survive cross-origin redirects**: cookies and `Authorization` go only to the origin you named; a hop to another domain strips them (same-host hops and hosts already holding that cookie in the jar keep them)
- The `session_id` cookie jar persists per host (24h expiry; responses report cookie names, never values). Since 17.0 it **spans both tiers**: a NAMED session's dedicated stealthy browser is seeded from the jar before navigation and harvests new cookies back after — HTTP-tier login state rides browser calls too. Boundaries: the shared auto session is never seeded (that would pour session A's login into session B); CDP attach never bridges (it runs your real Chrome profile, read-only). Roster and cleanup: `sys_state` (no args = the roster, `clear=["<session id>"]` forgets one, `clear=["sessions"]` with `confirm=true` forgets all)
- `<meta http-equiv=refresh>` is followed on both tiers (the HTTP tier may not follow delayed refreshes; `metadata.meta_refresh` records the hop origin)

## Conditional requests (304 is a success)

**Conditional requests are dhole's own business since 17.0**: no parameter asks them. `web_watch` stores the origin's version markers next to its baseline and asks "changed?" on every later check, so an unchanged page costs one header. Reading rule that survives: `status=304` with `content=[]` is **success**, never a failed fetch, and must not escalate to the browser (there is no body to render by definition).

## web_read: batches, links and the local waiver

`urls=[...]` batches in parallel; `include_links` caps each link class at 30/20/20 (`max_links` up to 100) with `total_found` reporting the pre-truncation count; `include_media` adds up to 20 image URLs to `links`. A proxy from `cred_defaults` is credited as an explicit proxy, so it gets a 5s TCP precheck — an unreachable one returns `proxy_unreachable` with no request sent. Local dev services need `cred_defaults` `allow_private` (true = loopback, or an explicit host list); nothing private is fetched before that waiver exists.

## anti_detect: one dial, four rungs

**One dial, four rungs (17.0)** — `off` (no hardening; use it only when the hardening itself is what a site trips on), `basic` (canvas noise + WebRTC block), `auto` (the default, and the tested-against-real-walls configuration; also runs the Cloudflare challenge path), `aggressive` (auto + loads every resource the page requests, which is what a real browser does — slower, and the rung to try when a site scores the missing subresources). No fifth rung and no per-knob override, deliberately: those settings only work as a set, so the six arguments that used to expose them (`headless`, `real_chrome`, `hide_canvas`, `block_webrtc`, `solve_cloudflare`, `useragent`) are gone as parameters. Two of them were capabilities you may expect back: a hand-set User-Agent is a **stronger** tell than none (the UA has to match the TLS fingerprint the HTTP tier impersonates and the browser tier's fingerprint profile — mismatching pairs are exactly what scanners look for), and forcing the installed-Chrome channel turns "Chrome is not installed" from a fallback into a hard failure — dhole already uses the installed Chrome where it exists, so that rung needs no argument.

**Two of those settings are launch-level, and that is where they get applied (17.0 B5-14a-2)**: canvas noise goes into the context's init script and the WebRTC policy into the browser's own startup arguments — neither can be changed under a running browser. So the shared warm browser is *built* at whichever rung is in force (`cred_defaults`, else `auto`) and every call that rides it gets that rung for real; changing the stored rung replaces the warm browser, which costs one cold start, exactly like changing `profile` does. A call asking for a rung the shared browser was not started with does not pretend to have got what it asked: it runs on its own browser and says so in the response notes, because what it gave up is not only the warm start but that browser's page state — logins included. `off` is the rung that lands there most often, which is another way of saying `off` is a decision and not a cheaper default. `basic` / `auto` / `aggressive` share the same launch identity, so they never pay it: the Cloudflare path and the resource budget are per-call settings. **`web_screenshot` takes the rung as well**, and there it means one thing: whether a Cloudflare challenge gets answered before the shutter (`off` / `basic` shoot the interstitial as-is, `auto` / `aggressive` answer it). The fingerprint half is that browser's launch fact, not this call's — so a capture that comes back with empty image boxes is fixed by storing a stronger rung, not by adding an argument to the screenshot. No rung ever asks a capture to drop images, styles or fonts: a blank frame is never what "screenshot this" meant.

## What the page is waiting for

**Stopped being a knob (17.0 §6)**: the browser tier extracts once the page has loaded, and pays a bounded 5s network-idle wait **only when it recognises that the page has not rendered** (its visible text is still ungrown while the page ships scripts). `wait` — a fixed sleep — is deleted with no replacement: nothing real is better served by sleeping a fixed number of milliseconds than by letting the page say when it is done, and a fixed sleep is exactly how an intermittent failure gets hidden. The settle is inside your `timeout` and visible in `duration_ms`, so a slow SPA costs what it costs and you can see it. `anti_detect` and `cdp_url` are the only two of the identity settings that stay per-call: the rung a wall needs and the browser a page belongs to are facts about the call in front of you, not about the machine. Waiting for a specific element is still expressible where it is the job — `web_screenshot`'s `wait_selector`.

The extraction path itself is not a knob: markdown / text / article / structured run through trafilatura's readability scoring first, markdownify when it comes back empty, and tag-stripping as the last resort — that ladder is walked for every call. `extraction_type` is the ordinary way to ask for `markdown` / `html` / `text` / `article` / `structured` and needs none of the above.

## web_search: engines, ranking, dates

**Engine matrix** (8 keyless + 2 keyed; max 9 names per search). All 8 keyless
engines are in the default pool — there is no opt-in tier to name:

| Engine | Direct from CN | Notes |
|---|---|---|
| `baidu` / `bing` / `sogou` / `so360` (`360`) / `yandex` | ✔ | `sogou` and `so360` are both IP-rate-limited |
| `bing_global` / `brave` | proxy needed | Nearly disjoint results from the CN editions |
| `duckduckgo` (`ddg`) | proxy needed | Same Bing-family index as `bing` |
| `tavily` / `bocha` | keyed (off by default) | **Per-call billing, quota deducted on dispatch** — only when the user names them |

17.0 retired 6 keyless backends, so two abilities are gone: **WeChat-article
search** (`sogou_weixin` was the only index of that pool — weixin.sogou.com
content is not fully covered by the general engines) and **encyclopedia lookups as
structured answers** (`wikipedia` / `grokipedia` / `baidu_baike`; the general
engines still return those pages, they are just no longer queried as APIs).

**Ranking**: a local cross-encoder reranks (bge-zh by default); while it's still loading in a fresh process, `rerank_mode=merge` (consensus ordering, slightly weaker) — one retry restores `neural`. `relevance_score` is normalized (top is always 1.0); `min_raw_relevance` is what can judge "the whole round is irrelevant" — the raw-score distribution (bge-zh, measured): off-topic ~1e-4, marginal ~0.2, true hits 0.93+. **Start at 0.1**; hard negatives on the same topic sit at p90 0.18, so higher starts killing good results. Recalibrate after switching models by hand (`~/.dhole/config/reranker.json`). `fetch_hint` reports each round's raw-score span. `engines_consensus` counts independent index FAMILIES (6 total — `bing`, `bing_global` and `duckduckgo` are one Bing-family entry, so 8 engines reach 6 families), not raw hits.

**Dates**: only `bing` / `bing_global` / `brave` / `duckduckgo` (keyed `bocha` / `tavily`
too) accept dates, and only as day/week/month/year presets. `after=<date>` is widened to the narrowest preset covering the window (`date_filter` reports what was sent and which engines were never asked); `before` is rejected outright — search results carry no publish dates, so "older-than" is both unaskable and unverifiable. Date-bounded increments belong to `feed_read(since=)`. There is **no `total_estimate`** — do not look for it, do not invent one.

**No SLA**: keyless engines are direct scrapes of public search results (unauthorized, unquota'd) — when providers change layout or ban IPs, they degrade **silently**. For reliability: a different egress (`DHOLE_SEARCH_PROXY`) or a keyed backend.

## web_discover: the map, and what it costs

- One call, URLs only: `pages_crawled=0` in the answer is the design, not a failure. Reading the picks is `web_read(urls=[...])`; a second `web_crawl` would rediscover what is already in hand
- A map has two prices. `sitemap=true` asks for the site's own index — one request, the whole site, and each row can carry the `<lastmod>` the site published. Without it the map is grown by walking links, which costs one request per step and stops at `max_pages` (1-40). `'auto'` tries the index first, then walks
- robots.txt still decides eligibility: URLs already known to be disallowed are dropped before any request and counted in `robots_skipped`. A map is not a promise that those pages can be fetched
- Map rows carry only what differs from the default, because a map's length is set by the site rather than by the caller — a 1000-URL map is 70KB of URLs, and restating one constant per row turned that into 350KB
- `path_include` / `path_exclude` scope the map exactly as they scope a crawl (`/docs` is a subtree, not a string prefix; the only wildcard is a trailing `/*`), and `search` filters it by URL path + page title

## web_crawl: discovery economics and politeness

- The map is its own tool: `web_discover` returns URLs without body extraction, but **expanding the link graph still costs one request per page** (up to `max_pages`). For a one-request map: `sitemap=true` (whole site) or `max_pages=1` (start page's links only)
- After mapping, read the picks with `web_read(urls=[...])` — one call, no re-discovery, and nothing to reconcile. `crawl_urls=` and its four drop counters left with 17.0 §3 decision 31: a caller's list needed normalization, same-domain checks, dedup and a cap before it became a crawl, and each cut needed its own receipt. Those URLs never needed discovery, so `web_read(urls=[...])` is their home
- `focus='query'` does two different jobs at once: it **prioritizes the links** whose path and anchor text look relevant (the best-first queue scores them) and it **filters each fetched page** down to the blocks matching the query — the same trimming `web_read` does, applied per page. Passing `focus` therefore does not only narrow what you read, it changes which pages get reached at all; leave it out for an unbiased crawl and use `search=` when you only want to filter the URL map, not the bodies
- `path_include` / `path_exclude` match **path subtrees**: `'/docs'` matches `/docs` and everything below it but NOT `/docs-old`; the only wildcard is a trailing `/*`
- Budget: **there is no whole-run character budget** (17.0 §3, decision 31). What caps a crawl is `max_pages` (1-40, default 10) × `max_content_chars_per` (500-20,000, default 8,000), plus `deadline_ms`. The total went because nobody could compute it — it is a product of page lengths the site decides
- Politeness: `concurrency` (requests in flight) is the only knob left. The interval between requests to one host comes from **that host's `robots.txt` `Crawl-delay`** — 17.0 §3 decision 31 deleted `delay=`, because spacing someone else's server is not a performance choice and the site already stated its preference. The value used is named in `summary` (with its author), the robots layer clamps any ask to 60s, and `ignore_robots=true` waives it. Note Python's robotparser only reads whole seconds — `Crawl-delay: 0.5` counts as no request at all
- List pages return a **structured link listing** instead of body text (`page_type=list`) — intentional, not an extraction bug
- robots.txt is honored page-by-page: links known to be disallowed are dropped before enqueueing
- With `focus_engine="neural"` and a `focus` query, each discovered page is scored against it before extraction deepens; pages below the off-topic floor (0.03) leave the queue and the count lands in `summary` (model missing = filter silently OFF — a crawl never fails for an optional enhancement; your named start URLs are never dropped). Default (no `focus_engine`, or `"bm25"`) skips the semantic scoring entirely — the cost is per page, so it multiplies by `max_pages`

## The remaining tools

**web_screenshot**: agents receive the image inline; text-only agents pass `save_to` (response reports the absolute path + byte size; parent dirs are created; a mistyped name over a non-image existing file is refused to protect it, while same-named image files may be overwritten). `selector` crops a single element; `decode_qr: true` decodes QR codes locally — both always available (17.0 removed the multimodal permission gate).

**doc_parse**: the only entry point for local documents in an MCP-only client (no file tools of its own), and the only safe one anywhere for a zip/container format or a file whose encoding you don't know — a docx/xlsx/pptx/odt is a zip and a pdf is a container, so a text reader sees bytes, and a non-UTF-8 csv/html decodes into plausible-looking **wrong** text rather than an error. Extensions: .html/.htm/.xhtml/.docx/.xlsx/.csv/.pdf/.md/.markdown/.txt/.json/.yaml/.yml/.pptx/.odt; .json/.yaml are **validated** (a broken file is one error line, not a byte dump); unlisted extensions (.log/.rst/.ini…) are refused with a rename-to-.txt hint. Mojibake → explicit `encoding=`. Parts a text tool cannot carry (pptx speaker notes, layout, images, comments) are declared in the **first line** of output, never silently dropped. HTML tables share one extraction function across parse / web_read / web_crawl — byte-identical markdown tables.

**feed_read**: batch RSS/Atom, newest first; item summaries are 500-char previews (`summary_truncated=true`) — fetch the item URL with web_read for full text. `since=` for incremental polls (ISO date or RFC-822 timestamp) — pass the newest `published` from the previous call; `items_older_than_since` counts what it dropped, and an entry whose date cannot be read is kept and counted as undated rather than silently dropped; passing a **site homepage** works — it follows the page's own alternate links to the feed (`discovered_from` records it); discovered addresses pass the SSRF guard too (a malicious page cannot weaponize auto-discovery against your intranet). Unparseable feeds fail with one plain-language verdict plus the candidates tried — don't re-parse XML yourself.

**web_resolve**: follows HTTP redirects and body meta-refresh to the final address without extracting content. Resolve t.co / bit.ly / tracking links before deciding whether to fetch.

**sys_state**: one tool for what dhole keeps on this machine and how to erase a named class of it — the body cache (`cache.db`), the cookies a site left behind a `session_id=` (`sessions.db`), the engine pool's cooldowns and yield history (`circuit_breaker.json` / `engine_stats.json`), and the stored recipes and watch baselines (`tasks.db`).
- No arguments = **the census**: every open session with its hosts and cookie NAMES (a value is a credential; values never leave the process), how long before each one lapses, whether a browser is running under it, plus the cache's entry/expiry counts and the stored-task counts. Reading is never an erasure.
- `clear=` takes those class names, **or one open session id** to forget exactly that session.
- `clear=["cache"]` wipes the whole content cache and forgets robots verdicts with it (a site that just relaxed its rules should not stay refused until the TTL lapses). It leaves login state **alone** — forgetting cookies is the `sessions` class, a separate token, because it is a separate consequence. To refresh ONE url, pass `cache_ttl=0` to web_read/web_crawl instead of wiping the cache.
- `clear=["sessions"]`, `["watch"]`, `["recipes"]` or `["all"]` **pause** without `confirm=true` and erase nothing. The rule behind that split is what you would notice: losing a site's cookies surfaces only at your next visit there, a watch with no baseline reports "no change" instead of the diff you were waiting for, and a deleted recipe surfaces when the page turns out to need a click chain. `cache` and `engine_state` need no confirm — their cost is the next call being slower or asking different engines.
- An unknown token is an error naming both the classes and the ids that ARE open, never a silent no-op — `clear=["cahce"]` that "succeeded" would be the worst possible answer.

**sys_state's `engine_state` class** is the MCP-side equivalent of `dhole engines reset`, and the one that reaches a server already running (the CLI clears the files; the process keeps its own copy in memory until it restarts).

## The stateful tools (web_recipe / web_watch / cred_vault / cred_login / cred_defaults)

All five are registered by default in 17.0 (the opt-in groups were deleted). If one is missing from `tools/list`, the operator named an explicit `DHOLE_TOOLS` subset — widening it is their call. State lives in `~/.dhole/tasks.db` (recipes, watch baselines) and holds no credentials; the identity `cred_defaults` stores lives in `identity.db` and does.

**web_recipe — record a way to reach a page, then replay it.** `op=save` stores a `{url, actions, note}` triple; `op=run` fetches that url with those actions; `op=list` shows every recipe with `use_count` / `last_ok` (a stale one is visible without running it); `op=delete` forgets one. The action chain is validated **at save time** by the same validator the executor uses, so a chain that would be refused at run time cannot be stored — a rejected `save` is a real signal, not a formality. `run` returns the whole fetch in `result` (same wire compaction as `web_read`) plus `outcomes`, one receipt per action, so a broken step is visible without digging into `result.metadata`. **`actions_failed` non-zero = the page changed under the recipe** — re-derive the steps with a snapshot and re-save; do not keep replaying it. `session_id` is a per-run argument, never stored in the recipe (a stored session id would be a stored credential).

**web_watch — diff a page against its last snapshot.** `op=add` stores the url (+ optional `focus` and `max_chars`), `op=check` fetches it fresh and diffs the text, `op=list` / `op=remove` manage them. Three fields carry the semantics:
- `changed` is **tri-state**: `true` / `false` when compared, **`null` on the first check** (`baseline_created: true`). A missing baseline is not "no change" — reporting the first run as unchanged invents a fact, so `changed` is deliberately never omitted from the wire.
- `added` / `removed` count the **whole** change even when `diff_truncated` is set (the diff text is capped, the counts are not).
- `baseline_cut` means the stored snapshot hit `max_chars`, so a later diff can miss content past the cut — widen `max_chars` and re-baseline for a long page.
A failed fetch leaves the baseline **untouched** and puts the reason in `error` — a transient outage never silently re-baselines (which would hide the very change you are watching for). Pass `focus` for a long page whose chrome changes on every load.

**cred_vault — read the user's OWN password manager, metadata only.** Reads the Bitwarden (`bw`) / 1Password (`op`) CLIs; it never creates or stores credentials. `op=status` reports per-manager `{available, unlocked, detail, hint}`; `op=list` returns item metadata (`id` / `name` / `username` / `has_password` / `has_totp` / `uris` / `source`, capped at 300); `op=match` returns the items whose stored URI matches the given URL's host (dot-bounded suffix match, so `example.com` does not match `notexample.com`). **There is no field here that can hold a password or a TOTP secret** — the response shape makes leaking one impossible instead of relying on remembering to redact. What it can say is `has_password: true`, which is all that picking a credential needs. A manager that is simply absent is a `notes` entry (carrying its install step), not an `error`. `status`'s `unlocked` is `null` when the CLI is not installed — "not asked" is not "locked".

**cred_login — sign in with a stored credential, in two calls.** Call it **without `confirm`** first: it opens the page, detects the username / password / submit controls, and returns `pending_approval: true` with `fields` + `plan` — **nothing is filled, nothing is submitted**. Re-call with the same arguments plus `confirm=true` after the user approves; only then does it fill and submit. The gate covers *fill* as well as submit, because typing into a page fires its input events and a hostile page can exfiltrate a value the moment it is typed. The password is read server-side from the vault and **never enters the response, the transcript, or any log** — the receipt names the vault item instead. Afterwards `logged_in` is the same model-free DOM + cookie-NAME probe as `web_read`'s `check_login`, and it is **tri-state**: `null` means the probe could not answer, which is not `false`. Cookies are kept under the `session_id` you pass, so later fetches reuse the session. CAPTCHA / 2FA are **not** solved — hand those back to the user. Override detection with `username_index` / `password_index` / `submit_index` when the form is unusual.

**cred_defaults — the identity later calls should assume.** Seven keys, one call: `proxy`, `profile`, `cdp`, `allow_private`, `anti_detect`, plus `auth` and `cookies`. The last two **require `"host"` in the same `set` object** and that is the tool's whole design: a stored credential with no host would ride along to the next site you read, which is a different site's request — the mistake the cross-origin credential drop prevents, made permanent. The match is dot-bounded (`api.example.com` takes a default stored for `example.com`; `evil-example.com` does not). The other five are global by nature: one egress, one browser context, one attached Chrome, one ladder, one private-host allowance.

Precedence is one rule with no exceptions: **what the caller passes this time beats what is stored, which beats the tool's own default.** Only two identity settings can be passed this time — `anti_detect` and `cdp_url` (see above for why those two). `auth` / `cookies` are stored per host, so "one site, different credential" is just another scoped row; `proxy` and `allow_private` are global, so reaching a single URL without them means `unset` around the call. `allow_private=false` is read as a decision rather than an absence. A stored proxy is credited as an explicit proxy (it gets the 5s TCP precheck and a dead one fails fast with `proxy_unreachable`); an environment proxy stays machine config and still decides nothing about one fetch, so a stored default is used in preference to it.

What comes back is **the redacted half**: `auth` as `{type, username}`, cookies as NAMES, a proxy as host + `credentials: true`. Values never leave the store toward the transcript, and each later `web_read` response names what it ran on in `identity_applied`. `profile` is the named persistent browser context — the only way to name one — and changing it rotates the warm browser (one cold start) instead of silently keeping the old logins. `anti_detect` rotates it for the same reason: the rung's canvas and WebRTC settings are decided when that browser starts, so a stored rung that changed has to be launched to take effect (see the ladder above). Revocation: `unset=["proxy"]` forgets one key (every host scope of it), `sys_state(clear=["cred_defaults"])` forgets the whole store and needs no `confirm`, because a missing default shows up on the very next call.

## Local data and privacy (~/.dhole, relocatable via DHOLE_HOME)

| Location | Contents |
|---|---|
| `cache.db` | Fetched body text (plaintext SQLite), TTL 1h by default (docs auto-raised to 24h, article 6h) |
| `sessions.db` | Cookie jar, **contains cookie values = credentials**, 24h expiry |
| `models/<name>/` | Rerank models (copy the whole directory for offline reuse) |
| `config/reranker.json` | Rerank model selection |
| `tasks.db` | Recorded recipes (`url` + `actions`) and watch baselines (page text). **No credentials** — a recipe stores the chain, never a session id |
| `identity.db` | The defaults `cred_defaults` stored: proxy, profile, CDP endpoint, private hosts, anti-detect rung, and host-scoped `auth` / `cookies`. **Contains credential values** — `sys_state` and every response show names only |
| `usage.jsonl` | With `DHOLE_USAGE_LOG=1`: call log (tool / duration / redacted errors) plus the audit channel — attach events, HTTP writes (non-GET), browser submits (names and outcome only, never parameter values) |

No telemetry: nothing leaves the process except traffic to the sites you point it at. Nuke `~/.dhole` for a full cleanup. Two 16.x leftovers worth naming to the user, because both were written by mechanisms 17.0 deleted and neither is read any more: `search_proxies.json` (the proxy pool) still holds its credentials in plaintext, and `repair.py` is still a script that kills every dhole process and force-reinstalls if anyone runs it. Deleting the two files is the fix.
