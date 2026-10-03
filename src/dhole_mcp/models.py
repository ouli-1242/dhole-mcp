"""Dhole 的响应模型：17 颗工具往线上发的每一行的形状。

从 server.py 原样搬出（17.0 B6 的第二刀，零行为变更）。搬的理由是这一块的内容
本来就与抓取无关：字段描述里写着"读 `next_action`""与 `content_ok` 一起看"，
那是给调用方读的说明书。搬完之后想要一个 `ResponseModel` 不必再把整个服务器
拖进来（`crawl.py` 至今还在函数体里惰性 `from dhole_mcp.server import ResponseModel`）。

`SessionType` 跟着搬：它是 `SessionInfo.session_type` 这颗字段的类型，
而别名本身只是 `Literal["dynamic", "stealthy"]`，没有别处需要它先存在。
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, PrivateAttr

SessionType = Literal["dynamic", "stealthy"]


class ResponseModel(BaseModel):
    """Request's response information structure."""
    status: int = Field(description="HTTP status (0=network error)")
    content: list[str] = Field(description="Extracted text (truncated if is_truncated)")
    url: str = Field(description="Final URL")
    original_url: str = Field(default="", description="The URL you passed, set ONLY when the fetch ended somewhere else (redirect or canonical rewrite). Empty = url is the URL you asked for, so comparing the two is how you detect a redirect.")
    cached: bool = Field(default=False, description="From cache")
    fetcher_used: str = Field(default="", description="Which tier answered: http / stealthy / cache / none")
    extracted_type: str = Field(default="markdown", description="markdown|html|text|article|structured")
    session_id: str = Field(default="", description="The id of the persistent session this call ran under (empty = stateless, the default). Pass session_id='name' to reuse cookies across calls: whatever the site sets is remembered for that host and sent back on the next call with the same id. A browser-session id is echoed here too. The jar covers the HTTP tier.")
    session_cookie_names: List[str] = Field(default_factory=list, description="Cookie NAMES the session jar now holds for this host (only when session_id is set). Values are never returned - a session cookie is a credential and tool output goes into your transcript. Use this to check a login actually stuck: empty after a sign-in call means nothing was stored.")
    duration_ms: float = Field(default=0, description="Duration ms")
    error: str = Field(default="", description="Error + recovery hints")
    content_type: str = Field(default="", description="e.g. text/html, application/json")
    total_size_bytes: int = Field(default=0, description="Raw body bytes")
    total_extracted_chars: int = Field(default=0, description="Total chars of extracted text (before chunking). Use to gauge how much remains: total_extracted_chars - offset")
    is_truncated: bool = Field(default=False, description="True=more extracted content. Use next_offset. Check total_extracted_chars to see how much remains.")
    next_offset: int = Field(default=0, description="Next offset when is_truncated. 0=no more")
    escalation_path: str = Field(default="", description="e.g. http→stealthy. Pre-v3.5 logs may contain http→dynamic→stealthy entries from the old 3-tier path.")
    retry_count: int = Field(default=0, description="Retries")
    # Agent-facing signals (set by _with_agent_hints on every finalized response).
    summary: str = Field(default="", description="One-line status for quick reasoning, e.g. '200 OK · 12.4KB markdown · http · truncated'")
    content_ok: bool = Field(default=False, description="True = real content retrieved (status<400, no error, not a JS shell/login wall). Check this before trusting content.")
    next_action: str = Field(default="", description="Suggested next call when one is obvious (paginate/retry/switch source). Empty = nothing to do.")
    fetched_at: str = Field(default="", description="ISO-8601 UTC timestamp this response was generated. For cached responses, content age is bounded by cache_ttl.")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Page metadata for citation/relevance: title, description, site_name, type, image, canonical, lang, published_time, author (OpenGraph + JSON-LD + canonical). For PDFs: title, author, subject, keywords, creator, producer, creation_date, mod_date. Empty for non-HTML/non-PDF.")
    media: List[str] = Field(default_factory=list, description="Image URLs on the page (only populated when include_media=true). Multimodal agents can fetch/screenshot these. For PDFs: per-page embedded-image metadata (count + dimensions).")
    links: Dict[str, Any] = Field(default_factory=dict, description="Outgoing links classified by context (only populated when include_links=true): {citations:[{url,text}], navigation:[{url,text}], external:[{url,text}], primary_source:url, total_found:int, is_truncated:bool}. citations = links inside the main-content area (the page's referenced sources - the highest-value links to follow); navigation = site chrome; external = off-domain links; primary_source = best-effort hint at the actual primary source (canonical/JSON-LD or a citation on arxiv/doi/github/etc); total_found counts every distinct link on the page BEFORE the per-list cap (30/20/20 defaults, or max_links) and is_truncated=true says the cap dropped some - raise max_links (1-100) when you need more. Use to follow a page's source chain in one step.")
    quality_score: Optional[float] = Field(default=None, description="PDF extraction quality 0.0-1.0 (readable-char ratio; 1.0 = clean, low = garbled/CID corruption) - null for anything that is not a PDF, because a 0.0 there reads as 'the extraction is maximally garbled' when nothing was ever scored. Trust PDF content more the closer this is to 1.0.")
    table_of_contents: list = Field(default_factory=list, description="PDF section-map: outline/bookmarks as [{level, title, page, end_page}] when the PDF has a ToC; for PDFs without bookmarks, a heading-based map is built from font-size detection. page+end_page give a range per section so you can pass pages='X-Y' to grab one section. Empty for non-PDF or PDFs with no detectable headings.")
    # ─── v10 research-grade envelope (additive; all default-valued) ───
    # page_type: structural class of the page, computed from raw HTML. Drives
    # next_action (list pages point to their links, auth walls suggest switching source).
    page_type: str = Field(default="unknown", description="Structural class: article|list|pdf|xml|js_shell|auth_wall|paywall|captcha|redirect|image|json|unknown. Drives next_action. What kind of SITE this is lives in source_type, never here. 'list' = a page whose main content is links to other pages (fetch those or web_crawl). 'xml' = a data XML document (sitemap, RSS/Atom, XML API) returned with its tags intact. 'auth_wall'/'paywall'/'captcha' = the body is a login/payment/anti-bot challenge rather than the page's content.")
    # source_type + is_official: domain-based authority signal so the agent can
    # weigh trust without a separate lookup. Conservative: is_official is True
    # only on a strong signal (the name itself cannot be bought by a third
    source_type: str = Field(default="unknown", description="Domain class from the URL: gov|edu|github|docs-site|reference|paper|news|blog|forum|qa|ecommerce|unknown. A hint, not a verdict - docs-site only means the host starts with docs./developer., and reference/paper only mean the domain is a known encyclopedia/standards/journal host (which is not the same as being right).")
    is_official: bool = Field(default=False, description="True ONLY where the name itself cannot be bought: registry-controlled namespaces (gov, edu, github) plus the bodies that RUN a namespace (IANA/ICANN/RFC Editor/IETF/W3C/Unicode, and the package registries pypi/npm/crates/rubygems/go/metacpan/packagist/nuget). Docs/developer subdomains and community-edited reference sites (Wikipedia) are NOT official - who controls a name is not the same as being right. Conservative default False; it is a hint, not a substitute for checking the source.")
    # Freshness: content_age_days from the page's own published/modified date
    # (OpenGraph/JSON-LD/PDF). None = no date recoverable (or a continuously
    # edited wiki whose only date is its creation date - see envelope.py).
    # is_stale compares the age against the horizon for THIS KIND of source.
    content_age_days: Optional[int] = Field(default=None, description="Age in days from the page's published/modified date (OpenGraph/JSON-LD/PDF creation_date). null = the page carries no recoverable date (NOT a negative age), or it is a continuously-edited wiki whose only date is its creation date, which says nothing about currency. Pair with is_stale to judge currency.")
    is_stale: bool = Field(default=False, description="True when content_age_days is past the staleness horizon for this kind of source: 30 days for news, 730 for reference/paper/docs/repo/QA, 365 for everything else. So the same age can be stale on a news page and current on a reference page. False when there is no age to judge.")
    # 17.0 §6：条件请求不再是调用方能传的参数，`cache_validators` / `not_modified`
    # 两个响应字段随之从 wire 上下来。它们存在的唯一理由是"你自己存好再传回来"那个
    # 合同，而合同删了。版本标记本身还要有个进程内的落点 —— 用它的是 `watch`
    # （见 _REVALIDATE），PrivateAttr 因此天然不上线。
    # "没变"这个事实也不再需要一个布尔：`status == 304` 就是它，而 304 早就在响应里。
    _validators: Dict[str, str] = PrivateAttr(default_factory=dict)
    # source + archived_at: set ONLY when this content came from the Internet
    # Archive (auto-fallback after a live hard-block). 'live' (default) = the
    # real page. Honest marking so the agent knows it may be a dated snapshot.
    source: str = Field(default="live", description="'live' (default) = fetched from the real URL. 'archive.org' = the live site hard-blocked and this content was recovered from the Internet Archive's closest snapshot (see archived_at for the snapshot date).")
    archived_at: str = Field(default="", description="ISO date of the archive.org snapshot when source='archive.org'. Empty when source='live'. The content reflects the page as it was on this date.")
    # ─── G33/G35: CDP attach 可见性 + 无模型登录判定 ───
    cdp_attached: bool = Field(default=False, description="True when this response was fetched through an attached real browser (cdp_url): every request rode the attached profile's authenticated cookies. Default false - the first attach to a host is refused with cdp_authorization_required until the user approves that host, and every attach attempt is audited. Never treat content from an attached profile like public content: it is the user's logged-in view.")
    # 17.0 §3-17: the stored default identity that was in force for THIS call.
    # A caller who never sees which calls ran on defaults cannot revoke them.
    identity_applied: str = Field(default="", description="Key names (and host scopes) from `cred_defaults` that this call was fetched with — names only, never values. Empty means nothing stored was in force and every credential on this call came from what you passed. Read it before concluding a page is public: content fetched with your stored credentials is your logged view of that host.")
    login_check: Optional[Dict[str, Any]] = Field(default=None, description="Only when check_login=true AND the fetch ran on the browser tier: {logged_in: true|false|null, signals: [...], cookie_names: [...], method: 'dom+cookie-names'}. Model-free: DOM shape (logout control / password field / login form) plus cookie NAMES - values are never returned. null when not requested, when the fetch never reached the browser tier, or on cache hits (the probe is live-only).")

    # Element captures from {\"screenshot\"} actions (multimodal flag): pixels
    # ride the CONTENT list as ImageContent, never this model — a base64 blob
    # in the JSON payload would bloat every transcript/cache copy of the
    # response for clients that cannot render it anyway. PrivateAttr keeps it
    # out of model_dump()/serialization by construction.
    _action_images: List[Dict[str, str]] = PrivateAttr(default_factory=list)


class BulkResponseModel(BaseModel):
    """Response from bulk fetch operations, one result per URL."""
    results: list[ResponseModel] = Field(description="Per-URL results")
    total: int = Field(description="Total URLs")
    successful: int = Field(description="Fetches with status<400 + no error")


class SessionInfo(BaseModel):
    """Information about an open browser session."""
    session_id: str = Field(description="Session ID")
    session_type: SessionType = Field(description="dynamic|stealthy")
    created_at: str = Field(description="ISO timestamp")
    is_alive: bool = Field(description="Session alive?")


class SessionCreatedModel(SessionInfo):
    """Response returned when a new session is created."""
    message: str = Field(description="Confirmation message")


class SessionCensusRow(BaseModel):
    """One live session in the `sys_state` census.

    Not a `SessionInfo` subclass: a session can exist purely as a cookie jar
    (a login done through the HTTP tier never opens a browser), so the browser
    half is what is optional here, not the id.
    """
    session_id: str = Field(description="The session_id= this call was given (empty = none)")
    hosts: Dict[str, List[str]] = Field(default_factory=dict, description="host -> the cookie NAMES the jar holds for it. Names only, always: a cookie value is a credential, and everything a tool returns lands in the transcript.")
    cookies: int = Field(default=0, description="Cookies held under this id. 0 with browser_open true means the browser keeps them in its own context, out of the jar.")
    expires_in_s: int = Field(default=0, description="Seconds until the EARLIEST of this session's cookies lapses by itself (0 = the jar holds nothing). Says whether forgetting it is urgent or already happening on its own.")
    browser_open: bool = Field(default=False, description="A browser is still running under this id, holding whatever that site set. Closing the jar does not stop it.")
    browser_type: str = Field(default="", description="dynamic|stealthy, empty when browser_open is false")
    browser_created: str = Field(default="", description="ISO timestamp of when the browser session opened")


class SysStateModel(BaseModel):
    """Response from `sys_state`: what local state exists, or what one call erased.

    The field names are the ones the two tools this one merged used to answer
    with (`purged`, `engine_state_reset`, `cookies_forgotten`, `prewarm_closed`,
    `open_count`...). That is deliberate: an agent that learned either tool from
    an old transcript, or from the guide, reads the same receipt for the same
    event instead of relearning a vocabulary that describes nothing new.
    """
    message: str = Field(default="", description="What this call did, in one line")
    cleared: List[str] = Field(default_factory=list, description="State classes this call actually erased, in the words you passed. Absent means nothing was erased - a paused or refused call says so here too.")
    pending_approval: bool = Field(default=False, description="true = this call erased NOTHING and is waiting for you to repeat it with confirm=true. Absent (false) is not approval: nothing happened yet. Reserved for erasures you would not notice as they happened - losing a site's cookies shows up only at your next visit to that site.")
    sessions: List[SessionCensusRow] = Field(default_factory=list, description="The census: every id holding a cookie jar or running a browser. Returned by a call with no `clear`, and listed in `error` when a named id does not exist.")
    open_count: int = Field(default=0, description="How many sessions are open right now (0 = nothing to forget)")
    session_id: str = Field(default="", description="The single session this call forgot, empty otherwise.")
    cookies_forgotten: int = Field(default=0, description="Cookies dropped by this call")
    browser_closed: bool = Field(default=False, description="True when this call shut a browser session down")
    closed: int = Field(default=0, description="Sessions closed by this call")
    prewarm_closed: bool = Field(default=False, description="Erasing every session also gave up the shared pre-warmed browser, so the next stealthy fetch pays a 3-5s cold start again. Named because it is a cost the caller did not ask for but should know about.")
    purged: int = Field(default=0, description="Cache rows erased by this call")
    cache: Dict[str, Any] = Field(default_factory=dict, description="Content-cache state: entries, expired (what a clear would drop that was going to lapse anyway), oldest_age_s, cap. Filled by a call with no arguments, so you can see the state before deciding to erase it.")
    engine_state_reset: bool = Field(default=False, description="True when this call also forgot engine cooldowns + yield history (circuit_breaker.json, engine_stats.json).")
    engine_health: Dict[str, Any] = Field(default_factory=dict, description="Per-engine pool health as dhole currently sees it: last status, yield verdict, and cooldown_seconds_left while an engine is on cooldown. Carried ONLY when the call names engine_state; a call about the content cache says nothing about the search pool.")
    watch_count: int = Field(default=0, description="Watches stored in ~/.dhole/tasks.db. Their rows, with baseline age, are web_watch(op='list').")
    recipe_count: int = Field(default=0, description="Recipes stored in ~/.dhole/tasks.db. Their rows are web_recipe(op='list').")
    cred_defaults: List[Dict[str, Any]] = Field(default_factory=list, description="The stored default identity (cred_defaults), redacted: key, host scope, age, and a value half that can never contain a secret. It is a credential you chose, kept on this machine for every later call - `clear` it here or `unset` one key there.")
    identity_count: int = Field(default=0, description="How many identity defaults are in force right now (0 = every call carries its own).")
    error: str = Field(default="", description="Set when a token meant nothing (an unknown class, or a session id nobody has open), or when a store refused the erasure; it names what DOES exist or what failed, rather than saying 'not found' and leaving you to guess.")
    next_action: str = Field(default="", description="What to do next, empty when there is nothing to do")


class CredDefaultsModel(BaseModel):
    """Response from `cred_defaults`: the shareable half of the stored identity.

    No field here can hold a secret by shape. `rows` carries what
    ``identity.redact`` returned — for a credential that is a type plus a
    username, cookie NAMES, or a proxy host with a "has credentials" boolean.
    The full values live in ``~/.dhole/identity.db`` and leave the process only
    as request headers to the host they were stored for.
    """
    message: str = Field(default="", description="One line: what this call stored or forgot, and what is in force now.")
    rows: List[Dict[str, Any]] = Field(default_factory=list, description="The whole store, redacted: [{key, scope, stored_s_ago, value}] where scope is the host a credential was given for ('' for the five global keys) and stored_s_ago is how long ago it was set. Values are never included.")
    changed: List[str] = Field(default_factory=list, description="Keys this call stored or replaced.")
    forgotten: List[str] = Field(default_factory=list, description="Keys this call forgot (every host scope of them).")
    error: str = Field(default="", description="Why nothing was stored: a key that is not one of the seven, a credential given without a host, or a value the key cannot hold. A refused call changed nothing on disk.")
    next_action: str = Field(default="", description="The call that would work, empty when this one did.")


class RecipeModel(BaseModel):
    """Response from `web_recipe`: save / list / run / delete of a recorded action chain.

    `run` carries the whole fetch back in `result` (the same ResponseModel
    web_read returns, same wire compaction), because a replay whose answer you
    cannot read is not a replay. `outcomes` repeats the per-action receipts so a
    broken step is visible without digging into result.metadata.
    """
    op: str = Field(description="What this call did: save|list|run|delete.")
    message: str = Field(default="", description="One line: what was stored/removed/ran.")
    name: str = Field(default="", description="The recipe this call named.")
    url: str = Field(default="", description="The URL the recipe replays.")
    recipes: List[Dict[str, Any]] = Field(default_factory=list, description="Every stored recipe (op=list), newest-used first, with use_count / last_ok so a stale one is visible without running it.")
    recipe: Dict[str, Any] = Field(default_factory=dict, description="The stored recipe itself (op=save): url, action count, note, created_at.")
    result: Optional[ResponseModel] = Field(default=None, description="op=run: the fetch this recipe produced, in full.")
    outcomes: List[Dict[str, Any]] = Field(default_factory=list, description="op=run: one row per action, ok or why not - the same receipts web_read reports, surfaced here so a broken step is obvious.")
    actions_failed: int = Field(default=0, description="op=run: how many of the recipe's actions failed. Non-zero means the page changed under the recipe; re-record it with web_recipe(op='save', ...).")
    error: str = Field(default="", description="Set when the recipe could not be stored or replayed; it names what exists rather than saying 'not found'.")
    next_action: str = Field(default="", description="What to do next, empty when there is nothing to do")


class WatchModel(BaseModel):
    """Response from `web_watch`: add / list / check / remove of a monitored URL.

    The one field to read carefully is `changed`: it is **null on the first
    check**, because a missing baseline is not the same as "nothing changed" —
    reporting the first run as "no change" invents a fact.
    """
    op: str = Field(description="What this call did: add|list|check|remove.")
    message: str = Field(default="", description="One line: what was stored/removed/compared.")
    name: str = Field(default="", description="The watch this call named.")
    url: str = Field(default="", description="The URL this watch monitors.")
    watches: List[Dict[str, Any]] = Field(default_factory=list, description="Every stored watch (op=list) with its baseline age, check_count and change_count.")
    changed: Optional[bool] = Field(default=None, description="op=check: true/false when compared against a baseline, NULL when this check only created the baseline (a missing baseline is not 'no change').")
    baseline_created: bool = Field(default=False, description="op=check: true when this call stored the first snapshot. changed is null in that case, by design.")
    baseline_at: str = Field(default="", description="ISO-8601 UTC of the snapshot this check compared against (empty when none existed).")
    added: int = Field(default=0, description="op=check: lines present now and not before.")
    removed: int = Field(default=0, description="op=check: lines that were there and are gone.")
    diff: List[str] = Field(default_factory=list, description="op=check: unified-diff lines (+added/-removed, one line of context).")
    diff_truncated: bool = Field(default=False, description="op=check: true when the diff was cut at the line cap - `added`/`removed` still count the whole change.")
    baseline_cut: bool = Field(default=False, description="op=check: true when the stored snapshot was cut at the size cap, so a later diff may miss content past the cut.")
    result: Optional[ResponseModel] = Field(default=None, description="op=check: the fetch this check made. A failed fetch leaves the baseline untouched and puts the reason in `error`.")
    error: str = Field(default="", description="Set when the watch could not be stored, or the fetch failed; the baseline is left untouched on a failed fetch.")
    next_action: str = Field(default="", description="What to do next, empty when there is nothing to do")


class VaultModel(BaseModel):
    """Response from `cred_vault`: which credential managers are reachable, and the
    metadata of the items they hold.

    There is deliberately **no field here for a password or a TOTP secret**. The
    tool cannot return a credential value even if a caller asks for one: values
    are read only by `login`, server-side, and are never serialised. What this
    model can say is that a value exists (`has_password`), which is what picking
    a credential actually needs.
    """
    op: str = Field(description="What this call did: status|list|match.")
    message: str = Field(default="", description="One line: what was found.")
    host: str = Field(default="", description="op=match: the host the item URIs were matched against.")
    sources: List[Dict[str, Any]] = Field(default_factory=list, description="op=status: one row per manager (bitwarden / onepassword) - installed?, unlocked?, and what to do when it is not.")
    items: List[Dict[str, Any]] = Field(default_factory=list, description="Credential metadata: id, name, username, has_password, has_totp, uris, source. NEVER a password or TOTP value.")
    total: int = Field(default=0, description="How many items matched before the cap.")
    truncated: bool = Field(default=False, description="True when the item list was cut at the cap.")
    notes: List[str] = Field(default_factory=list, description="Per-manager trouble (not installed / vault locked / CLI not signed in), each with its fix. A manager that is simply absent is a note, not an error.")
    error: str = Field(default="", description="Set when the op itself could not run.")
    next_action: str = Field(default="", description="What to do next, empty when there is nothing to do")


class LoginModel(BaseModel):
    """Response from `cred_login`: the plan (before approval) or the outcome.

    Two fields carry the contract. `pending_approval=true` is the FIRST response
    a caller sees: with confirm=false nothing is filled and nothing is
    submitted, and `fields`/`plan` say what a confirmed run would do. `logged_in`
    is tri-state like web_read's login_check — null is "the probe could not
    answer", which is not the same as false.

    The credential's password never appears in any field; the receipt names the
    vault item instead.
    """
    op: str = Field(default="login", description="Always 'login'.")
    message: str = Field(default="", description="One line: what happened, or what is waiting.")
    url: str = Field(default="", description="The page this login targeted.")
    credential: str = Field(default="", description="The vault item used, by NAME. The username and password are never returned.")
    credential_source: str = Field(default="", description="Which manager answered: bitwarden|onepassword.")
    session_id: str = Field(default="", description="The session the resulting cookies were kept under.")
    pending_approval: bool = Field(default=False, description="true = nothing was filled or submitted; the detected plan is waiting for the user's yes. Re-call with confirm=true to run it.")
    plan: str = Field(default="", description="pending_approval: one line naming the detected fields and the submit step.")
    fields: Dict[str, Any] = Field(default_factory=dict, description="The detected form: username/password/submit element indices with their names, and how submit would happen (element or Enter).")
    filled: int = Field(default=0, description="How many fields were filled (0 before approval).")
    submitted: bool = Field(default=False, description="true when the submit step actually ran.")
    logged_in: Optional[bool] = Field(default=None, description="Post-login verdict from the same model-free DOM + cookie-NAME probe web_read's check_login uses. NULL means the probe could not answer (no browser tier, or the page never settled) - not 'not logged in'.")
    signals: List[str] = Field(default_factory=list, description="What the probe saw (logout control / password field / login form / session cookie names).")
    cookie_names: List[str] = Field(default_factory=list, description="Cookie NAMES the site set. Values are never returned.")
    result: Optional[ResponseModel] = Field(default=None, description="The post-submit fetch, in full.")
    error: str = Field(default="", description="Set when the login could not run: no manager, no such item, no password field, or a failed fill/submit.")
    next_action: str = Field(default="", description="What to do next, empty when there is nothing to do")

