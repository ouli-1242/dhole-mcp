# Tool Deep Behavior

Tool descriptions cover parameters; this volume covers **how the tools actually behave** — tiers, limits, and the parts most likely to be misread. Value ranges still live in `tools/list`.

## smart_fetch: the fetch chain and interaction

**Three-tier escalation**: direct HTTP (~1s) → stealthy browser when blocked or facing a JS shell → archive.org snapshot on final failure. `escalation_path` records where the call actually landed. Two no-escalation exceptions (since 16.0): 500/502 go to archive fallback or an honest error instead of the browser; data documents (XML / images / PDF) are never sent to the browser. On strongly anti-bot sites, send subsequent calls with `force_fetcher='stealthy'` and skip re-walking the chain.

**Shell-page judgment**: `content_ok=true` only means 2xx + non-empty. Check `total_extracted_chars` and `page_type` first — a few-hundred-char "success" is a placeholder. Retry true shells with stealthy; login-walled sites (out of scope) and DataDome / Akamai / interactive Turnstile cannot be beaten — say so honestly.

**Trimming and pagination**: `focus` returns only BM25-relevant blocks (header notes "showing N of M blocks"; `focus:''` gets the full page); after truncation, continue with `offset=next_offset`, re-passing the same focus. `max_content_chars` spans 500–200000. The 50MB body cap gates BEFORE download — oversized bodies fail immediately and raising `timeout` won't help.

**PDF**: `pages=` only trims **extraction** — the file still downloads whole, so raise `timeout` (default 30s, max 120s; it is the wall-clock budget for the whole call) for big files. Responses carry `table_of_contents` (titles + page ranges) — pick pages from it. Scanned or CID-broken PDFs auto-OCR.

**`css_selector` applies to HTML only**: on XML/JSON/plain text it matches nothing — the whole document comes back and `summary` states the selector was not applied. Don't mistake the full payload for your slice.

**Page interaction (actions, via the stealthy browser)**: actions run in order; each leaves a receipt in `metadata.actions`.
- `{scroll:n}` or `{scroll:{steps,selector,ms_per_step}}`: scrolls to the bottom, then polls the DOM until the page **stops growing** (chases the new bottom, two quiet rounds ends it), capped at a 20s budget — and it dispatches a real `scroll` event, because when the viewport is tall and content short, `scrollTo` fires nothing and event-only lazy-loaders never wake
- Sites gating lazy-load on `event.isTrusted` ignore synthetic scrolls — use `{click:'<css of the load-more button>'}`; forms take `{fill:{selector,text}}` + `{press:'Enter'}`; `{wait:ms}` / `{wait_selector:'css'}` (or `{selector,count,state,timeout_ms}`) hold for elements

**Structured extraction (schema)**: repeated records REQUIRE a "container selector + sub-schema" — sub-selectors evaluate **inside** each container, one record per container. A bare top-level selector returns the page-wide union; "which tag belongs to which quote" simply doesn't exist in that output. Max 200 records per field, 8 nesting levels; only `selector` / `attribute` / `type` / `properties` / `items` are read — any other JSON-Schema keyword is ignored; sub-selectors pass the same injection validation. Example:

```json
{"properties": {"quotes": {"type": "array", "selector": ".quote",
  "properties": {"text": {"selector": ".text"}, "tags": {"type": "array", "selector": ".tag"}}}}}
```

**Non-GET is one-shot**: POST/PUT/DELETE/PATCH are never retried (the previous call may already be committed — re-sending writes twice), never cached, never escalated to the browser, never answered from snapshots — browsers only navigate (GET) and a snapshot is one GET from some past day; neither answers your write. `HEAD` returns status + declared length + type only. Body cap 256KB: this tool retrieves things, it does not upload them.

**Credentials and sessions**:
- `options.auth` shapes: `{type:'basic',username,password}` / `{type:'bearer',token}` / `{type:'header',name,value}` (for `X-API-Key`-style headers). It only places the header — **no OAuth flows, no token refresh**; when a token expires, supply a fresh one. Credential **values** are never echoed anywhere; bodies fetched with credentials are cached per-credential (an admin view is never replayed to anonymous requests)
- **Credentials do not survive cross-origin redirects**: cookies and `Authorization` go only to the origin you named; a hop to another domain strips them (same-host hops and hosts already holding that cookie in the jar keep them)
- The `options.session_id` cookie jar is **HTTP-tier only** (per host, 24h expiry; responses report cookie names, never values); the stealthy browser keeps its own cookies in its warm context and the two tiers do NOT share — a `force_fetcher='stealthy'` call will not carry HTTP-tier login state. Roster and cleanup: `close_session` (no args = roster, `session_id=` forgets one, `all=true` clears all)
- `<meta http-equiv=refresh>` is followed on both tiers (the HTTP tier may not follow delayed refreshes; `metadata.meta_refresh` records the hop origin)

**Conditional requests**: `if_none_match` / `if_modified_since` are HTTP-tier only; a 304 is **success** (`not_modified=true`, empty body, no error) — never escalate it to the browser. Responses carry `cache_validators`; pass them back verbatim next time.

**Misc**: `urls=[...]` batches in parallel; `include_links` caps each link class at 30/20/20 (`max_links` up to 100) with `total_found` reporting the pre-truncation count; explicit `options.proxy` gets a 5s TCP precheck — an unreachable proxy returns `proxy_unreachable` with no request sent; `allow_private` (true = loopback, or an explicit host list) is for local dev services.

## smart_search: engines, ranking, dates

**Engine matrix** (14 keyless + 4 keyed; max 9 per search):

| Engine | Default pool | Direct from CN | Notes |
|---|---|---|---|
| `baidu` / `bing` / `sogou` / `yandex` | ✔ | ✔ | `sogou` is IP-rate-limited |
| `bing_global` / `brave` | ✔ | proxy needed | Nearly disjoint results from the CN editions |
| `duckduckgo` (`ddg`) / `yahoo` / `wikipedia` / `grokipedia` / `mwmbl` | opt-in | proxy needed | `mwmbl` has narrow coverage, uneven relevance |
| `baidu_baike` / `so360` (`360`) / `sogou_weixin` | opt-in | ✔ | `sogou_weixin` is the WeChat-article vertical; `baidu_baike` is narrow |
| `brightdata` / `tavily` / `exa` / `bocha` | keyed (off by default) | — | **Per-call billing, quota deducted on dispatch** — only when the user names them |

**Ranking**: a local cross-encoder reranks (bge-zh by default); while it's still loading in a fresh process, `rerank_mode=merge` (consensus ordering, slightly weaker) — one retry restores `neural`. `relevance_score` is normalized (top is always 1.0); `min_raw_relevance` is what can judge "the whole round is irrelevant" — the raw-score distribution (bge-zh, measured): off-topic ~1e-4, marginal ~0.2, true hits 0.93+. **Start at 0.1**; hard negatives on the same topic sit at p90 0.18, so higher starts killing good results. Recalibrate after `dhole model use`. `fetch_hint` reports each round's raw-score span. `engines_consensus` counts independent index FAMILIES (5 total), not raw hits.

**Dates**: only `bing` / `bing_global` / `brave` (plus opt-in `ddg` / `yahoo`, keyed `bocha` / `tavily`) accept dates, and only as day/week/month/year presets. `after=<date>` is widened to the narrowest preset covering the window (`date_filter` reports what was sent and which engines were never asked); `before` is rejected outright — search results carry no publish dates, so "older-than" is both unaskable and unverifiable. Date-bounded increments belong to `feed_fetch(since=)`. There is **no `total_estimate`** — do not look for it, do not invent one.

**No SLA**: keyless engines are direct scrapes of public search results (unauthorized, unquota'd) — when providers change layout or ban IPs, they degrade **silently**. For reliability: a search proxy (`DHOLE_SEARCH_PROXY` or the proxy pool) or a keyed backend.

## smart_crawl: discovery economics and politeness

- `discover_only=true` returns a URL map without body extraction, but **expanding the link graph still costs one request per page** (up to `max_pages`). For a one-request map: `options.sitemap=true` (whole site) or `discover_only=true, max_pages=1` (start page's links only)
- After mapping, fetch picks with `crawl_urls=[...]`; dropped entries are reported honestly (`urls_supplied` / `urls_deduped` / `urls_dropped_off_domain` / `urls_dropped_over_max_pages`)
- `path_include` / `path_exclude` match **path subtrees**: `'/docs'` matches `/docs` and everything below it but NOT `/docs-old`; the only wildcard is a trailing `/*`
- Budget: `max_total_chars` hard-caps at 1,000,000 and governs **discovery-driven progress**; pages named in `crawl_urls` are always fetched in full regardless of it (measured: a 2000 budget returned two pages totaling ~7000 chars) — cap single pages with `max_content_chars_per`
- Politeness has two knobs: `concurrency` (requests in flight) + `options.delay` (per-host interval in seconds, cap 60). The site's `robots.txt` `Crawl-delay` only raises the interval (actual value in `summary`); `ignore_robots=true` waives that too. Note Python's robotparser only reads whole seconds — `Crawl-delay: 0.5` counts as no request at all
- List pages return a **structured link listing** instead of body text (`page_type=list`) — intentional, not an extraction bug
- robots.txt is honored page-by-page: links known to be disallowed are dropped before enqueueing

## The remaining tools

**screenshot**: multimodal agents receive the image inline; text-only agents pass `options.save_to` (response reports the absolute path + byte size; parent dirs are created; a mistyped name over a non-image existing file is refused to protect it, while same-named image files may be overwritten).

**parse**: the only entry point for local documents (MCP-only clients have no file tools). Extensions: .html/.htm/.xhtml/.docx/.xlsx/.csv/.pdf/.md/.markdown/.txt/.json/.yaml/.yml/.pptx/.odt; .json/.yaml are **validated** (a broken file is one error line, not a byte dump); unlisted extensions (.log/.rst/.ini…) are refused with a rename-to-.txt hint. Mojibake → explicit `encoding=`. Parts a text tool cannot carry (pptx speaker notes, layout, images, comments) are declared in the **first line** of output, never silently dropped. HTML tables share one extraction function across parse / smart_fetch / smart_crawl — byte-identical markdown tables.

**feed_fetch**: batch RSS/Atom, newest first; item summaries are 500-char previews (`summary_truncated=true`) — fetch the item URL with smart_fetch for full text. `since=` for incremental polls (ISO date or RFC-822 timestamp); passing a **site homepage** works — it follows the page's own alternate links to the feed (`discovered_from` records it); discovered addresses pass the SSRF guard too (a malicious page cannot weaponize auto-discovery against your intranet). Unparseable feeds fail with one plain-language verdict plus the candidates tried — don't re-parse XML yourself.

**resolve_url**: follows HTTP redirects and body meta-refresh to the final address without extracting content. Resolve t.co / bit.ly / tracking links before deciding whether to fetch.

**cache_clear**: wipes the body cache **and forgets robots verdicts and every session cookie jar** (whoever wants the cache cleared is exactly who needs to know login state is dropping); `engine_state=true` additionally resets engine cooldowns. To forget a single session, use `close_session`.

**close_session**: no args = the roster (id, hosts, cookie names, time to expiry, browser open or not); cookie values never leave the process.

## Local data and privacy (~/.dhole, relocatable via DHOLE_HOME)

| Location | Contents |
|---|---|
| `cache.db` | Fetched body text (plaintext SQLite), TTL 1h by default (docs auto-raised to 24h, article 6h) |
| `sessions.db` | Cookie jar, **contains cookie values = credentials**, 24h expiry |
| `models/<name>/` | Rerank models (copy the whole directory for offline reuse) |
| `config/reranker.json` | Rerank model selection |
| `search_proxies.json` | Proxy pool, **credentials stored in plaintext** (masked in `list` output) |

No telemetry: nothing leaves the process except traffic to the sites you point it at. Nuke `~/.dhole` for a full cleanup.
