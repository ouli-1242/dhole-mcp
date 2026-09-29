# Task Recipes

Starting routes for multi-step tasks. Each recipe marks where to pivot on failure — an empty search or a shell page is a route change, not a reason to hammer retries.

## Research a topic and answer

1. `smart_search` (default pool; name verticals: WeChat `sogou_weixin`, encyclopedias `wikipedia`/`baidu_baike`, international add `bing_global`/`brave`/`duckduckgo`)
2. Pick 2–3 `fetch_relevance: high` hits (ranking is a hint, not a directive — judge by content), `smart_fetch` with `focus=` for full text
3. Cross-check, then answer with URLs cited; content that only appeared in snippets and was never fetched does not count as verified
- Pivot: thin/empty results → troubleshooting "Search"; one shell page → `force_fetcher='stealthy'`

## Track a site's updates (incremental polling)

1. `feed_fetch(url=<homepage or feed URL>)` — passing the homepage works, it finds the feed itself (`discovered_from`); unparseable feeds fail with a plain-language error, don't re-parse XML yourself
2. Record the newest item's `published`
3. Every later round: `since=<that timestamp>` for increments only (the filtered count is reported)
- Best for: news sites, blogs, changelogs, release pages. Sites without feeds fall back to `smart_search` + `freshness`/`after` (a four-preset window, not precise)

## Collect a documentation site

1. `smart_crawl(url=<docs root>, options={sitemap:true}, discover_only=true)` — whole-site URL map in one request (if no sitemap: `discover_only=true`, but map expansion costs one request per page)
2. `path_include:"/docs"` to scope a subtree (`'/docs'` does not match `/docs-old`), `search:"keywords"` to filter by title/URL
3. `crawl_urls=[...]` to fetch the picks (named pages always fetch in full; dropped entries are reported); for known URLs you can also go straight to `smart_fetch(urls=[...])`
4. List pages return structured link listings (`page_type=list`) — follow them to content pages
- Budget sense: `max_total_chars` caps the whole run at 1,000,000; map first, then name pages — don't max `max_pages` blindly

## Structured extraction (table/listing page → JSON)

1. `smart_fetch` + `schema`: repeated records need a "container selector + sub-schema" — sub-selectors evaluate inside the container (syntax in references/tools.md)
2. `css_selector`/schema only work on HTML; XML/JSON APIs come back as-is
3. For content behind scroll/click, run `actions` first (scroll waits for the page to stop growing; isTrusted-gated lazy-load needs click) and pair with schema

## Verify a local dev / staging service

1. `smart_fetch(url="http://127.0.0.1:PORT/...", options={allow_private:true})` — the SSRF guard rejects private hosts by default; name the waiver; for recurring use set `DHOLE_ALLOW_PRIVATE_HOSTS`. With a proxy running, loopback may be intercepted (dead ports answer 502 instead of connection-refused) — rule the proxy out first (`NO_PROXY` += `127.0.0.1,localhost`); never probe dead ports with stealthy (the browser waits out the 30s timeout)
2. Rendered view / real behavior: `screenshot` (text-only agents: `options.save_to` then read the file); interaction checks via `actions` click/fill
3. `resolve_url` settles where server-side redirects actually land

## Reading PDFs

- Online: `smart_fetch` (`pages="1-5"` trims extraction only — the file downloads whole; raise `timeout` for big files; scanned PDFs auto-OCR; pick pages from `table_of_contents`)
- Local: `parse` (also .docx/.xlsx/.pptx/.odt; JSON/YAML are validated)
- When citing, distinguish original text from OCR output; HTML tables come out as the same markdown table across all three entries (parse / smart_fetch / smart_crawl)

## When search fails (pivot in this order)

1. Re-search naming other engines (`dhole engines probe` first to see who's alive; use verticals for vertical content)
2. Know the domain? Skip search: `smart_crawl(url=<domain>, discover_only=true)` to map the site directly
3. Site has a feed? `feed_fetch`
4. Everything down (blocked, no proxy): tell the user about `DHOLE_SEARCH_PROXY` or a keyed backend (references/configuration.md) — this is a "config change", so present options and wait for the user's decision

## Sites behind login

Out of scope; two paths: user-supplied cookies (`options.cookies` + `options.session_id` keeps the jar, 24h) or an API token (`options.auth`: basic/bearer/header). Cookies live in the HTTP tier only and do not share with the browser tier; credentials don't survive cross-origin redirects; values are never echoed. Ask the user for credentials first — never guess login endpoints yourself.

## Overhead compression (long sessions / bulk jobs)

- Known URLs in bulk: one parallel `smart_fetch(urls=[...])`
- Lead every long page with `focus=`; only paginate with `offset` when focus isn't enough
- Lightweight search+fetch sessions: suggest `DHOLE_TOOLS=smart_fetch,smart_search` to halve connection overhead
- Repeated fetches ride the cache (1h / docs 24h / article 6h); `cache_ttl=0` only for fresh data
