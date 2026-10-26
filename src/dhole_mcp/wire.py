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


# ─── 线格式压实（每次调用都要付的 token，不是一次性的）──────────────────────
#
# 在本服务器上实测：一个零内容的 404 返回 927 字符，而一次成功抓取的字节里有
# 33% 是停在自己默认值上的字段（is_stale=false、page_type="unknown"、links={}、
# retry_count=0 …）。一旦"缺席"表达了同样的意思，默认值就不再携带信息，所以线
# 格式把它丢掉。每次调用的信封税从约 380 字符降到约 40。
#
# 只压实**线上那一份**。ResponseModel 保留每一个字段，所以缓存信封、crawl 的
# 逐页聚合与 actions 那一档读到的仍是完整对象 —— 这里的 bug 不会污染存储。
#
# 政策按模型而非按字段名，因为同一个名字在不同行里含义不同：`source` 在 fetch
# 上是 "live"/"archive.org"，在搜索结果里是引擎名；`page_type` 在一个上默认
# "unknown"，在爬取页上默认 ""。而 0.0 不总是默认值 —— `relevance_score` 的
# 0.0 是重排器在说"跑题"，所以它不在任何省略集里；而表示"从未打过分"的
# `quality_score` None 在。
#
# 只有当字段等于它自己声明的默认值（或对于 default_factory 字段是空容器）时才
# 省略。别的值一律留下，包括每一个作为裁决而非默认值的 False（content_ok）。
_WIRE_OMIT: dict = {
    "ResponseModel": frozenset({
        "original_url", "cached", "session_id", "session_cookie_names",
        "is_truncated", "next_offset", "retry_count", "escalation_path",
        "extracted_type", "page_type", "source_type", "is_official",
        "content_age_days", "is_stale", "source",
        "archived_at", "quality_score", "total_size_bytes",
        "total_extracted_chars", "media", "links", "table_of_contents",
        # 0 = 不是 PDF（或没有抽到页）；只有 PDF 响应才为这两颗付钱。
        "pages_total", "pages_returned",
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
        # 浏览器档的网络账同理：干净的一页一个字节都不付（实测 163 页 55 个请求、0 失败）。
        # 只有失败响应 / 安全拦截时才上线，形状见 models.ResponseModel.network。
        "network",
        # 空正文的 0 不携带信息（缺席即"没有可计费的正文"）。
        "approx_tokens",
        # 没折叠任何样板时 0 不携带信息（缺席即"这页没有页内重复行"）。
        "duplicate_lines_collapsed",
        # C5：一次没被每次尝试的上限咬过的调用不该报一个与 `timeout=` 一模一样的数。
        # 缺席就是"没咬"（或这一发根本没走 HTTP 一档），所以上线的每一发都是收据。
        "timeout_applied",
        # R02-031 的另一半：这颗报的是"一次尝试被给了多少"，只有真的拨过号才有话说。
        # 0 = 这一发没有 HTTP 尝试（缓存回放、浏览器档自己的时钟），缺席就是那句"没有"。
        "attempt_cap_ms",
        # 分页基准：不带 focus 的调用走的是 'full' 那一路，也就是这颗字段的默认值 ——
        # 一句"这一路的数是全文"没有信息，而它是绝大多数调用。focus 一上，三颗各自上线
        # （'focused' / 未过滤的总长 / 那句基准），因为那时 `total_extracted_chars`
        # 已经不再描述整页了（实测一个 URL 随 focus 词交出过六个值）。
        "pagination_stream", "page_total_chars", "pagination_basis",
        # 0 = 切口本来就落在界上（或这是最后一片）；上线的每一发都是"挪了几个字"的收据。
        "chars_snapped_back",
        # R01-024：这句关于"这一窗是半页"的话从前接在正文尾巴上交付。空串 = 这一发没被
        # 切走任何东西，而那是绝大多数调用 —— 缺席就是"整页都给你了"。
        "truncation_note",
        # 块账（3.6）：0 = 这一发根本没跑 focus，缺席即"没有块可数"。跑了 focus 的两颗
        # 都至少是 1（`_untouched` 那一路也交 1/1 —— 整页一块正是读者唯一看不见的形状，
        # 0.3 的量具在这一格上翻过车，所以这里不许静默）。
        "focus_blocks_total", "focus_blocks_kept",
        # 同一族的另外三颗此前**没有**登记，于是不带 focus 的每一次调用都在付
        # `"focus_terms_total":0,"focus_terms_absent":0,"focus_selection":""` 这 64 个字符
        # （2026-10-23 实测：example.com 一发 wire 1,109 字符里有它们）。缺席 = 没跑 focus，
        # 与上面两颗是同一句话；上线的每一发都非默认值。
        "focus_terms_total", "focus_terms_absent", "focus_selection",
        # 实质（3.3/3.4）：0/"" = 这一发不是 HTML（PDF/JSON/图片/HEAD），或没什么可判的。
        # 判决上线的每一发都带着它背后的数（`substance_basis`），读者能自己核。
        "visible_text_chars", "substance", "substance_basis",
    }),
    "CrawlPage": frozenset({
        "depth", "fetcher_used", "title", "page_type", "content_chars",
        "is_truncated", "next_offset", "fetched_at", "lastmod", "summary",
        "error", "untrusted_content",
        # R02-142：行上的把手与信封同一约定 —— 干净的一行不付诊断的钱（None=缺席）。
        "diagnosis",
        # 3.4 那一维的两颗读数：一行绝大多数是正常正文，`substance=''`（没判）与
        # `substance='ok'`（判过且干净）都不该占 50 行爬取的字节。判决在场时它照样出现。
        "substance", "substance_basis",
    }),
    "CrawlResponseModel": frozenset({
        "discover_only", "truncated_by_max_pages",
        "truncated_by_time", "truncated_by_total_chars", "sitemap_truncated",
        "sitemaps", "robots_skipped", "error", "duration_ms", "ssrf_recheck",
        # R02-012 的三颗时钟读数：0 = 这一趟没有可报的越线/帽（跑在预算内、或没走抓取
        # 循环）。手册对这三颗的承诺都是"0/absent"，所以上线的每一颗都是收据。
        "deadline_ms_applied", "budget_overrun_ms", "attempt_cap_ms",
        # plan_id / next_resume_token 只在有计划、或一次续跑提前停下时出现 ——
        # 缺席仍是默认值（P11）。
        "plan_id", "next_resume_token",
        # R02-131：爬行信封的把手与 ResponseModel 同一约定 —— 一次干净的爬行（或一张
        # 没有报错的地图）不付诊断的钱，None 就是缺席。
        "diagnosis",
    }),
    # 批量信封：`ssrf_recheck` 是"这台机器跳过了重查"的那一句，只在真跳过时上线
    # （它原先在每一行的 metadata 里重复一遍，见 BulkResponseModel 的序列化器）。
    "BulkResponseModel": frozenset({"ssrf_recheck"}),
    "SearchResult": frozenset({
        "snippet", "source", "position", "fetch_relevance",
        "engines_consensus", "source_type",
        # G4：official=False（绝大多数行）省略；True 才上线（+15 chars/行）。
        "official",
        # R02-003：标题位上住的是本行地址时才有话可说。实测 1,067 行里 14 行命中（1.4%），
        # 所以缺席就是"这一行的标题不是它自己的地址" —— 绝大多数行零字符，同 official 那一族。
        "title_is_address",
        # S2：raw 的缺席**就是**它的含义（"这一轮没有模型量过"），所以默认值必须是
        # None 而不是 0.0 —— `_wire_omittable` 比的是 `value == default and
        # type(value) is type(default)`，于是 0.0（量过、判为不相关）留在线上，
        # None（没量过）才消失。把 0.0 也省掉就是把"量为零"说成"没量"，那是 R4 那颗
        # 谎在新位置重生。
        "relevance_raw",
        # 批次8 刀5：行级注入标记只在命中时上线（None=干净行，缺席即默认）。
        "untrusted_content",
        # 期5.4：形状旗子同理 —— 绝大多数行不命中，缺席就是"这一行的地址在常规形状里"。
        # 它是旗子不是判决：把它裁掉的是"没话说"，不是"判过且没问题"，两种都在字段说明里。
        "low_trust_source",
        # 期4.3：一行**没有**日期时，这三颗都停在 ""（=默认）上，一个字都不该上线。
        # 实测（冻存 SERP 走真 parser 与真盖章那一步）落值的行数：sogou 8 行里 4、baidu
        # 两个版面 5 里 2 / 6 里 2、bing 一个版面 4 里 4 而另一个 4 里 1、bing_global 10 里
        # 1（且那一行是相对的），so360 与 yandex 各 0 —— 所以"缺席"是常态而不是异常，
        # 而 `date_coverage` 说的正是这个稀疏程度 —— 让空串行也付三份 `""` 的字节，
        # 就是让读者花钱买一句"没有"。
        "published", "published_precision", "published_basis",
        # 期4.5：第四颗同族 —— 它只在 `fetch_content=true` 且那一行真被取回且页面自述
        # 了日期时上线。默认那一发搜索根本没有取任何页面，所以它是这一族里最常缺席的一颗；
        # 缺席说的都是"没有可核验的源站日期"，不是"源站没写日期"（两种含义都在字段说明里）。
        "published_verified",
    }),
    "SearchResponseModel": frozenset({
        "engines_used", "engine_blocked", "engine_empty", "engine_preempted",
        "date_filter", "consensus_basis", "cached", "rerank_mode",
        "fetch_hint", "related_queries", "fetched_pages", "duration_ms",
        # 期5.1/5.2/5.3：逐家的账、那句分数、那段短等全是"空即无话"（报错行、写这三颗
        # 之前就存在的缓存行、以及没为第二个家族等过的轮次都交空串/空表/0.0）。
        # 非空的那一发才是收据。
        "engine_detail", "families_answered_of_asked", "engine_waited_for_family_s",
        # S4：成对的"要了几条 / 回了几条"只在不相等时上线（相等时它不携带信息）。
        "requested", "delivered",
        # 没设共识地板（或它没丢任何东西）时，空名单不携带信息——缺席即"闸门
        # 什么都没丢"。
        "consensus_skipped",
        # 期4.3：{} 说的是"这一页没一行带日期、也没人问过日期"——缺席说的就是这句，
        # 所以空账上线只等于让每一发都付一遍"没有"的字节。非空的那一发才是收据
        # （有几行带日期 / 丢了几行 / 几行没法比）。
        "date_coverage",
        # R02-131：同一句约定在这一扇上也成立（`search._param_refusal` 从此交把手，
        # 而成功那一发仍然一格都不带）。
        "diagnosis",
        # P0 纵向搜索：默认那一发只问 web，三颗都停在 "" / [] 上 —— 缺席即
        # "没有纵向源参与、也没有改排序"，与上面 date_coverage 同一条约定。
        # 纵向那一发才有收据要付。
        "sources_used", "source_detail", "sort_applied",
    }),
    "FeedResult": frozenset({
        "source_title", "error", "discovered_from", "since",
        "note",
        # 没有文档就没有时刻可报：报错行的空戳上线只会读成「刚才确实取到了什么」。
        "fetched_at",
        # 与 ResponseModel.diagnosis 同一约定：一次干净成功不付诊断的钱（None=缺席）。
        "diagnosis",
    }),
    "FeedItem": frozenset({"title", "published", "summary", "summary_truncated",
                           "untrusted_content"}),
    # sys_state：普查行里几乎每个字段都是"大多数时候停在默认值"的按会话细节
    # （没开浏览器、没有 cookie、没有东西在过期）。`session_id` **不**可省略 ——
    # 一行没有自己的 id，就对任何事情都没有话可说。
    "SessionCensusRow": frozenset({
        "hosts", "cookies", "expires_in_s", "browser_open",
        "browser_type", "browser_created",
        # R02-029：驻留那一格同理是「大多数行没有」的细节 —— 没钉页时两格都沉默，
        # 钉着时才上线（正控制在 tests/test_a_pinned_page_answers_before_the_fact.py）。
        "pinned_url", "pinned_left_s",
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
        # R02-054：重排器的状态是一颗 census，没点名 show=['models'] 就一个键都不给。
        "reranker",
        # R02-130：把手与 ResponseModel 同一口径 —— 缺席即干净成功，落在 None 上不许上线。
        "diagnosis",
    }),
    # cred_defaults 的回执：`rows` 是答案本身（且只有脱敏的一半），其余四颗是
    # "这次改了什么 / 为什么没改"。打在默认值上就下线，与 sys_state 同一口径。
    "CredDefaultsModel": frozenset({
        "changed", "forgotten", "error", "next_action", "diagnosis",
    }),
    # feed_read 门里自己写的那句拒绝（R02-133）：这一副壳只有被拒时才会出现，`error` /
    # `summary` / `next_action` 三格恒有内容，`feeds` 恒空但要照发（它是这扇回执的既有形状）。
    # 只有把手那一格守同一条约定：没内容就整格缺席，不许以 null 顶替。
    "FeedRefusalModel": frozenset({"diagnosis"}),
    # 边界上那副门名壳（R02-130 的 unknown-tool 半边 · F123）：与 feed 那副同一约定 ——
    # 它只在被拒时出现，`error` / `summary` / `next_action` 三格恒有内容，只有把手守
    # "没内容就整格缺席"那条。
    "ToolRosterRefusalModel": frozenset({"diagnosis"}),
}


# 有几个工具自己拼 dict 而不是返回模型（feed_read 每条 feed 手拼一个 dict，逐字段
# 组装），所以上面的政策没有类可查。给每个这样的容器点名它背后的模型，同一套修剪
# 就能适用。惰性解析：`feed` 在首次使用时导入，模块的导入成本分工本来就把放在那里。
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
    """一个字段声明的默认值；default_factory 字段则返回哨兵。"""
    from pydantic_core import PydanticUndefined
    field = model.model_fields[name]
    if field.default is not PydanticUndefined:
        return field.default
    return _WIRE_EMPTY_SENTINEL


_WIRE_EMPTY_SENTINEL = object()


def _wire_omittable(model: type, name: str, value) -> bool:
    """字段值不携带"缺席"表达不了的信息时为 True。"""
    if name not in _WIRE_OMIT.get(model.__name__, frozenset()):
        return False
    default = _wire_default(model, name)
    if default is _WIRE_EMPTY_SENTINEL:
        return not value
    return value == default and type(value) is type(default)


def _wire_scalar(name: str, value):
    """修剪两个"花的比说的多"的数字。

    duration_ms 曾返回 1583.716630935669（32 字符），fetched_at 曾是带微秒和时区
    偏移的 ISO 串（48 字符）。亚毫秒的计时与亚秒的新鲜度不改变 agent 的任何行动，
    所以前者取整、后者丢掉小数 —— 两个从不值得这些字符的字段，每次调用省 80 字符。
    """
    if name == "duration_ms" and isinstance(value, float):
        return round(value)
    if name == "fetched_at" and isinstance(value, str) and "." in value:
        return value.split(".")[0] + "Z"
    return value


def _wire_child_model(annotation):
    """list[CrawlPage] / CrawlPage 背后的模型类（如果有）。"""
    from typing import get_args
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in get_args(annotation or ()):
        if isinstance(arg, type) and issubclass(arg, BaseModel):
            return arg
    return None


def _wire_prune(data: dict, model: type) -> dict:
    """按行自己模型的政策修剪一条**已经序列化**的行。

    在序列化后的 dict 上工作而不是从活对象重建，正是重点：CrawlResponseModel 自己的
    序列化器会丢掉一份 sitemap 地图各页共有的常量，重建会把它们全部还回来。
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
    """按线格式想要的样子序列化一个响应：不付"沉默填充"字段的钱。"""
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
    """每次工具调用返回的 (content_list, structured_dict) 二元组。

    ``images``（web_read {\"screenshot\"} 动作的元素截图）以 ImageContent 块
    前置：文本通道保留完整 JSON 载荷，像素并排同行，于是多模态客户端能看到它们，
    而 base64 不必流经 JSON 本身。
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
    """一次 _dispatch 返回值转成 CallToolResult，纯文本。

    元组表示工具有结构化载荷：文本通道完整携带它，那就是整个线上表面（17.0 删掉了
    可选的 structuredContent 双信封；没人想读两遍同一份 JSON）。
    """
    from mcp.types import CallToolResult
    if isinstance(result, tuple):
        content_list, _structured = result
        return CallToolResult(content=content_list)
    return CallToolResult(content=result)


def _wire_json(obj) -> tuple:
    """一个响应的 (text, structured) —— 压实形式唯一存在的地方。

    17.0: 无条件压实。``DHOLE_WIRE_FULL`` 逃生口已注销 —— "字段缺席 = 默认值"
    这份契约写进了工具文档与 instructions，一个读不懂缺席语义的调用方也读不懂
    工具文档；保留开关等于把两套线格式都当永久承诺养着。
    """
    payload = _wire_dump(obj)
    return json.dumps(payload, ensure_ascii=False,
                      separators=(",", ":")), payload


# ─── 连接期的面孔：一把算术，一个作者 ────────────────────────────────
#
# 同一串字节此前有三个作者：`updater._append_wire_cost` 里 doctor 报的那份指纹、
# `tests/test_tool_defs_hash_lock.py` 里锁的两半（各算一遍）、以及 `ops` 想读它的人
# 只能再算第三遍。指纹的价值全在"三处算的是同一件事"上：算法漂了，比对就失去意义，
# 而那种漂**不会报错**（字数相同内容不同这一族本来就是靠指纹才发现的）。所以现在
# 算术在这里，doctor / 锁 / CLI 都只是读者。
#
# 序列化口径是数字的一部分，不是实现细节：`separators=(",", ":")` + `sort_keys=True`
# + `ensure_ascii=False`，与计价（连接期字符数）同一份输入。改这一行等于换掉所有
# 历史 tag 里那些指纹的读法，所以它由 `tests/test_the_lock_is_written_by_a_command_
# and_never_by_hand.py` 的一枚黄金值钉住。

_FACE_KB = ("name", "description", "inputSchema", "annotations")


def minimal_face(defs: list[dict]) -> list[dict]:
    """一颗工具在 wire 上的那一份：name / description / inputSchema / annotations。

    `annotations` 缺席时不补空字典 —— 宿主读的是"这颗没声明"，而不是"声明了默认值"。
    """
    return [{k: td[k] for k in _FACE_KB if k in td} for td in defs]


def tool_defs_digest(defs: list[dict]) -> str:
    """锁的第一半：`_TOOL_DEFS` 全量的 sha256（含键的顺序之外的一切）。"""
    import hashlib
    return hashlib.sha256(json.dumps(defs, ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()


def wire_face_digest(defs: list[dict], instructions: str) -> str:
    """锁的第二半，也是 doctor 那一行的同一把算术：这次连接要付的整张脸。"""
    import hashlib
    payload = {"tools": minimal_face(defs), "instructions": instructions}
    return hashlib.sha256(json.dumps(payload, separators=(",", ":"),
                                     ensure_ascii=False, sort_keys=True)
                          .encode("utf-8")).hexdigest()


def face_cost(defs: list[dict], instructions: str) -> tuple[int, int]:
    """(颗数, 连接期字符数) —— 与 doctor 的 connect cost 行同一份输入。"""
    compact = len(json.dumps({"tools": minimal_face(defs)}, separators=(",", ":"),
                              ensure_ascii=False))
    return len(defs), compact + len(instructions)


def face_ledger(defs: list[dict]) -> list[dict]:
    """常驻层的账，按工具与参数拆开：每一句要每次连接都付的话值多少字符。

    存在的理由是一条被量过的事实：`CONNECT_CEILING` 当前余量为 0，所以"把这句搬上
    常驻层"不是一个措辞决定而是一笔要花掉别处的钱。没有这张账，抬棘轮的人只能整体
    重算一遍才知道某一句值多少；有了它，一句新话的价格、和腾出它的哪一句更贵，
    都是当场能问的。这里只报**测量**，不报余量 —— 余量的属主是那条测试的棘轮，
    把常数抄到这里就是第二个作者。
    """
    rows: list[dict] = []
    for td in minimal_face(defs):
        own = len(json.dumps(td, separators=(",", ":"), ensure_ascii=False))
        params = {name: len(json.dumps(spec, separators=(",", ":"),
                                       ensure_ascii=False))
                  for name, spec in (td.get("inputSchema") or {}).get(
                      "properties", {}).items()}
        rows.append({"tool": td["name"], "chars": own,
                     "description_chars": len(td.get("description", "")),
                     "params": params})
    return rows
