# Task Recipes

Starting routes for multi-step tasks. Each recipe marks where to pivot on failure — an empty search or a shell page is a route change, not a reason to hammer retries.

## Research a topic and answer

1. `smart_search` (default pool = every keyless engine; to lean international, name `bing_global`/`brave`/`duckduckgo`)
2. Pick 2–3 `fetch_relevance: high` hits (ranking is a hint, not a directive — judge by content), `smart_fetch` with `focus=` for full text
3. Cross-check, then answer with URLs cited; content that only appeared in snippets and was never fetched does not count as verified
- Pivot: thin/empty results → troubleshooting "Search"; one shell page → `force_fetcher='stealthy'`

## Track a site's updates (incremental polling)

1. `feed_fetch(url=<homepage or feed URL>)` — passing the homepage works, it finds the feed itself (`discovered_from`); unparseable feeds fail with a plain-language error, don't re-parse XML yourself
2. Record the newest item's `published`
3. Every later round: `since=<that timestamp>` for increments only (the filtered count is reported)
- Best for: news sites, blogs, changelogs, release pages. Sites without feeds fall back to `smart_search` + `freshness`/`after` (a four-preset window, not precise)

## Collect a documentation site

1. `smart_crawl(url=<docs root>, sitemap=true, discover_only=true)` — whole-site URL map in one request (if no sitemap: `discover_only=true`, but map expansion costs one request per page)
2. `path_include:"/docs"` to scope a subtree (`'/docs'` does not match `/docs-old`), `search:"keywords"` to filter by title/URL
3. `crawl_urls=[...]` to fetch the picks (named pages always fetch in full; dropped entries are reported); for known URLs you can also go straight to `smart_fetch(urls=[...])`
4. List pages return structured link listings (`page_type=list`) — follow them to content pages
- Budget sense: `max_total_chars` caps the whole run at 1,000,000; map first, then name pages — don't max `max_pages` blindly

## Structured extraction (table/listing page → JSON)

1. `smart_fetch` + `schema`: repeated records need a "container selector + sub-schema" — sub-selectors evaluate inside the container (syntax in references/tools.md)
2. `css_selector`/schema only work on HTML; XML/JSON APIs come back as-is
3. For content behind scroll/click, run `actions` first (scroll waits for the page to stop growing; isTrusted-gated lazy-load needs click) and pair with schema

## Verify a local dev / staging service

1. `smart_fetch(url="http://127.0.0.1:PORT/...", allow_private=true)` — the SSRF guard rejects private hosts by default; name the waiver; for recurring use set `DHOLE_ALLOW_PRIVATE_HOSTS`. With a proxy running, loopback may be intercepted (dead ports answer 502 instead of connection-refused) — rule the proxy out first (`NO_PROXY` += `127.0.0.1,localhost`); never probe dead ports with stealthy (the browser waits out the 30s timeout)
2. Rendered view / real behavior: `screenshot` (text-only agents: `save_to` then read the file); interaction checks via `actions` click/fill
3. `resolve_url` settles where server-side redirects actually land

## Reading PDFs

- Online: `smart_fetch` (`pages="1-5"` trims extraction only — the file downloads whole; raise `timeout` for big files; scanned PDFs auto-OCR; pick pages from `table_of_contents`)
- Local: `parse` (also .docx/.xlsx/.pptx/.odt; JSON/YAML are validated)
- When citing, distinguish original text from OCR output; HTML tables come out as the same markdown table across all three entries (parse / smart_fetch / smart_crawl)

## When search fails (pivot in this order)

1. Re-search naming other engines (`dhole engines probe` first to see who's alive; 17.0 has no vertical index — name the international engines for international content)
2. Know the domain? Skip search: `smart_crawl(url=<domain>, discover_only=true)` to map the site directly
3. Site has a feed? `feed_fetch`
4. Everything down (blocked, no proxy): tell the user about `DHOLE_SEARCH_PROXY` or a keyed backend (references/configuration.md) — this is a "config change", so present options and wait for the user's decision

## Sites behind login

Five paths, cheapest first: (1) user-supplied cookies (`cookies` + `session_id` keeps the jar, 24h) or an API token (`auth`: basic/bearer/header). (2) **No-password browser flow (17.0)**: pass the same named `session_id` to browser-tier calls — the jar bridges into that session's dedicated browser and back; verify with `check_login`, act with snapshot + index actions, and let `{"submit": ...}` pause for the user's confirmation. (3) **Stored-credential login (17.0, opt-in)**: `vault(op='match', url=...)` to find the item, then `login` without `confirm` to show the plan and again with `confirm=true` to run it — for a password the user already keeps in Bitwarden / 1Password; the value is read server-side and never enters the transcript. (4) CDP attach (user's real Chrome, one-time setup — configuration.md). (5) DataDome / Akamai / interactive Turnstile / 2FA remain hand-offs. Cookies and tokens don't survive cross-origin redirects; values are never echoed. **Never ask the user to paste a password into the conversation** — route it through path 2, 3, or 4 instead.

## Reach a page that needs steps — record and replay (recipe, opt-in)

1. Derive the chain once: `smart_fetch` with `actions: [{"snapshot": true}]`, then address by index with `expect` (see "Operate a page")
2. `recipe(op='save', name=..., url=..., actions=[...], note=...)` — validated at save time, so a chain the executor would refuse cannot be stored
3. Later: `recipe(op='run', name=..., session_id=...)` returns the whole fetch plus one receipt per action. `session_id` is per-run — recipes never store one
4. Pivot: `actions_failed` non-zero (or `op='list'` showing `last_ok: false`) means the page changed → re-snapshot, re-derive, re-save. Don't keep replaying a broken chain
- Best for: pages whose content only appears after clicks, or a repeated multi-step fetch (always registered in 17.0)

## Watch a page for changes (watch)

1. `watch(op='add', name=..., url=..., focus=...)` — add `focus` for a long page whose chrome changes every load
2. First `watch(op='check', name=...)` **only stores the baseline**: `changed=null`, `baseline_created=true`. Report "baseline stored", NOT "no change"
3. Later checks answer `changed: true/false` with `added`/`removed` counts and a capped `diff`
4. Pivot: a failed fetch leaves the baseline untouched and puts the reason in `error` — fix and re-check rather than assuming the page is gone. `baseline_cut=true` means `max_chars` was too small to see the whole page — widen it and re-baseline
- Best for: changelogs, pricing pages, status pages, docs — a page without a feed

## Operate a page (form, settings, checkout)

1. `smart_fetch` with `actions: [{"snapshot": true}]` (+ `force_fetcher='stealthy'`) — read the numbered element list
2. Address by index with `expect`: `{"fill": {"index": 3, "text": ...}}`, `{"click": {"index": 12, "expect": "Save"}}`; an `expect` mismatch = the page changed → re-snapshot, never retry a stale index
3. Submission: `{"submit": {...}}` without `confirm` pauses (`pending_approval`, nothing sent) — describe what will be submitted to the user; only on their explicit yes re-run the **whole chain** with `confirm: true`. Every submit (paused or confirmed) is audit-logged
4. Captchas / 2FA / sliders: ≤3 tries on image captchas, then hand off — see SKILL.md constraint 12

## Overhead compression (long sessions / bulk jobs)

- Known URLs in bulk: one parallel `smart_fetch(urls=[...])`
- Lead every long page with `focus=`; only paginate with `offset` when focus isn't enough
- Lightweight search+fetch sessions: suggest `DHOLE_TOOLS=smart_fetch,smart_search` to halve connection overhead
- Repeated fetches ride the cache (1h / docs 24h / article 6h); `cache_ttl=0` only for fresh data
