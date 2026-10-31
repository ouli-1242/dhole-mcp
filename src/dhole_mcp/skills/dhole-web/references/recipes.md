# Task Recipes

Starting routes for multi-step tasks. Each recipe marks where to pivot on failure — an empty search or a shell page is a route change, not a reason to hammer retries.

## Research a topic and answer

1. `web_search` (default pool = every keyless engine; to lean international, name `bing_global`/`brave`/`duckduckgo`)
2. Pick 2–3 `fetch_relevance: high` hits (ranking is a hint, not a directive — judge by content), `web_read` with `focus=` for full text
3. Cross-check, then answer with URLs cited; content that only appeared in snippets and was never fetched does not count as verified
- Pivot: thin/empty results → troubleshooting "Search"; one shell page → `force_fetcher='stealthy'`

## Track a site's updates (incremental polling)

1. `feed_read(url=<homepage or feed URL>)` — passing the homepage works, it finds the feed itself (`discovered_from`); unparseable feeds fail with a plain-language error, don't re-parse XML yourself
2. Record the newest item's `published`
3. Every later round: `since=<that timestamp>` for increments only (the filtered count is reported)
- Best for: news sites, blogs, changelogs, release pages. Sites without feeds fall back to `web_search` + `freshness`/`after` (a four-preset window, not precise)

## Collect a documentation site

1. `web_discover(url=<docs root>, sitemap=true)` — whole-site URL map from the site's own index (no sitemap: drop `sitemap` and the map walks links, which costs one request per page)
2. `path_include:"/docs"` to scope a subtree (`'/docs'` does not match `/docs-old`), `search:"keywords"` to filter by title/URL
3. `web_read(urls=[...])` to read the picks — one call, no re-discovery, no counters to reconcile
4. List pages return structured link listings (`page_type=list`) — follow them to content pages
- Budget sense: three caps, and the summary names the one that bit — `max_pages`, `max_content_chars_per` per page, `max_total_chars` for the whole run (default 60,000). Map first, then name pages; don't max `max_pages` blindly

## Structured extraction (table/listing page → JSON)

1. `web_read` + `schema`: repeated records need a "container selector + sub-schema" — sub-selectors evaluate inside the container (syntax in references/tools.md)
2. `css_selector`/schema only work on HTML; XML/JSON APIs come back as-is
3. For content behind scroll/click, run `actions` first (scroll waits for the page to stop growing; isTrusted-gated lazy-load needs click) and pair with schema

## Verify a local dev / staging service

1. `cred_defaults(set={allow_private: true})` once (or `["host.name"]` for a named host), then `web_read(url="http://127.0.0.1:PORT/...")` — the SSRF guard rejects private hosts until that waiver exists. For a one-machine waiver instead, set `DHOLE_ALLOW_PRIVATE_HOSTS`. With a proxy running, loopback may be intercepted (dead ports answer 502 instead of connection-refused) — rule the proxy out first (`NO_PROXY` += `127.0.0.1,localhost`); never probe dead ports with stealthy (the browser waits out the 30s timeout)
2. Rendered view / real behavior: a `screenshot` action inside `web_act`'s chain (text-only agents: add `save_to` and read the file); interaction checks via click/fill in the same chain
3. `web_read` settles where server-side redirects actually land (it follows them and reports the landing page)

## Reading PDFs

- Online: `web_read` (`pages="1-5"` trims extraction only — the file downloads whole, and one download is one HTTP attempt of at most 30s, so a bigger `timeout=` pays for parsing and OCR after the bytes land, not for a slower connection; scanned PDFs auto-OCR; pick pages from `table_of_contents`)
- Local: `doc_parse` (also .docx/.xlsx/.xlsm/.pptx/.odt/.tsv; JSON/YAML are validated; PDFs take the same `pages=` / `password=`)
- When citing, distinguish original text from OCR output; HTML tables come out as the same markdown table across all three entries (doc_parse / web_read / web_crawl)

## When search fails (pivot in this order)

1. Re-search naming other engines (`dhole engines probe` first to see who's alive; the web pool has no vertical index — use `sources=` for the vertical pools, and name the international engines for international content)
2. Know the domain? Skip search: `web_discover(url=<domain>)` to map the site directly
3. Site has a feed? `feed_read`
4. Everything down (blocked, no proxy): tell the user about `DHOLE_SEARCH_PROXY` or a keyed backend (references/configuration.md) — this is a "config change", so present options and wait for the user's decision

## Sites behind login

Four paths, cheapest first: (1) stored credentials — `cred_defaults` `set={cookies: ..., host: ...}` or `{auth: ..., host: ...}` (`session_id` keeps a browser jar across calls, 24h). (2) **No-password browser flow**: pass the same named `session_id` to browser-tier calls — the jar bridges into that session's dedicated browser and back; verify with `check_login`, act with snapshot + index actions, and let `{"submit": ...}` pause for the user's confirmation. (3) CDP attach (user's real Chrome, one-time setup — configuration.md). (4) DataDome / Akamai / interactive Turnstile / 2FA remain hand-offs. Cookies and tokens don't survive cross-origin redirects; values are never echoed. **Read the logged-in page back with `cache_ttl=0`**: the content cache is keyed by URL, not by who was logged in, so inside the TTL a post-login read can be answered by the anonymous copy — `cached=true` is the only trace that the site was never asked, and the response now says so in `summary`. **Never ask the user to paste a password into the conversation** — route it through path 2 or 3 instead.

## Operate a page (form, settings, checkout)

1. `web_act` with `actions: [{"snapshot": true}]` — read the numbered element list (the chain is the browser tier: no `force_fetcher` to say)
2. Address by index with `expect`: `{"fill": {"index": 3, "text": ...}}`, `{"click": {"index": 12, "expect": "Save"}}`; an `expect` mismatch = the page changed → re-snapshot, never retry a stale index
3. Submission: `{"submit": {...}}` without `confirm` pauses (`pending_approval`, nothing sent) — describe what will be submitted to the user; only on their explicit yes re-run the **whole chain** with `confirm: true`. Every submit (paused or confirmed) is audit-logged. Aim it at the control that submits (a `button` / `input[type=submit]`), not at the `<form>` — a target no click submits answers `not_submitted: true` and nothing is sent
4. Captchas / 2FA / sliders: ≤3 tries on image captchas, then hand off — see SKILL.md constraint 12

## Overhead compression (long sessions / bulk jobs)

- Known URLs in bulk: one parallel `web_read(urls=[...])`
- Lead every long page with `focus=`; only paginate with `offset` when focus isn't enough
- Lightweight search+fetch sessions: suggest `DHOLE_TOOLS=web_read,web_search` to halve connection overhead
- Repeated fetches ride the cache (1h default / article 6h); `cache_ttl=0` only for fresh data
