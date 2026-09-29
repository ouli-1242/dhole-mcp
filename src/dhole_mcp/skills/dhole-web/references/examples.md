# Call Pattern Handbook (copy-ready)

Every pattern below is live-tested. **Read section 0 first** — misplaced parameters are the single most common way to fumble this MCP.

## 0. Parameter placement rules (correct vs incorrect)

Knobs live in the `options` bag. For `smart_fetch` / `smart_search` / `smart_crawl` / `screenshot`, same-named top-level parameters are a compat read (top-level wins when both are given). Three iron exceptions:

```json
✅ {"url": "https://example.com", "options": {"focus": "pricing"}}
❌ {"options": {"url": "https://example.com"}}        // smart_fetch's url MUST be top-level; unreadable inside options
❌ {"url": "...", "options": {"crawl_urls": ["..."]}} // smart_crawl's discover_only/crawl_urls/focus are top-level ONLY
❌ {"url": "...", "options": {"since": "2026-09-01"}} // parse/feed_fetch/resolve_url/cache_clear/close_session take top-level only
```

Rejections list the keys the tool actually accepts — follow the error.

## smart_fetch

**Basic fetch + focus** (BM25 returns only relevant blocks; the header says "showing N of M blocks"; `focus:""` gets the full page):
```json
{"url": "https://example.com/pricing", "options": {"focus": "enterprise pricing"}}
```

**Paginate a long read** (`next_offset` comes from the previous response; **repeat the same focus** — focus runs after cache, no re-request):
```json
{"url": "https://example.com/long-doc", "options": {"focus": "deployment steps", "offset": 40000}}
```

**Batch in parallel** (each URL gets its own result object):
```json
{"urls": ["https://a.com/post/1", "https://b.com/post/2"]}
```

**Conditional request** (step 1: plain GET, grab `cache_validators`; step 2: send the etag back — a 304 is SUCCESS, `not_modified=true` with an empty body):
```json
{"url": "https://example.com", "options": {"if_none_match": "\"6aba938b-2c9\""}}
```

**Write request** (one-shot: no retry, no cache, no browser escalation; body ≤256KB):
```json
{"url": "https://api.example.com/orders", "options": {"method": "POST", "body": "{\"item\": 1, \"qty\": 2}", "content_type": "application/json"}}
```

**Credentials** (three shapes; values are never echoed; they do not survive cross-origin redirects; 401 responses distinguish "rejected" from "not sent"):
```json
{"url": "https://api.example.com/me", "options": {"auth": {"type": "bearer", "token": "eyJ..."}}}
{"url": "https://api.example.com/me", "options": {"auth": {"type": "basic", "username": "u", "password": "p"}}}
{"url": "https://api.example.com/me", "options": {"auth": {"type": "header", "name": "X-API-Key", "value": "key-123"}}}
```

**Session cookies** (same `session_id` re-sends that host's cookies automatically, 24h lifetime; responses report cookie NAMES only, never values; roster/cleanup via `close_session`):
```json
{"url": "https://example.com/login-check", "options": {"session_id": "sess1", "cookies": [{"name": "token", "value": "abc"}]}}
```

**Lazy-load interaction** (one action key per object; `scroll` waits for the page to stop growing — sites that gate lazy-load on `event.isTrusted` need `click` instead; every action leaves a receipt in `metadata.actions`):
```json
{"url": "https://example.com/feed", "options": {"force_fetcher": "stealthy", "actions": [
  {"scroll": 3},
  {"click": "button.load-more"},
  {"wait_selector": {"selector": ".item", "count": 40, "state": "visible"}},
  {"fill": {"selector": "#search", "text": "keywords"}},
  {"press": "Enter"}
]}}
```

**Structured extraction** (repeated records REQUIRE a "container selector + sub-schema": sub-selectors evaluate inside each container, one record per container; a bare top-level selector returns a page-wide union with no record attribution):
```json
{"url": "https://quotes.toscrape.com", "options": {"schema": {"properties": {"quotes": {
  "type": "array", "selector": ".quote",
  "properties": {"text": {"selector": ".text"}, "author": {"selector": ".author"}, "tags": {"type": "array", "selector": ".tag"}}
}}}}}
```

**PDF** (read `table_of_contents` in the response first, then pick `pages`; `pages` trims extraction only — the file downloads whole, so raise `timeout` for big files; scanned PDFs auto-OCR):
```json
{"url": "https://arxiv.org/pdf/1706.03762", "options": {"pages": "1-3", "timeout": 60000}}
```

**Local dev service** (the SSRF guard rejects private hosts by default; with a proxy running, loopback may be intercepted — see troubleshooting "local services"):
```json
{"url": "http://127.0.0.1:8080/api/health", "options": {"allow_private": true}}
```

**Force the browser tier** (once a site has shown escalation, skip the chain):
```json
{"url": "https://hard-site.com/page", "options": {"force_fetcher": "stealthy"}}
```

**Explicit proxy** (5s TCP precheck; an unreachable proxy returns `proxy_unreachable` before any request):
```json
{"url": "https://example.com", "options": {"proxy": "http://127.0.0.1:7890"}}
```

## smart_search

**Vertical + dated + threshold + pull full text** (`before` does not exist; `after` is widened to a day/week/month/year preset — read `date_filter.note`; `fetch_content` also fetches the top 3 results' full text):
```json
{"query": "MCP streamable HTTP spec", "options": {"engines": ["bing", "sogou_weixin"], "after": "2026-09-01", "min_raw_relevance": 0.1, "fetch_content": true}}
```

When searching without fetching: rank by `relevance_score` / `fetch_relevance` (high first), and treat `engines_consensus: 1 of N` as unverified — `smart_fetch` before citing.

## smart_crawl (three-step method)

```json
// Step 1: whole-site URL map in one request
{"url": "https://docs.example.com", "discover_only": true, "options": {"sitemap": true}}
// Step 2: scope the map to a subtree ('/guide' matches /guide and below, NOT /guide-old; the only wildcard is a trailing /*)
{"url": "https://docs.example.com", "discover_only": true, "options": {"path_include": "/guide", "max_pages": 30, "delay": 1}}
// Step 3: fetch the picks (named pages are ALWAYS fetched in full — max_total_chars does not stop them; cap single pages with max_content_chars_per)
{"url": "https://docs.example.com", "crawl_urls": ["https://docs.example.com/guide/a", "https://docs.example.com/guide/b"], "options": {"max_content_chars_per": 8000}}
```

List pages return a structured link listing (`page_type=list`), not body text — follow it to the content pages.

## feed_fetch (incremental polling)

```json
// First call: passing the site homepage works too — it follows the page's own alternate link to the feed (discovered_from records this)
{"urls": ["https://blog.example.com/"], "max_items": 20}
// Every later round: since= the newest `published` from last round; only increments return (items_older_than_since counts what was filtered)
{"urls": ["https://blog.example.com/feed.xml"], "since": "2026-09-29T11:14:45+00:00"}
```

## screenshot

```json
{"url": "https://example.com", "options": {"save_to": "C:/tmp/page.png", "full_page": true}}
```
Multimodal agents receive the image inline; text-only agents read the absolute path from `save_to` and open it with their own image tool.

## The remaining four (top-level parameters only)

```json
// parse: the only entry point for local documents; pass encoding explicitly for mojibake (Shift_JIS/EUC-KR are outside auto-detection)
{"file_path": "C:/docs/report.docx"}
{"file_path": "C:/docs/japanese.txt", "encoding": "shift_jis"}
// resolve_url: resolve short links before deciding to fetch
{"url": "https://t.co/xxxx"}
// cache_clear: wipes body cache — AND robots verdicts and every session cookie jar; engine_state also resets engine cooldowns
{"engine_state": true}
// close_session: no args = session roster; name one to forget it; all=true clears everything
{"all": true}
```
