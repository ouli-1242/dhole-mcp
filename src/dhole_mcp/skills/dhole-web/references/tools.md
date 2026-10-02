# Tool Deep Behavior

Tool descriptions cover parameters; this volume covers **how the tools actually behave** — tiers, limits, and the parts most likely to be misread. Value ranges still live in `tools/list`.

## web_read: the fetch chain and interaction

**Three-tier escalation**: direct HTTP (~1s) → stealthy browser when blocked or facing a JS shell → archive.org snapshot on final failure. `escalation_path` records where the call actually landed. Two no-escalation exceptions (since 16.0): 500/502 go to archive fallback or an honest error instead of the browser; data documents (XML / images / PDF) are never sent to the browser. On strongly anti-bot sites, send subsequent calls with `force_fetcher='stealthy'` and skip re-walking the chain.

**Shell-page judgment**: `content_ok=true` only means 2xx + non-empty. Check `total_extracted_chars` and `page_type` first — a few-hundred-char "success" is a placeholder. Retry true shells with stealthy; login-walled sites (out of scope) and DataDome / Akamai / interactive Turnstile cannot be beaten — say so honestly.

**Trimming and pagination**: `focus` returns only relevant blocks (header notes "showing N of M blocks"; `focus:''` gets the full page); after truncation, continue with `offset=next_offset`, re-passing the same focus. `max_content_chars` spans 500–200000. The 50MB body cap gates BEFORE download — oversized bodies fail immediately and raising `timeout` won't help. Default block picking is BM25 keyword overlap; `focus_engine="neural"` scores blocks **semantically** instead (model missing = an explicit error, never a silent download; `focus_mode="best"` returns the single top block plus its heading — the "which paragraph answers this" mode).

**`focus_engine` picks the scorer per call** (`"bm25"` | `"neural"`). 17.0: there is no machine-wide license anymore — omitted or `"bm25"` always runs the cheap keyword focus, `"neural"` runs the local cross-encoder for this call only (~12.5s of CPU per page plus a ~280MB model, which is exactly why it is never the default). `"neural"` with the model missing is **refused** (`neural_focus_model_missing`, no request sent, no silent download): offer the one-time download (any neural `web_search` fetches it, resumable) or re-run with BM25. It applies to `web_crawl` too, where the per-page cost multiplies by `max_pages`.

**PDF**: `pages=` only trims **extraction** — the file still downloads whole, so raise `timeout` (default 30s, max 120s; it is the wall-clock budget for the whole call) for big files. Responses carry `table_of_contents` (titles + page ranges) — pick pages from it. Scanned or CID-broken PDFs auto-OCR.

**`css_selector` applies to HTML only**: on XML/JSON/plain text it matches nothing — the whole document comes back and `summary` states the selector was not applied. Don't mistake the full payload for your slice.

**Page interaction (actions, via the stealthy browser)**: actions run in order; each leaves a receipt in `metadata.actions`.
- `{scroll:n}` or `{scroll:{steps,selector,ms_per_step}}`: scrolls to the bottom, then polls the DOM until the page **stops growing** (chases the new bottom, two quiet rounds ends it), capped at a 20s budget — and it dispatches a real `scroll` event, because when the viewport is tall and content short, `scrollTo` fires nothing and event-only lazy-loaders never wake
- Sites gating lazy-load on `event.isTrusted` ignore synthetic scrolls — use `{click:'<css of the load-more button>'}`; forms take `{fill:{selector,text}}` + `{press:'Enter'}`; `{wait:ms}` / `{wait_selector:'css'}` (or `{selector,count,state,timeout_ms}`) hold for elements
- **No selector? snapshot first**: `{"snapshot": true}` returns a numbered list of interactive elements (index/tag/role/name, capped at 80 with `elements_truncated` + `elements_total` when cut). Then address by index: `{click:{index:12, expect:'登录'}}`, `{fill:{index:3, text:'...'}}`, `{press:{index:7, key:'Enter'}}`. Snapshot and actions share ONE interactive-element selector in document order, so indices reproduce across page loads; `expect` verifies the element's visible name before acting — a stale index fails loudly naming what sits there now, instead of clicking the wrong thing
- **Submit is gated**: `{"submit": {selector|index}}` is the explicit irreversible step (clicking a submit button is NOT it). Without `"confirm": true` it PAUSES: a `pending_approval` receipt, nothing submitted, the response's summary/next_action tell you to describe the submission to the user and re-run the same actions chain with `{"confirm": true` on that submit after a yes. Paused and confirmed runs both land in the write audit. Never set `confirm: true` yourself without the user's explicit yes
- **Element screenshots** are always available: `{"screenshot": {selector|index, decode_qr?}}` rides the response as image blocks (not base64 in JSON); `decode_qr: true` decodes QR codes locally (deterministic OpenCV work) and lists the texts in the receipt. Captcha discipline is part of the tool description: ≤3 attempts on image captchas then hand off to the user; DataDome / Akamai / interactive Turnstile / 2FA / sliders are always hand-offs — dhole is a tool layer, cracking is not its business

**Structured extraction (schema)**: repeated records REQUIRE a "container selector + sub-schema" — sub-selectors evaluate **inside** each container, one record per container. A bare top-level selector returns the page-wide union; "which tag belongs to which quote" simply doesn't exist in that output. Max 200 records per field, 8 nesting levels; only `selector` / `attribute` / `type` / `properties` / `items` are read — any other JSON-Schema keyword is ignored; sub-selectors pass the same injection validation. Example:

```json
{"properties": {"quotes": {"type": "array", "selector": ".quote",
  "properties": {"text": {"selector": ".text"}, "tags": {"type": "array", "selector": ".tag"}}}}}
```

**Non-GET is one-shot**: POST/PUT/DELETE/PATCH are never retried (the previous call may already be committed — re-sending writes twice), never cached, never escalated to the browser, never answered from snapshots — browsers only navigate (GET) and a snapshot is one GET from some past day; neither answers your write. `HEAD` returns status + declared length + type only. Body cap 256KB: this tool retrieves things, it does not upload them.

**Credentials and sessions**:
- `auth` shapes: `{type:'basic',username,password}` / `{type:'bearer',token}` / `{type:'header',name,value}` (for `X-API-Key`-style headers). It only places the header — **no OAuth flows, no token refresh**; when a token expires, supply a fresh one. Credential **values** are never echoed anywhere; bodies fetched with credentials are cached per-credential (an admin view is never replayed to anonymous requests)
- **Credentials do not survive cross-origin redirects**: cookies and `Authorization` go only to the origin you named; a hop to another domain strips them (same-host hops and hosts already holding that cookie in the jar keep them)
- The `session_id` cookie jar persists per host (24h expiry; responses report cookie names, never values). Since 17.0 it **spans both tiers**: a NAMED session's dedicated stealthy browser is seeded from the jar before navigation and harvests new cookies back after — HTTP-tier login state rides browser calls too. Boundaries: the shared auto session is never seeded (that would pour session A's login into session B); CDP attach never bridges (it runs your real Chrome profile, read-only). Roster and cleanup: `close_session` (no args = roster, `session_id=` forgets one, `all=true` clears all)
- `<meta http-equiv=refresh>` is followed on both tiers (the HTTP tier may not follow delayed refreshes; `metadata.meta_refresh` records the hop origin)

**Conditional requests are dhole's own business since 17.0**: no parameter asks them. `watch` stores the origin's version markers next to its baseline and asks "changed?" on every later check, so an unchanged page costs one header. Reading rule that survives: `status=304` with `content=[]` is **success**, never a failed fetch, and must not escalate to the browser (there is no body to render by definition).

**Misc**: `urls=[...]` batches in parallel; `include_links` caps each link class at 30/20/20 (`max_links` up to 100) with `total_found` reporting the pre-truncation count; `include_media` adds up to 20 image URLs to `links`; explicit `proxy` gets a 5s TCP precheck — an unreachable proxy returns `proxy_unreachable` with no request sent; `allow_private` (true = loopback, or an explicit host list) is for local dev services.

**Browser-tier and extraction knobs (pre-tuned — leave them unset unless something names one)**: the defaults are the anti-detect configuration, chosen together and tested against real walls; overriding one usually makes a site notice you rather than less. `headless` (browser runs headless or not), `useragent` (replace the UA the page sees), `extra_headers` (headers sent with the request; `auth` is the credential-shaped version), `wait` (extra ms after load before extracting), `network_idle` (wait for the page's network activity to quiet down before extracting — helps SPAs that render after the load event, costs time on chatty sites), `real_chrome` (drive the installed Chrome channel instead of the bundled Chromium; fingerprint fidelity differs), `hide_canvas` (noise the canvas fingerprint — some sites fingerprint canvas reads to score you), `block_webrtc` (block WebRTC, which otherwise leaks local IP addresses), `solve_cloudflare` (run the interactive challenge solver for this call; the automatic path already tries the non-interactive one), The extraction path itself is not a knob: markdown / text / article / structured run through trafilatura's readability scoring first, markdownify when it comes back empty, and tag-stripping as the last resort — the ladder is the answer for every call. `extraction_type` is the ordinary way to ask for `markdown` / `html` / `text` / `article` / `structured` and needs none of these.

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

## web_crawl: discovery economics and politeness

- `discover_only=true` returns a URL map without body extraction, but **expanding the link graph still costs one request per page** (up to `max_pages`). For a one-request map: `sitemap=true` (whole site) or `discover_only=true, max_pages=1` (start page's links only)
- After mapping, read the picks with `web_read(urls=[...])` — one call, no re-discovery, and nothing to reconcile. `crawl_urls=` and its four drop counters left with 17.0 §3 decision 31: a caller's list needed normalization, same-domain checks, dedup and a cap before it became a crawl, and each cut needed its own receipt. Those URLs never needed discovery, so `web_read(urls=[...])` is their home
- `focus='query'` does two different jobs at once: it **prioritizes the links** whose path and anchor text look relevant (the best-first queue scores them) and it **filters each fetched page** down to the blocks matching the query — the same trimming `web_read` does, applied per page. Passing `focus` therefore does not only narrow what you read, it changes which pages get reached at all; leave it out for an unbiased crawl and use `search=` when you only want to filter the URL map, not the bodies
- `path_include` / `path_exclude` match **path subtrees**: `'/docs'` matches `/docs` and everything below it but NOT `/docs-old`; the only wildcard is a trailing `/*`
- Budget: **there is no whole-run character budget** (17.0 §3, decision 31). What caps a crawl is `max_pages` (1-40, default 10) × `max_content_chars_per` (500-20,000, default 8,000), plus `deadline_ms`. The total went because nobody could compute it — it is a product of page lengths the site decides
- Politeness: `concurrency` (requests in flight) is the only knob left. The interval between requests to one host comes from **that host's `robots.txt` `Crawl-delay`** — 17.0 §3 decision 31 deleted `delay=`, because spacing someone else's server is not a performance choice and the site already stated its preference. The value used is named in `summary` (with its author), the robots layer clamps any ask to 60s, and `ignore_robots=true` waives it. Note Python's robotparser only reads whole seconds — `Crawl-delay: 0.5` counts as no request at all
- List pages return a **structured link listing** instead of body text (`page_type=list`) — intentional, not an extraction bug
- robots.txt is honored page-by-page: links known to be disallowed are dropped before enqueueing
- With `focus_engine="neural"` and a `focus` query, each discovered page is scored against it before extraction deepens; pages below the off-topic floor (0.03) leave the queue and the count lands in `summary` (model missing = filter silently OFF — a crawl never fails for an optional enhancement; your named start URLs are never dropped). Default (no `focus_engine`, or `"bm25"`) skips the semantic scoring entirely — the cost is per page, so it multiplies by `max_pages`

## The remaining tools

**screenshot**: agents receive the image inline; text-only agents pass `save_to` (response reports the absolute path + byte size; parent dirs are created; a mistyped name over a non-image existing file is refused to protect it, while same-named image files may be overwritten). `selector` crops a single element; `decode_qr: true` decodes QR codes locally — both always available (17.0 removed the multimodal permission gate).

**parse**: the only entry point for local documents in an MCP-only client (no file tools of its own), and the only safe one anywhere for a zip/container format or a file whose encoding you don't know — a docx/xlsx/pptx/odt is a zip and a pdf is a container, so a text reader sees bytes, and a non-UTF-8 csv/html decodes into plausible-looking **wrong** text rather than an error. Extensions: .html/.htm/.xhtml/.docx/.xlsx/.csv/.pdf/.md/.markdown/.txt/.json/.yaml/.yml/.pptx/.odt; .json/.yaml are **validated** (a broken file is one error line, not a byte dump); unlisted extensions (.log/.rst/.ini…) are refused with a rename-to-.txt hint. Mojibake → explicit `encoding=`. Parts a text tool cannot carry (pptx speaker notes, layout, images, comments) are declared in the **first line** of output, never silently dropped. HTML tables share one extraction function across parse / web_read / web_crawl — byte-identical markdown tables.

**feed_read**: batch RSS/Atom, newest first; item summaries are 500-char previews (`summary_truncated=true`) — fetch the item URL with web_read for full text. `since=` for incremental polls (ISO date or RFC-822 timestamp); passing a **site homepage** works — it follows the page's own alternate links to the feed (`discovered_from` records it); discovered addresses pass the SSRF guard too (a malicious page cannot weaponize auto-discovery against your intranet). Unparseable feeds fail with one plain-language verdict plus the candidates tried — don't re-parse XML yourself.

**web_resolve**: follows HTTP redirects and body meta-refresh to the final address without extracting content. Resolve t.co / bit.ly / tracking links before deciding whether to fetch.

**cache_clear**: wipes the body cache **and forgets robots verdicts and every session cookie jar** (whoever wants the cache cleared is exactly who needs to know login state is dropping); `engine_state=true` additionally resets engine cooldowns. To forget a single session, use `close_session`.

**close_session**: no args = the roster (id, hosts, cookie names, time to expiry, browser open or not); cookie values never leave the process.

## The stateful tools (recipe / watch / vault / login)

All four are registered by default in 17.0 (the opt-in groups were deleted). If one is missing from `tools/list`, the operator named an explicit `DHOLE_TOOLS` subset — widening it is their call. State lives in `~/.dhole/tasks.db` (recipes, watch baselines) and holds no credentials.

**recipe — record a way to reach a page, then replay it.** `op=save` stores a `{url, actions, note}` triple; `op=run` fetches that url with those actions; `op=list` shows every recipe with `use_count` / `last_ok` (a stale one is visible without running it); `op=delete` forgets one. The action chain is validated **at save time** by the same validator the executor uses, so a chain that would be refused at run time cannot be stored — a rejected `save` is a real signal, not a formality. `run` returns the whole fetch in `result` (same wire compaction as `web_read`) plus `outcomes`, one receipt per action, so a broken step is visible without digging into `result.metadata`. **`actions_failed` non-zero = the page changed under the recipe** — re-derive the steps with a snapshot and re-save; do not keep replaying it. `session_id` is a per-run argument, never stored in the recipe (a stored session id would be a stored credential).

**watch — diff a page against its last snapshot.** `op=add` stores the url (+ optional `focus` and `max_chars`), `op=check` fetches it fresh and diffs the text, `op=list` / `op=remove` manage them. Three fields carry the semantics:
- `changed` is **tri-state**: `true` / `false` when compared, **`null` on the first check** (`baseline_created: true`). A missing baseline is not "no change" — reporting the first run as unchanged invents a fact, so `changed` is deliberately never omitted from the wire.
- `added` / `removed` count the **whole** change even when `diff_truncated` is set (the diff text is capped, the counts are not).
- `baseline_cut` means the stored snapshot hit `max_chars`, so a later diff can miss content past the cut — widen `max_chars` and re-baseline for a long page.
A failed fetch leaves the baseline **untouched** and puts the reason in `error` — a transient outage never silently re-baselines (which would hide the very change you are watching for). Pass `focus` for a long page whose chrome changes on every load.

**vault — read the user's OWN password manager, metadata only.** Reads the Bitwarden (`bw`) / 1Password (`op`) CLIs; it never creates or stores credentials. `op=status` reports per-manager `{available, unlocked, detail, hint}`; `op=list` returns item metadata (`id` / `name` / `username` / `has_password` / `has_totp` / `uris` / `source`, capped at 300); `op=match` returns the items whose stored URI matches the given URL's host (dot-bounded suffix match, so `example.com` does not match `notexample.com`). **There is no field here that can hold a password or a TOTP secret** — the response shape makes leaking one impossible instead of relying on remembering to redact. What it can say is `has_password: true`, which is all that picking a credential needs. A manager that is simply absent is a `notes` entry (carrying its install step), not an `error`. `status`'s `unlocked` is `null` when the CLI is not installed — "not asked" is not "locked".

**login — sign in with a stored credential, in two calls.** Call it **without `confirm`** first: it opens the page, detects the username / password / submit controls, and returns `pending_approval: true` with `fields` + `plan` — **nothing is filled, nothing is submitted**. Re-call with the same arguments plus `confirm=true` after the user approves; only then does it fill and submit. The gate covers *fill* as well as submit, because typing into a page fires its input events and a hostile page can exfiltrate a value the moment it is typed. The password is read server-side from the vault and **never enters the response, the transcript, or any log** — the receipt names the vault item instead. Afterwards `logged_in` is the same model-free DOM + cookie-NAME probe as `web_read`'s `check_login`, and it is **tri-state**: `null` means the probe could not answer, which is not `false`. Cookies are kept under the `session_id` you pass, so later fetches reuse the session. CAPTCHA / 2FA are **not** solved — hand those back to the user. Override detection with `username_index` / `password_index` / `submit_index` when the form is unusual.

## Local data and privacy (~/.dhole, relocatable via DHOLE_HOME)

| Location | Contents |
|---|---|
| `cache.db` | Fetched body text (plaintext SQLite), TTL 1h by default (docs auto-raised to 24h, article 6h) |
| `sessions.db` | Cookie jar, **contains cookie values = credentials**, 24h expiry |
| `models/<name>/` | Rerank models (copy the whole directory for offline reuse) |
| `config/reranker.json` | Rerank model selection |
| `tasks.db` | Recorded recipes (`url` + `actions`) and watch baselines (page text). **No credentials** — a recipe stores the chain, never a session id |
| `usage.jsonl` | With `DHOLE_USAGE_LOG=1`: call log (tool / duration / redacted errors) plus the audit channel — attach events, HTTP writes (non-GET), browser submits (names and outcome only, never parameter values) |

No telemetry: nothing leaves the process except traffic to the sites you point it at. Nuke `~/.dhole` for a full cleanup. Two 16.x leftovers worth naming to the user, because both were written by mechanisms 17.0 deleted and neither is read any more: `search_proxies.json` (the proxy pool) still holds its credentials in plaintext, and `repair.py` is still a script that kills every dhole process and force-reinstalls if anyone runs it. Deleting the two files is the fix.
