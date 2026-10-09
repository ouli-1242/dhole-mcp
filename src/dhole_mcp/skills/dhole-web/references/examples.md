# Call Pattern Handbook (copy-ready)

Every pattern below is tested against the live wire. **Read section 0 first** — misplaced parameters are the single most common way to fumble this MCP.

## 0. Parameter placement rules (correct vs incorrect)

Every parameter is a **top-level argument** of the tool it belongs to. Nothing is nested: a wrapper object is rejected, not read.

```json
✅ {"url": "https://example.com", "focus": "pricing"}
❌ {"url": "https://example.com", "options": {"focus": "pricing"}}   // rejected outright
❌ {"url": "...", "since": "2026-09-01"}                             // since= belongs to feed_read, not web_read
```

A rejection names the key and lists the keys that tool accepts — follow the error rather than guessing a placement.

## web_read

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

**A write is not asked of this tool**: POST / PUT / PATCH / DELETE / HEAD go to `web_request` — see its own section below. `web_read` rejects `method=`, and the rejection names the door the capability moved to.

**Credentials** (three shapes; stored once with `cred_defaults`, then every later call to that host uses them; values are never echoed; they do not survive cross-origin redirects; 401 responses distinguish "rejected" from "not sent"). The `"host"` key is not optional — a credential without one is refused, because it would ride to the next site you read:
```json
{"tool": "cred_defaults", "arguments": {"set": {"host": "api.example.com", "auth": {"type": "bearer", "token": "eyJ..."}}}}
{"tool": "cred_defaults", "arguments": {"set": {"host": "api.example.com", "auth": {"type": "basic", "username": "u", "password": "p"}}}}
{"tool": "cred_defaults", "arguments": {"set": {"host": "api.example.com", "auth": {"type": "header", "name": "X-API-Key", "value": "key-123"}}}}
{"url": "https://api.example.com/me"}
```

**Session cookies** (same `session_id` re-sends that host's cookies automatically, 24h lifetime; responses report cookie NAMES only, never values; roster/cleanup via `sys_state`. Cookies you supply are stored per host the same way):
```json
{"tool": "cred_defaults", "arguments": {"set": {"host": "example.com", "cookies": {"token": "abc"}}}}
{"url": "https://example.com/login-check", "session_id": "sess1"}
```

## web_act

`actions` is `web_act`'s argument - `web_read` refuses it, and the chain forces the browser tier, so there is no `force_fetcher` left to say. Every example below was one call to `web_act`.

**Lazy-load interaction** (one action key per object; `scroll` waits for the page to stop growing — sites that gate lazy-load on `event.isTrusted` need `click` instead; every action leaves a receipt in `metadata.actions`):
```json
{"url": "https://example.com/feed", "actions": [
  {"scroll": 3},
  {"click": "button.load-more"},
  {"wait_selector": {"selector": ".item", "count": 40, "state": "visible"}},
  {"fill": {"selector": "#search", "text": "keywords"}},
  {"press": "Enter"}
]}
```

**Act without selectors — snapshot, then index addressing** (step 1 lists interactive elements numbered in document order; step 2 acts by index; `expect` verifies the element's visible name, so a stale index fails loudly instead of clicking the wrong thing — re-snapshot when it fires):
```json
{"url": "https://example.com/settings", "actions": [{"snapshot": true}]}
{"url": "https://example.com/settings", "actions": [
  {"fill": {"index": 3, "text": "new value"}},
  {"click": {"index": 12, "expect": "Save"}}
]}
```

**Hover to reveal, drag to reorder** (hover menus and tooltips render synchronously, so the chain can click what the hover exposed; a drag names BOTH endpoints and each takes selector or index):
```json
{"url": "https://example.com/admin", "actions": [
  {"hover": {"index": 5, "expect": "Orders"}},
  {"click": {"index": 9, "expect": "Refunds"}}
]}
{"url": "https://example.com/board", "actions": [
  {"drag": {"index": 3, "expect": "Task A", "to": {"index": 7}}}
]}
```

**Form submission goes through the approval gate** (without `confirm`, the submit PAUSES with a `pending_approval` receipt and nothing is sent — describe the submission to the user, then re-run the **whole chain** with `confirm: true` only after they said yes; each call reloads the page, so the re-run must redo every prior step; paused and confirmed runs both land in the audit log):
```json
{"url": "https://shop.example.com/checkout", "actions": [
  {"fill": {"index": 3, "text": "note to seller"}},
  {"submit": {"index": 8}}
]}
// after the user confirmed:
{"url": "https://shop.example.com/checkout", "actions": [
  {"fill": {"index": 3, "text": "note to seller"}},
  {"submit": {"index": 8, "confirm": true}}
]}
```

**Element screenshot + QR decode** (always available; `decode_qr` lists decoded texts in the receipt):
```json
{"url": "https://example.com/ticket", "actions": [
  {"screenshot": {"selector": ".qr-panel", "decode_qr": true}}
]}
```

## web_read: focus, structured extraction, PDFs and the tiers

**Answer located to one paragraph** (`focus_mode="best"` returns the single highest-scoring block plus its heading instead of a block list; picking is semantic with `focus_engine="neural"`, BM25 by default):
```json
{"url": "https://docs.example.com/guide", "focus": "retry policy", "focus_mode": "best"}
```

**Choosing the focus scorer for one call** (`focus_engine` — a pure per-call choice: omitted or `"bm25"` is keyword focus, `"neural"` is the local cross-encoder):
```json
{"url": "https://docs.example.com/guide", "focus": "retry policy", "focus_engine": "bm25"}
```
The same key exists on `web_crawl`, where it matters more: the semantic path costs ~12.5s of CPU per page times `max_pages`, so the cheap default is what runs when nobody names it. `"neural"` with the model missing is **refused** (`neural_focus_model_missing`) instead of silently downloading ~280MB — the first neural `web_search` fetches it once (resumable), or re-run with `"bm25"`.

**Structured extraction** (repeated records REQUIRE a "container selector + sub-schema": sub-selectors evaluate inside each container, one record per container; a bare top-level selector returns a page-wide union with no record attribution):
```json
{"url": "https://quotes.toscrape.com", "schema": {"properties": {"quotes": {
  "type": "array", "selector": ".quote",
  "properties": {"text": {"selector": ".text"}, "author": {"selector": ".author"}, "tags": {"type": "array", "selector": ".tag"}}
}}}}
```

**Let the page propose the schema** (no selectors: `schema="auto"` detects JSON-LD / tables / lists / OpenGraph, and when the page has a repeated structure it adds a `_schema` you can paste back into `schema=` for the sibling pages of the same site):
```json
{"url": "https://books.toscrape.com/", "schema": "auto"}
```
Read the reply's `_schema` as a starting point, not a verdict: the container is the most-repeated `tag.class` group and each field is named from its class's last token, so a field called `link` is "an `<a class=\"link\">`", not "the link you wanted". No `_schema` key means the page had nothing repeated — an honest "no structure here", not a failure.

**Two layers in one call** (the items live on their own pages, so `schema` nesting cannot reach them — `nested_selector` names the list entries on THIS page, `nested_fields` says what to read on each page they point to; 8 links by default, ceiling 30, robots checked per link):
```json
{"url": "https://quotes.toscrape.com/", "nested_selector": "div.quote",
 "nested_fields": [{"name": "author", "selector": "h3"}, {"name": "born", "selector": "span.author-born-date"}, {"name": "description", "selector": "div.author-description"}]}
```
Measured: one call, 8 grouped records, `summary` carrying `nested: 8 links expanded, rungs=2, ok 8 / empty 0 / failed 0`. Name the element that HOLDS the value: `{"name": "description", "selector": "p", "type": "array"}` on that same author page returns Login / copyright / credit lines, which is a hit that is not the answer.

```json
// wrong — nine calls that each re-derive the links the first call already had:
{"url": "https://quotes.toscrape.com/"}
{"url": "https://quotes.toscrape.com/author/Albert-Einstein", "schema": "..."}
// right — the fan-out is the tool's job now, and it checks robots per link:
{"url": "https://quotes.toscrape.com/", "nested_selector": "div.quote", "nested_fields": {"author": "h3"}}
// more than 30 items: take the href list on the first page, then batch it — schema= combines with urls=
{"url": "https://quotes.toscrape.com/", "schema": {"properties": {"hrefs": {"type": "array", "selector": "a[href^=\"/author/\"]", "attribute": "href"}}}}
{"urls": ["https://quotes.toscrape.com/author/Albert-Einstein", "…"], "schema": {"properties": {"author": {"selector": "h3"}}}}
```

**PDF** (read `table_of_contents` in the response first, then pick `pages`; `pages` trims extraction only — the file downloads whole, and the download is ONE attempt of at most 30s, so a bigger `timeout=` covers parsing and OCR after it, not a slower connection; scanned PDFs auto-OCR):
```json
{"url": "https://arxiv.org/pdf/1706.03762", "pages": "1-3", "timeout": 60000}
```

**Local dev service** (the SSRF guard rejects private hosts until the waiver is stored; with a proxy running, loopback may be intercepted — see troubleshooting "local services"):
```json
{"tool": "cred_defaults", "arguments": {"set": {"allow_private": true}}}
{"url": "http://127.0.0.1:8080/api/health"}
```

**Force the browser tier** (once a site has shown escalation, skip the chain):
```json
{"url": "https://hard-site.com/page", "force_fetcher": "stealthy"}
```

**How hard to hide that it is a browser** (`anti_detect` is the only fingerprint dial — `off` / `basic` / `auto` / `aggressive`, no per-knob override. `auto` is already the tested configuration, so the two worth writing are `aggressive` when a site scores the subresources dhole trims, and `off` when the hardening itself is what trips it):
```json
{"url": "https://hard-site.com/page", "anti_detect": "aggressive"}
{"url": "https://legacy-app.example.com/report", "anti_detect": "off"}
```

**CDP attach — login-walled page via the user's real Chrome** (requires the user's one-time setup: copy-profile Chrome with `--remote-debugging-port=9222` — configuration.md "CDP attach"; the FIRST attach to a host is refused with `cdp_authorization_required` and authorizing it is one re-call with `"confirm": true` after the user says yes). `check_login` returns `{logged_in: true/false/null, signals, cookie_names}` — cookie names only, never values; `schema=` cannot combine with `cdp_url`:
```json
{"url": "https://portal.example.com/dashboard", "cdp_url": "http://127.0.0.1:9222", "check_login": true}
```

**Proxy** (stored, not passed: a stored proxy is credited as an explicit one, so it gets the 5s TCP precheck and an unreachable proxy returns `proxy_unreachable` before any request):
```json
{"tool": "cred_defaults", "arguments": {"set": {"proxy": "http://127.0.0.1:7890"}}}
{"url": "https://example.com"}
```

## web_request

**JSON POST** (sent once: the transport will not retry it, the answer is not cached, and no browser replays it — a replay answers about a GET to that URL, not about your write; body ≤256KB):
```json
{"url": "https://api.example.com/orders", "method": "POST", "body": "{\"item\": 1, \"qty\": 2}", "content_type": "application/json"}
```

**A dict body is a form** unless the type says json — `{"item": 1}` arrives as `item=1`, which is what an HTML form endpoint expects. Naming the type is how you ask for JSON instead:
```json
{"url": "https://api.example.com/orders", "method": "POST", "body": {"item": 1, "qty": 2}, "content_type": "application/json"}
```

**PUT / PATCH / DELETE** take the same shape. `method` is required and must be one of the five: a typo (`POSET`) is refused before anything leaves, because sending a verb the caller did not name is worse than sending nothing.
```json
{"url": "https://api.example.com/orders/7", "method": "DELETE"}
```

**HEAD** answers status + declared length + type with no body — the cheap way to ask "did it move" before paying for a read:
```json
{"url": "https://example.com/reports/q3.pdf", "method": "HEAD"}
```

**A write that needs credentials** stores them first; the request carries them and `identity_applied` names what it ran on (the store never echoes values back; the arguments you pass do sit in the transcript — operator pre-configuration keeps a value out entirely):
```json
{"tool": "cred_defaults", "arguments": {"set": {"host": "api.example.com", "auth": {"type": "bearer", "token": "eyJ..."}}}}
{"url": "https://api.example.com/orders", "method": "POST", "body": "{\"item\": 1}", "content_type": "application/json"}
```

**After a timeout, do not resend.** The answer is "unknown", not "failed" — the write may have landed. Read the resource (`web_read`) and let the site say; a GET via `web_read` also shows where the endpoint actually lands before you send at all.

## web_search

**Vertical + dated + threshold + pull full text** (`before` does not exist; `after` is widened to a day/week/month/year preset — read `date_filter.note`; `fetch_content` also fetches the top 3 results' full text):
```json
{"query": "MCP streamable HTTP spec", "engines": ["bing", "so360"], "after": "2026-09-01", "min_raw_relevance": 0.1, "fetch_content": true}
```

When searching without fetching: rank by `relevance_score` / `fetch_relevance` (high first), and treat `engines_consensus: 1 of N` as unverified — `web_read` before citing.

**Spend the auto-fetch budget on corroborated hits** (`fetch_min_consensus: 2` keeps only hits corroborated by ≥2 independent index families; the ones it drops are named in `consensus_skipped` rather than silently lost — with a single-family pool the floor is not applied at all, and the summary says so):
```json
{"query": "MCP streamable HTTP spec", "fetch_content": true, "fetch_min_consensus": 2}
```

## Big site: map, scope, read (web_discover then web_read)

```json
// Step 1: whole-site URL map in one request (web_discover)
{"url": "https://docs.example.com", "sitemap": true}
// Step 2: scope the map to a subtree ('/guide' matches /guide and below, NOT /guide-old; the only wildcard is a trailing /*)
{"url": "https://docs.example.com", "path_include": "/guide", "max_pages": 30}
// Step 3: read the picks - web_read, not a second crawl
{"urls": ["https://docs.example.com/guide/a", "https://docs.example.com/guide/b"], "max_content_chars": 8000}
```

List pages return a structured link listing (`page_type=list`), not body text — follow it to the content pages.

## feed_read (incremental polling)

```json
// First call: passing the site homepage works too — it follows the page's own alternate link to the feed (discovered_from records this)
{"urls": ["https://blog.example.com/"], "max_items": 20}
// Every later round: since= the newest `published` from last round; only increments return (items_older_than_since counts what was filtered)
{"urls": ["https://blog.example.com/feed.xml"], "since": "2026-09-29T11:14:45+00:00"}
```

## Screenshots (inside web_act's action chain)

```json
{"url": "https://example.com", "actions": [{"snapshot": true},
                                            {"screenshot": {"selector": "img.hero", "decode_qr": true}}]}
// whole page, written to disk instead of the transcript (text-only hosts)
{"url": "https://example.com", "actions": [{"screenshot": {"full_page": true, "save_to": "C:/tmp/page.png"}}]}
```
Inline images reach agents that can see them; text-only agents read the absolute path from the action receipt (`saved` + `saved_bytes`). `selector` crops one element, `decode_qr` decodes QR payloads locally — both always available.

## The remaining four

```json
// parse: the entry point for local documents in a client with no file tools of its own, and
// the only safe one for a zip/container format or any file whose encoding you don't know.
// Pass encoding explicitly for mojibake (Shift_JIS/EUC-KR are outside auto-detection)
{"file_path": "C:/docs/report.docx"}
{"file_path": "C:/docs/japanese.txt", "encoding": "shift_jis"}
// where a short link lands: web_read follows redirects (web_resolve was retired)
{"url": "https://t.co/xxxx"}
// sys_state: no arguments = the census (open sessions + cache state)
{}
// show one class without touching anything
{"show": ["cache", "engine_state"]}
// wipe the whole content cache (robots verdicts go with it; login state does NOT)
{"clear": ["cache"]}
// forget one named session — an id that is open is a valid token
{"clear": ["my-shop"]}
// every session: these pause until you say so
{"clear": ["sessions"], "confirm": true}
// engine cooldowns + yield history, for "the same engines keep getting skipped"
{"clear": ["engine_state"]}
```

## The stateful tool (cred_defaults, always registered)

Nothing is nested here either — every parameter of every tool sits at the top level of the call.

```json
// cred_defaults: set the identity once. A credential WITHOUT a host is refused, because it would
// ride along to the next site you read. A bare call = what is in force (names only, never values).
{}
{"set": {"proxy": "http://127.0.0.1:7890", "anti_detect": "aggressive"}}
{"set": {"host": "api.example.com", "auth": {"type": "bearer", "token": "..."}, "cookies": {"sid": "..."}}}
{"set": {"host": ["api.example.com", "intranet.test"], "profile": "work"}}
{"unset": ["proxy"]}
// later calls then name what they ran on: web_read's response carries identity_applied="auth, proxy"
{"url": "https://api.example.com/v1/items"}
```
