# Call Pattern Handbook (copy-ready)

Every pattern below is live-tested (16.1 full pass) or wire-test verified (the 17.0 additions: snapshot / index actions / submit gate / element screenshot / `focus_mode`). **Read section 0 first** — misplaced parameters are the single most common way to fumble this MCP.

## 0. Parameter placement rules (correct vs incorrect)

Every parameter is a **top-level argument** of the tool it belongs to. Nothing is nested: a wrapper object is rejected, not read.

```json
✅ {"url": "https://example.com", "focus": "pricing"}
❌ {"url": "https://example.com", "options": {"focus": "pricing"}}   // rejected outright
❌ {"url": "...", "since": "2026-09-01"}                             // since= belongs to feed_fetch, not smart_fetch
```

A rejection names the key and lists the keys that tool accepts — follow the error rather than guessing a placement.

## smart_fetch

**Basic fetch + focus** (BM25 returns only relevant blocks; the header says "showing N of M blocks"; `focus:""` gets the full page):
```json
{"url": "https://example.com/pricing", "focus": "enterprise pricing"}
```

**Paginate a long read** (`next_offset` comes from the previous response; **repeat the same focus** — focus runs after cache, no re-request):
```json
{"url": "https://example.com/long-doc", "focus": "deployment steps", "offset": 40000}
```

**Batch in parallel** (each URL gets its own result object):
```json
{"urls": ["https://a.com/post/1", "https://b.com/post/2"]}
```

**Conditional request** (step 1: plain GET, grab `cache_validators`; step 2: send the etag back — a 304 is SUCCESS, `not_modified=true` with an empty body):
```json
{"url": "https://example.com", "if_none_match": "\"6aba938b-2c9\""}
```

**Write request** (one-shot: no retry, no cache, no browser escalation; body ≤256KB):
```json
{"url": "https://api.example.com/orders", "method": "POST", "body": "{\"item\": 1, \"qty\": 2}", "content_type": "application/json"}
```

**Credentials** (three shapes; values are never echoed; they do not survive cross-origin redirects; 401 responses distinguish "rejected" from "not sent"):
```json
{"url": "https://api.example.com/me", "auth": {"type": "bearer", "token": "eyJ..."}}
{"url": "https://api.example.com/me", "auth": {"type": "basic", "username": "u", "password": "p"}}
{"url": "https://api.example.com/me", "auth": {"type": "header", "name": "X-API-Key", "value": "key-123"}}
```

**Session cookies** (same `session_id` re-sends that host's cookies automatically, 24h lifetime; responses report cookie NAMES only, never values; roster/cleanup via `close_session`):
```json
{"url": "https://example.com/login-check", "session_id": "sess1", "cookies": [{"name": "token", "value": "abc"}]}
```

**Lazy-load interaction** (one action key per object; `scroll` waits for the page to stop growing — sites that gate lazy-load on `event.isTrusted` need `click` instead; every action leaves a receipt in `metadata.actions`):
```json
{"url": "https://example.com/feed", "force_fetcher": "stealthy", "actions": [
  {"scroll": 3},
  {"click": "button.load-more"},
  {"wait_selector": {"selector": ".item", "count": 40, "state": "visible"}},
  {"fill": {"selector": "#search", "text": "keywords"}},
  {"press": "Enter"}
]}
```

**Act without selectors — snapshot, then index addressing** (step 1 lists interactive elements numbered in document order; step 2 acts by index; `expect` verifies the element's visible name, so a stale index fails loudly instead of clicking the wrong thing — re-snapshot when it fires):
```json
{"url": "https://example.com/settings", "force_fetcher": "stealthy", "actions": [{"snapshot": true}]}
{"url": "https://example.com/settings", "force_fetcher": "stealthy", "actions": [
  {"fill": {"index": 3, "text": "new value"}},
  {"click": {"index": 12, "expect": "Save"}}
]}
```

**Form submission goes through the approval gate** (without `confirm`, the submit PAUSES with a `pending_approval` receipt and nothing is sent — describe the submission to the user, then re-run the **whole chain** with `confirm: true` only after they said yes; each call reloads the page, so the re-run must redo every prior step; paused and confirmed runs both land in the audit log):
```json
{"url": "https://shop.example.com/checkout", "force_fetcher": "stealthy", "actions": [
  {"fill": {"index": 3, "text": "note to seller"}},
  {"submit": {"index": 8}}
]}
// after the user confirmed:
{"url": "https://shop.example.com/checkout", "force_fetcher": "stealthy", "actions": [
  {"fill": {"index": 3, "text": "note to seller"}},
  {"submit": {"index": 8, "confirm": true}}
]}
```

**Element screenshot + QR decode** (always available in 17.0; `decode_qr` lists decoded texts in the receipt):
```json
{"url": "https://example.com/ticket", "force_fetcher": "stealthy", "actions": [
  {"screenshot": {"selector": ".qr-panel", "decode_qr": true}}
]}
```

**Answer located to one paragraph** (`focus_mode="best"` returns the single highest-scoring block plus its heading instead of a block list; picking is semantic with `focus_engine="neural"`, BM25 by default):
```json
{"url": "https://docs.example.com/guide", "focus": "retry policy", "focus_mode": "best"}
```

**Choosing the focus scorer for one call** (`focus_engine` — a pure per-call choice in 17.0: omitted or `"bm25"` is keyword focus, `"neural"` is the local cross-encoder):
```json
{"url": "https://docs.example.com/guide", "focus": "retry policy", "focus_engine": "bm25"}
```
The same key exists on `smart_crawl`, where it matters more: the semantic path costs ~12.5s of CPU per page times `max_pages`, so the cheap default is what runs when nobody names it. `"neural"` with the model missing is **refused** (`neural_focus_model_missing`) instead of silently downloading ~280MB — the first neural `smart_search` fetches it once (resumable), or re-run with `"bm25"`.

**Structured extraction** (repeated records REQUIRE a "container selector + sub-schema": sub-selectors evaluate inside each container, one record per container; a bare top-level selector returns a page-wide union with no record attribution):
```json
{"url": "https://quotes.toscrape.com", "schema": {"properties": {"quotes": {
  "type": "array", "selector": ".quote",
  "properties": {"text": {"selector": ".text"}, "author": {"selector": ".author"}, "tags": {"type": "array", "selector": ".tag"}}
}}}}
```

**PDF** (read `table_of_contents` in the response first, then pick `pages`; `pages` trims extraction only — the file downloads whole, so raise `timeout` for big files; scanned PDFs auto-OCR):
```json
{"url": "https://arxiv.org/pdf/1706.03762", "pages": "1-3", "timeout": 60000}
```

**Local dev service** (the SSRF guard rejects private hosts by default; with a proxy running, loopback may be intercepted — see troubleshooting "local services"):
```json
{"url": "http://127.0.0.1:8080/api/health", "allow_private": true}
```

**Force the browser tier** (once a site has shown escalation, skip the chain):
```json
{"url": "https://hard-site.com/page", "force_fetcher": "stealthy"}
```

**CDP attach — login-walled page via the user's real Chrome** (requires the user's one-time setup: copy-profile Chrome with `--remote-debugging-port=9222` — configuration.md "CDP attach"; the FIRST attach to a host is refused with `cdp_authorization_required` and authorizing it is one re-call with `"confirm": true` after the user says yes). `check_login` returns `{logged_in: true/false/null, signals, cookie_names}` — cookie names only, never values; `schema=` cannot combine with `cdp_url`:
```json
{"url": "https://portal.example.com/dashboard", "cdp_url": "http://127.0.0.1:9222", "check_login": true}
```

**Explicit proxy** (5s TCP precheck; an unreachable proxy returns `proxy_unreachable` before any request):
```json
{"url": "https://example.com", "proxy": "http://127.0.0.1:7890"}
```

## smart_search

**Vertical + dated + threshold + pull full text** (`before` does not exist; `after` is widened to a day/week/month/year preset — read `date_filter.note`; `fetch_content` also fetches the top 3 results' full text):
```json
{"query": "MCP streamable HTTP spec", "engines": ["bing", "so360"], "after": "2026-09-01", "min_raw_relevance": 0.1, "fetch_content": true}
```

When searching without fetching: rank by `relevance_score` / `fetch_relevance` (high first), and treat `engines_consensus: 1 of N` as unverified — `smart_fetch` before citing.

## smart_crawl (three-step method)

```json
// Step 1: whole-site URL map in one request
{"url": "https://docs.example.com", "discover_only": true, "sitemap": true}
// Step 2: scope the map to a subtree ('/guide' matches /guide and below, NOT /guide-old; the only wildcard is a trailing /*)
{"url": "https://docs.example.com", "discover_only": true, "path_include": "/guide", "max_pages": 30, "delay": 1}
// Step 3: fetch the picks (named pages are ALWAYS fetched in full — max_total_chars does not stop them; cap single pages with max_content_chars_per)
{"url": "https://docs.example.com", "crawl_urls": ["https://docs.example.com/guide/a", "https://docs.example.com/guide/b"], "max_content_chars_per": 8000}
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
{"url": "https://example.com", "save_to": "C:/tmp/page.png", "full_page": true}
```
Agents that can see images receive it inline; text-only agents read the absolute path from `save_to` and open it with their own image tool. The tool also takes `selector` (element crop) and `decode_qr: true` (local QR decode) — both always available in 17.0.

## The remaining four

```json
// parse: the entry point for local documents in a client with no file tools of its own, and
// the only safe one for a zip/container format or any file whose encoding you don't know.
// Pass encoding explicitly for mojibake (Shift_JIS/EUC-KR are outside auto-detection)
{"file_path": "C:/docs/report.docx"}
{"file_path": "C:/docs/japanese.txt", "encoding": "shift_jis"}
// resolve_url: resolve short links before deciding to fetch
{"url": "https://t.co/xxxx"}
// cache_clear: wipes body cache — AND robots verdicts and every session cookie jar; engine_state also resets engine cooldowns
{"engine_state": true}
// close_session: no args = session roster; name one to forget it; all=true clears everything
{"all": true}
```

## The stateful four (always registered in 17.0)

Nothing is nested here either — every parameter of every tool sits at the top level of the call.

```json
// recipe: record a chain once — validated at save time by the same validator the executor uses,
// so a chain that would be refused at run time cannot be stored
{"op": "save", "name": "price-watch", "url": "https://shop.example.com/item",
 "actions": [{"click": {"index": 12, "expect": "Show price"}}], "note": "reveals the member price"}
// replay it (session_id is per-run and never stored); a non-zero actions_failed means the page changed — re-save
{"op": "run", "name": "price-watch", "session_id": "sess1"}
// see what is stored (use_count / last_ok show a stale recipe without running it); forget one
{"op": "list"}
{"op": "delete", "name": "price-watch"}

// watch: add, then check. The FIRST check only stores the baseline and answers changed=null
{"op": "add", "name": "changelog", "url": "https://example.com/changelog", "focus": "release notes"}
{"op": "check", "name": "changelog"}
{"op": "list"}
{"op": "remove", "name": "changelog"}

// vault: which manager answered, then the items that fit a host — metadata only, never values
{"op": "status"}
{"op": "match", "url": "https://portal.example.com/login"}
{"op": "list", "query": "portal", "source": "bitwarden"}

// login: step 1 detects the form and fills NOTHING (pending_approval) — show this plan to the user
{"url": "https://portal.example.com/login", "credential": "portal", "session_id": "sess1"}
// step 2, only after the user approves: the SAME arguments plus confirm=true
{"url": "https://portal.example.com/login", "credential": "portal", "session_id": "sess1", "confirm": true}
```
