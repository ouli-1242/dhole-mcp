"""Dhole 的线格式：每一次调用都要付的那份 token，以及"字段缺席 = 默认值"这份契约。

从 server.py 原样搬出（17.0 B6 的第三刀，零行为变更）。这一块与抓取无关，
它只管一个响应对象变成 JSON 文本的过程，所以政策表（`_WIRE_OMIT`）与实现
（`_wire_dump` / `_wire_prune`）必须在同一个文件里：政策改了而实现跟不上，
表现是"某个字段悄悄一直上线"，这件事在响应里看不出来。

只依赖 stdlib + pydantic + mcp.types，加上两处按需 import（投影行取自
`dhole_mcp.feed`，默认值哨兵取自 `pydantic_core`）；对 server 没有任何反向依赖。
"""

from __future__ import annotations

import json
from typing import Optional

from pydantic import BaseModel


# ─── Wire compaction (tokens paid on EVERY call, not once) ──────────────────
#
# Measured on this server: a 404 with zero content returned 927 chars, and 33%
# of a successful fetch's bytes were fields sitting at their own default
# (is_stale=false, page_type="unknown", links={}, retry_count=0, ...). A default
# value carries no information once its ABSENCE says the same thing, so the wire
# drops it. The per-call envelope tax goes from ~380 to ~40 chars.
#
# Only the WIRE copy is compacted. ResponseModel keeps every field, so the cache
# envelope, crawl's per-page aggregation and the actions tier still read a
# complete object - a bug here cannot corrupt what gets stored.
#
# The policy is per model rather than by field name, because the same name means
# different things in different rows: `source` is "live"/"archive.org" on a fetch
# and an engine name on a search result; `page_type` defaults to "unknown" on one
# and "" on a crawl page. And a 0.0 is not always a default - `relevance_score`
# 0.0 is the reranker's way of saying "off-topic", so it is in no omit set, while
# the `quality_score` None that means "nothing was ever scored" is.
#
# A field is omitted only when it equals its own declared default (or is an empty
# container for default_factory fields). Anything else, including every False
# that is a verdict rather than a default (content_ok), stays.
_WIRE_OMIT: dict = {
    "ResponseModel": frozenset({
        "original_url", "cached", "session_id", "session_cookie_names",
        "is_truncated", "next_offset", "retry_count", "escalation_path",
        "extracted_type", "page_type", "source_type", "is_official",
        "content_age_days", "is_stale", "source",
        "archived_at", "quality_score", "total_size_bytes",
        "total_extracted_chars", "media", "links", "table_of_contents",
        "metadata", "fetcher_used", "content_type",
        # G33/G35: 默认 False/None（无人 attach、未要求登录判定）时省略；
        # 非 default 的值（attach 过、probe 出结果）照常上线。
        "cdp_attached", "login_check",
        # §3-17：没用到落盘身份时这颗根本不该出现（绝大多数调用如此）。
        "identity_applied",
        # G25 阶段4：干净的正文不点名（None 缺席）；命中启发式才上线。
        "untrusted_content",
        # 一次干净成功不付诊断的钱（None 缺席）；有 code 才上线。
        "diagnosis",
        # 空正文的 0 不携带信息（缺席即"没有可计费的正文"）。
        "approx_tokens",
        # 没折叠任何样板时 0 不携带信息（缺席即"这页没有页内重复行"）。
        "duplicate_lines_collapsed",
    }),
    "CrawlPage": frozenset({
        "depth", "fetcher_used", "title", "page_type", "content_chars",
        "is_truncated", "next_offset", "fetched_at", "lastmod", "summary",
        "error", "untrusted_content",
    }),
    "CrawlResponseModel": frozenset({
        "discover_only", "truncated_by_max_pages",
        "truncated_by_time", "truncated_by_total_chars", "sitemap_truncated",
        "sitemaps", "robots_skipped", "error", "duration_ms", "ssrf_recheck",
    }),
    # 批量信封：`ssrf_recheck` 是"这台机器跳过了重查"的那一句，只在真跳过时上线
    # （它原先在每一行的 metadata 里重复一遍，见 BulkResponseModel 的序列化器）。
    "BulkResponseModel": frozenset({"ssrf_recheck"}),
    "SearchResult": frozenset({
        "snippet", "source", "position", "fetch_relevance",
        "engines_consensus", "source_type",
        # G4：official=False（绝大多数行）省略；True 才上线（+15 chars/行）。
        "official",
        # 批次8 刀5：行级注入标记只在命中时上线（None=干净行，缺席即默认）。
        "untrusted_content",
    }),
    "SearchResponseModel": frozenset({
        "engines_used", "engine_blocked", "engine_empty", "engine_preempted",
        "date_filter", "consensus_basis", "cached", "rerank_mode",
        "fetch_hint", "related_queries", "fetched_pages", "duration_ms",
        # 没设共识地板（或它没丢任何东西）时，空名单不携带信息——缺席即"闸门
        # 什么都没丢"。
        "consensus_skipped",
    }),
    "FeedResult": frozenset({
        "source_title", "error", "discovered_from", "since",
        "note",
    }),
    "FeedItem": frozenset({"title", "published", "summary", "summary_truncated",
                           "untrusted_content"}),
    # sys_state: every field of the census row is per-session detail that
    # sits at its default most of the time (no browser, no cookies, nothing
    # expiring). `session_id` is NOT omittable - a row without its id says
    # nothing about anything.
    "SessionCensusRow": frozenset({
        "hosts", "cookies", "expires_in_s", "browser_open",
        "browser_type", "browser_created",
    }),
    # sys_state（cache_clear + close_session 合成的一颗）：只 `message` 一定上线，
    # 其余全是"这次没发生"的收据。`pending_approval` 缺省即"没有等待批准的动作"，
    # 上线即"什么都没抹，去 confirm"。
    "SysStateModel": frozenset({
        "cleared", "pending_approval", "sessions", "open_count", "session_id",
        "cookies_forgotten", "browser_closed", "closed", "prewarm_closed",
        "purged", "cache", "engine_state_reset", "engine_health",
        "error", "next_action",
        "cred_defaults", "identity_count",
        # G25 评审的名册补全：两个新 census 类，空名单（没有授权过主机 /
        # config.json 没存过旋钮）照例缺席。
        "cdp_hosts", "config_keys",
    }),
    # cred_defaults 的回执：`rows` 是答案本身（且只有脱敏的一半），其余四颗是
    # "这次改了什么 / 为什么没改"。打在默认值上就下线，与 sys_state 同一口径。
    "CredDefaultsModel": frozenset({
        "changed", "forgotten", "error", "next_action",
    }),
}


# A few tools project their own dicts instead of returning models (feed_read
# hands back one dict per feed, assembled field by field), so the policy above
# has no class to look up. Name the model behind each such container and the
# same pruning applies. Resolved lazily: `feed` is imported on first use, where
# the module's import-cost split already puts it.
_WIRE_PROJECTED_ROWS = {"feeds": "FeedResult"}
_WIRE_MODEL_CACHE: dict = {}


def _wire_projected_model(field_name: str):
    name = _WIRE_PROJECTED_ROWS.get(field_name)
    if name is None:
        return None
    if name not in _WIRE_MODEL_CACHE:
        import dhole_mcp.feed as _feed
        _WIRE_MODEL_CACHE[name] = getattr(_feed, name)
    return _WIRE_MODEL_CACHE[name]


def _wire_default(model: type, name: str):
    """The declared default for one field, or a sentinel for default_factory."""
    from pydantic_core import PydanticUndefined
    field = model.model_fields[name]
    if field.default is not PydanticUndefined:
        return field.default
    return _WIRE_EMPTY_SENTINEL


_WIRE_EMPTY_SENTINEL = object()


def _wire_omittable(model: type, name: str, value) -> bool:
    """True when this field's value says nothing its absence would not say."""
    if name not in _WIRE_OMIT.get(model.__name__, frozenset()):
        return False
    default = _wire_default(model, name)
    if default is _WIRE_EMPTY_SENTINEL:
        return not value
    return value == default and type(value) is type(default)


def _wire_scalar(name: str, value):
    """Trim the two numbers that cost more than they say.

    duration_ms came back as 1583.716630935669 (32 chars) and fetched_at as an
    ISO string with microseconds plus offset (48 chars). Sub-millisecond timing
    and sub-second freshness change nothing an agent acts on, so the first rounds
    and the second loses its fraction - 80 chars per call for two fields that
    never warranted them.
    """
    if name == "duration_ms" and isinstance(value, float):
        return round(value)
    if name == "fetched_at" and isinstance(value, str) and "." in value:
        return value.split(".")[0] + "Z"
    return value


def _wire_child_model(annotation):
    """The model class behind list[CrawlPage] / CrawlPage, if there is one."""
    from typing import get_args
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in get_args(annotation or ()):
        if isinstance(arg, type) and issubclass(arg, BaseModel):
            return arg
    return None


def _wire_prune(data: dict, model: type) -> dict:
    """Prune an ALREADY-serialized row against its own model's policy.

    Working on the serialized dict rather than rebuilding from the live object is
    the point: CrawlResponseModel's own serializer drops the per-row constants a
    sitemap map has in common, and rebuilding would hand them all back.
    """
    out: dict = {}
    for name, value in data.items():
        if _wire_omittable(model, name, value):
            continue
        child = _wire_child_model(model.model_fields[name].annotation
                                  if name in model.model_fields else None)
        if child is not None and isinstance(value, dict):
            out[name] = _wire_prune(value, child)
        elif child is not None and isinstance(value, list):
            out[name] = [(_wire_prune(item, child) if isinstance(item, dict)
                          else _wire_dump(item)) for item in value]
        else:
            out[name] = _wire_scalar(name, value)
    return out


def _wire_dump(obj) -> dict:
    """Serialize one response the way the wire wants it: no silence-filled fields."""
    if isinstance(obj, BaseModel):
        model = type(obj)
        fields = model.model_fields
        out: dict = {}
        for name, value in obj.model_dump().items():
            if _wire_omittable(model, name, value):
                continue
            child = _wire_child_model(fields[name].annotation
                                      if name in fields else None)
            if child is not None and isinstance(value, dict):
                out[name] = _wire_prune(value, child)
            elif child is not None and isinstance(value, list):
                out[name] = [(_wire_prune(item, child) if isinstance(item, dict)
                              else _wire_dump(item)) for item in value]
            else:
                out[name] = _wire_scalar(name, value)
        return out
    if isinstance(obj, (list, tuple)):
        return [_wire_dump(item) for item in obj]
    if isinstance(obj, dict):
        out: dict = {}
        for key, value in obj.items():
            child = _wire_projected_model(key)
            if child is not None and isinstance(value, list):
                out[key] = [_wire_prune(row, child) if isinstance(row, dict)
                            else _wire_dump(row) for row in value]
            elif isinstance(value, BaseModel):
                out[key] = _wire_dump(value)
            elif isinstance(value, list):
                out[key] = [_wire_dump(i) for i in value]
            else:
                out[key] = _wire_scalar(key, value)
        return out
    return obj


def _wire_result(obj, images: Optional[list] = None) -> tuple:
    """The (content_list, structured_dict) pair every tool call returns.

    ``images`` (element captures from web_read {\"screenshot\"} actions) are
    prepended as ImageContent blocks: the text channel keeps the full JSON
    payload, and the pixels ride alongside it so a multimodal client sees them
    without any base64 flowing through the JSON itself.
    """
    from mcp.types import ImageContent, TextContent
    text, payload = _wire_json(obj)
    content: list = []
    for img in (images or []):
        data = (img or {}).get("data")
        if data:
            content.append(ImageContent(
                type="image", data=data,
                mime_type=(img or {}).get("mime_type", "image/png")))
    content.append(TextContent(type="text", text=text))
    return content, payload


def _call_result(result):
    """One _dispatch return as a CallToolResult, text-only.

    A tuple means the tool has a structured payload: the text channel carries it
    in full and that IS the whole wire surface (17.0 removed the opt-in
    structuredContent double envelope; nobody reads the same JSON twice).
    """
    from mcp.types import CallToolResult
    if isinstance(result, tuple):
        content_list, _structured = result
        return CallToolResult(content=content_list)
    return CallToolResult(content=result)


def _wire_json(obj) -> tuple:
    """(text, structured) for one response - the only place the compact form exists.

    17.0: 无条件压实。``DHOLE_WIRE_FULL`` 逃生口已注销 —— "字段缺席 = 默认值"
    这份契约写进了工具文档与 instructions，一个读不懂缺席语义的调用方也读不懂
    工具文档；保留开关等于把两套线格式都当永久承诺养着。
    """
    payload = _wire_dump(obj)
    return json.dumps(payload, ensure_ascii=False,
                      separators=(",", ":")), payload
