# Response Field Dictionary

Consult this when a response doesn't make sense or a field shows a non-default value. **Responses are compressed: absence means default** — a missing field is "nothing unusual", not lost data. There is no switch that makes dhole send every field: the shape is the shape.

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
| `escalation_path` | The escalation chain walked (e.g. `direct:http` → stealthy → archive); a CDP-attach call reads `cdp-attach:stealthy` | Reached stealthy/archive = strong anti-bot; send later same-site calls with `force_fetcher='stealthy'` |
| `cdp_attached` | The fetch ran inside the user's real Chrome via CDP attach (omitted = normal tiers) | Attach is user-granted and audit-logged; read `login_check` before citing the content |
| `login_check` | `{logged_in: true/false/null, signals, cookie_names, method:"dom+cookie-names"}` — DOM shape + cookie NAMES, no model, values never leave the browser | `false` = session expired → ask the user to re-login in the attached Chrome; `null` = page shape undecidable → say so, don't treat it as success; `cookie_names` are identifiers, not secrets |
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
| `metadata.actions` | One receipt per page-interaction action: `ok`, `error`, and — for rich actions — `image: true` (pixels rode the content list as image blocks), `qr: [...]` (decoded QR texts), `elements` / `elements_total` / `elements_truncated` (snapshot), `submitted: true` | If an action didn't take effect, look here (e.g. wait_selector timed out). A receipt with `pending_approval: true` means a submit PAUSED before acting — nothing was sent; ask the user, then re-run the whole chain with `confirm: true` on that submit. A receipt with `error` naming "element index" beyond the page means the page changed — re-snapshot |
| `metadata.encoding` | (parse) encoding actually used | On mojibake, compare — if detection guessed wrong, pass `encoding=` explicitly |

## The stateful tools (recipe / watch / vault / login)

Registered by default in 17.0; absent from `tools/list` only when the operator named an explicit `DHOLE_TOOLS` subset. Absence-means-default applies here too.

| Field | Meaning | What to do when non-default |
|---|---|---|
| `changed` (watch) | `true` / `false` vs the stored baseline; **`null` when this check only created it** | `null` + `baseline_created: true` = report "baseline stored", never "no change". Deliberately never omitted from the wire |
| `added` / `removed` (watch) | Lines added / removed vs the baseline | Counted over the whole change even when `diff_truncated` is set |
| `baseline_cut` (watch) | The stored snapshot hit `max_chars` | A later diff can miss content past the cut — widen `max_chars` and re-baseline |
| `diff_truncated` (watch) | The `diff` text was cut at the line cap | `added` / `removed` still count the whole change |
| `outcomes` / `actions_failed` (recipe) | One receipt per replayed action / how many failed | `actions_failed` non-zero = the page changed; re-derive the steps and re-`save` |
| `recipes[].use_count` / `last_ok` (recipe) | How often it ran / whether it last succeeded | A recipe that never succeeds is stale — re-record it |
| `sources` (vault) | One row per manager: `available` / `unlocked` / `detail` / `hint` | `unlocked: null` = the CLI is not installed ("not asked" ≠ "locked"); a missing manager is a note, not an error |
| `items[]` (vault) | Credential **metadata**: `id` / `name` / `username` / `has_password` / `has_totp` / `uris` / `source` | **Never a password or TOTP value** — there is no field for one. `has_password: true` is what picking a credential needs |
| `notes` (vault) | Per-manager trouble with its fix (not installed / locked / not signed in) | Relay the note verbatim; a locked vault is the user's to unlock |
| `pending_approval` + `plan` + `fields` (login) | The login ran without `confirm` — **nothing filled, nothing submitted** | Show the plan to the user; re-call the same arguments with `confirm=true` only on approval |
| `logged_in` (login) | Post-login verdict, same model-free DOM + cookie-NAME probe as `check_login` — **tri-state** | `false` = the session did not take → check the credential; `null` = the probe could not answer, which is **not** "not logged in" |
| `filled` / `submitted` (login) | How many fields were filled / whether the submit ran | Both `0` / `false` before approval — that is the gate working, not a failure |
| `credential` / `credential_source` (login) | The vault item **by name** and which manager answered | The password itself never appears; this is the receipt |
| `cookie_names` (login) | Cookie NAMES the site set | Identifiers, not secrets — values are never returned |
