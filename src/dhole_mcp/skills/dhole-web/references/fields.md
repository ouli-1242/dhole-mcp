# Response Field Dictionary

Consult this when a response doesn't make sense or a field shows a non-default value. **Responses are compressed: absence means default** — a missing field is "nothing unusual", not lost data (legacy parsers that need the full shape can opt into `DHOLE_WIRE_FULL=1`).

## Verdict fields (read these three first)

| Field | Meaning | What to do when non-default |
|---|---|---|
| `status` | HTTP status; `0` = the request never went out (argument validation / SSRF / robots) | On `0`, read `error`; do not retry the same arguments |
| `content_ok` | `true` = 2xx + no error + non-empty body. **Does NOT mean the content is real** | Pair with `total_extracted_chars`: a few-hundred-char "success" is usually a shell page (troubleshooting "shell pages") |
| `error` | Failure reason, in plain language | Fix what it says; symptom-indexed fixes in troubleshooting.md |
| `not_modified` | 304: content unchanged | **Success** — the copy you hold is current; do not re-fetch |
| `summary` | One-line plain-language conclusion | Quick scan to confirm what the call actually did |

## Content shape

| Field | Meaning | What to do when non-default |
|---|---|---|
| `content` | Markdown body (array); PDFs carry page markers; schema extraction returns JSON | — |
| `total_extracted_chars` | Total extracted characters | Feeds shell-page judgment and budget decisions |
| `is_truncated` + `next_offset` | This call's budget cut the body | Continue with `offset=next_offset` (repeat the same focus) |
| `page_type` | `article` / `list` / `json` / `pdf` / … | `list` = structured link listing, not an extraction bug — follow it to content pages |
| `source_type` | `news` / `reference` (encyclopedia, standards) / `paper` (journals, preprints) / `blog` / `repo` / … | Informs freshness weighting and trust |
| `quality_score` | Extraction quality (PDF only) | Cite OCR results cautiously when low |
| `table_of_contents` | PDF outline (titles + page ranges) | Pick `pages` from it |
| `content_age_days` + `is_stale` | Content age / staleness flag (news 30d, docs/reference/paper 730d, rest 365d) | On `is_stale=true`, label citations with dates; `null` = continuously-edited site with no mtime |
| `extracted_type` | `structured` when schema extraction was applied | — |

## Provenance (citations and trust)

| Field | Meaning | What to do when non-default |
|---|---|---|
| `url` / `original_url` | Final URL / the URL you passed | Different = a redirect or meta-refresh hop happened |
| `fetcher_used` | Tier actually used: `http` / `stealthy` / `cache` / `parse` / `none` | `none` = no request was sent (validation / SSRF / robots) |
| `escalation_path` | The escalation chain walked (e.g. `direct:http` → stealthy → archive) | Reached stealthy/archive = strong anti-bot; send later same-site calls with `force_fetcher='stealthy'` |
| `source` + `archived_at` | archive.org snapshot fallback + snapshot date | **Label citations** "archived snapshot from X"; flag time-sensitive content |
| `discovered_from` | (feed) homepage the feed was auto-discovered from | — |
| `duration_ms` / `fetched_at` | Elapsed time / fetch timestamp | — |

## Search-specific (smart_search)

| Field | Meaning | What to do when non-default |
|---|---|---|
| `results[].relevance_score` | Normalized relevance (top is always 1.0) | — |
| `results[].fetch_relevance` | `high` / `med` / `low` — fetch-worthiness | Fetch high first; a lower tier can still be the right one (ranking is a hint, not a directive) |
| `results[].engines_consensus` | How many independent index FAMILIES agree (`x of N`) | `1 of N` = no cross-validation — verify via `smart_fetch` before citing |
| `engines_used` | Engines that actually answered | Only 1–2 names = degraded pool |
| `engine_empty` / `engine_preempted` | Answered with zero results / didn't finish before the deadline | A long list = rate-limiting/blocking/cooling — follow the signal table |
| `rerank_mode` | `neural` = cross-encoder ranked / `merge` = consensus ordering | `merge` = model still loading (first search of a process); one retry restores `neural` |
| `date_filter` | `.requested` / `.sent` / `.exact` / `.engines_filtered` / `.engines_unfiltered` / `.note` | **`.note` is the authoritative plain-language account**; state time ranges the way it does (preset substituted, some engines can't be date-filtered at all) |
| `fetch_hint` | This round's raw-score span and filtering; reports error when the whole round was cleared | When `min_raw_relevance` filtered, it shows the span the raw scores covered |
| `related_queries` | Related-search suggestions | Useful for a rephrase |
| `total_results` | Number of results returned | There is **no `total_estimate`** field (result-count estimates) — don't look for one, don't invent one |

## Sessions, cache, and feed increments

| Field | Meaning | What to do when non-default |
|---|---|---|
| `session_id` + `session_cookie_names` | Session active + cookie NAMES stored for that host (values are never echoed) | Cookies are live credentials — `close_session(session_id=...)` when done |
| `cache_validators` | `.etag` / `.last_modified` — pass back verbatim as `if_none_match` / `if_modified_since` | Reuse on the next conditional request; 304 = unchanged |
| `items_older_than_since` | (feed) entries filtered out by `since` | Non-zero means incremental filtering is working |
| `summary_truncated` | (feed) item summary is a 500-char preview | Fetch the item URL with `smart_fetch` for full text |

## Links and metadata

| Field | Meaning | What to do when non-default |
|---|---|---|
| `links.citations` / `navigation` / `external` + `total_found` + `is_truncated` | Categorized links + pre-truncation true count + whether the default caps (30/20/20) hid any | `is_truncated=true` = more links exist; `max_links` raises the cap up to 100 |
| `metadata.robots` | This request's robots verdict | `bypassed (DHOLE_IGNORE_ROBOTS=1…)` = compliance off process-wide; also the ground truth behind `robots_disallowed` |
| `metadata.title` / `lang` / `published_time` / `author` / `canonical` | Page metadata | Prefer `canonical` and `published_time` when citing |
| `metadata.actions` | One receipt per page-interaction action | If an action didn't take effect, look here (e.g. wait_selector timed out) |
| `metadata.encoding` | (parse) encoding actually used | On mojibake, compare — if detection guessed wrong, pass `encoding=` explicitly |
