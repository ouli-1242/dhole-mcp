"""Dhole 的搜索引擎层（v7.5：内置 ddgs 元搜索骨架）。

运行时没有 API key、没有账号、没有第三方服务。真正的多后端抓取 + 解析 + 轮转住在
`search_metasearch.py`（从 ddgs 内置并裁剪，MIT，署名见 NOTICE.ddgs.txt）。本模块是
dhole 这一侧的薄适配层：把 dhole 的 web_search 参数（engines、freshness、site、region、
page）映射到元搜索上，把结果映射回带跨后端共识的 RawResult，并构造按引擎的报告。

后端（全部免密，注册表 12 家，**默认池也是全部 12 家**）：baidu、bing、sogou、so360、
bing_global、yahoo、yandex、brave、duckduckgo、marginalia、shenma、seznam。
没有引擎需要 opt-in —— 池就是注册表，所以 `engines=` 只能收窄它，永远不能加进一个
不存在的名字。
17.0 撤掉了 5 个后端，也撤掉了读者可能怀念的两项能力：
**微信公众号文章的检索**（sogou_weixin 是唯一的公众号索引，Bing/百度都搜不全这个池子）
和**知识库直查**（wikipedia / grokipedia / baidu_baike 的 JSON/词条入口；通用引擎仍能搜到
这些站点，只是不再把词条当成结构化答案）。17.1 让 yahoo 作为 Bing 家族的第二个入口
返回（可用性冗余，不加家族），并加入独立小网索引 marginalia。
引擎**并行**运行；一家遇到 CAPTCHA / 限流 / 没有主题匹配，就只是不交结果，其余引擎
继续扛。搜索 100% 走 HTTP（不开浏览器）—— 唯一那个 Patchright 浏览器只为 web_read 留着。

DHOLE_SEARCH_PROXY（http/https/socks5）是调用方可以点名的唯一出口，用来对付按 IP
限流 —— 那是任何抓取器都无法从单个 IP 里逃开的一件事。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_metasearch = None


def _get_metasearch():
    """只为真实搜索加载沉重的抓取后端。

    导入 search_metasearch 会拉进 primp/httpx/lxml/fake_useragent。把这笔开销挡在
    缓存命中、校验失败与服务器启动之外。测试可以直接 monkeypatch `_metasearch`；
    这个 helper 尊重那种写法。
    """
    global _metasearch
    if _metasearch is None:
        from dhole_mcp.search_metasearch import metasearch as loaded
        _metasearch = loaded
    return _metasearch


# 公开的默认引擎池（顺序 = 粗略偏好）。web_search 里 `engines=None` 经元搜索走这份池子。
# 组合：直连主力 2（baidu/bing）+ 稳定国际 3（bing_global/duckduckgo/yahoo）+
# 常被限流/地区墙 5（brave/sogou/so360/shenma/yandex）+ 小众观察 2（seznam/marginalia），
# 12 家全部入池 —— 17.0 之后没有 opt-in 引擎，注册表就是默认池。
# **池序 = 可信度分层**（2026-10）：连通稳定的主力在前，按 IP 限流（sogou/so360
# 都按 IP 限流、so360 还有校验页、shenma 有 AliWAF 惩罚页）、常被反爬/地区挡
# （yandex/brave）的居中，小众浅索引垫底。这张序的唯一作者是对应的
# `_PRIORITY_TIERS`（见下），合并序的稳定分区也读那张表 —— 改判级两处一起动，
# 别只挪池子。
# baidu 排首位：国内直连、独立索引（百度自家索引，非 bing/google 代理），实测无反爬。
# bing 走 cn.bing.com，同样无需 VPN；sogou/so360/shenma 是另外三家国内独立索引
# （服务端渲染；shenma 是 UC/阿里的移动索引，夸克同一套基础设施）。
# bing_global 是 www.bing.com 那套国际索引：与 cn 版结果几乎不重合，所以它补的是**覆盖面**
# 而不是家族数（家族按底层索引归，两者同属 bing）。yahoo 是 Bing 家族的第二个入口，
# 补的是可用性（同一套索引，不加家族）。国内直连常需代理，被墙时由引擎冷却
# （连续 3 次连接失败 → 冷却 10 分钟）兜住，不会每轮陪跑。
# seznam 是捷克门户的自有索引：新接入的观察位（段末第二），seznam 家族。
# marginalia 是独立小网索引（英文为主），放在末位：答得上时提供一个独立家族。
# 共识家族：**12 引擎 / 9 家族**（baidu、bing+bing_global+yahoo+duckduckgo、sogou、
# so360、shenma、yandex、brave、seznam、marginalia），渲染出来是 "x of 9"。
# 分母跟着家族走而不是引擎走。
# NOTE 双份定义：search_metasearch._DEFAULT_BACKENDS 是同一份列表的 backend 名
# 版本。合成一处需要 search_engines 在模块顶层 import metasearch 链（primp/lxml），
# 而这里的惰性导入正是为了避免拖重依赖 —— 所以留两份 + 由
# tests/test_engine_registry.py::test_default_pool_definitions_agree 钉住一致性。
DEFAULT_ENGINES = ("baidu", "bing", "bing_global", "duckduckgo", "yahoo",
                   "brave", "sogou", "so360", "shenma", "yandex",
                   "seznam", "marginalia")

# 引擎可信度分层（2026-10）：合并序按它做稳定分区（主力先说话），池序按它排。
# 判级依据是本仓库自己的实测记录（CHANGELOG / improvement-plan / 各引擎类的
# docstring），不是引擎的宣传：baidu/bing 直连稳定无反爬；sogou/so360 按 IP
# 限流（so360 另有 HTTP 200 校验页）；yandex 常按地区跳转；brave 抓取版有反爬墙
# （它的 keyed 入口 brave_api 反而稳定）；marginalia 小网浅索引；seznam 新接入
# 未积累观察，先按小众放。keyed 引擎一并分层：点名混跑时同一张表说话。
# 这**不是相关性宣称**：relevance 由重排器判，这张表只决定平局时谁的意见靠前、
# 以及一行由多家引擎共同确认时展示谁的名字（backend 取 first-seen，与从前一样）。
_PRIORITY_TIERS = {
    "baidu": 0, "bing": 0,                                   # 直连稳定的主力
    "bing_global": 1, "duckduckgo": 1, "yahoo": 1,           # 稳定的国际入口
    "tavily": 1, "brave_api": 1, "exa": 1,                   # keyed 稳定入口
    "sogou": 2, "so360": 2,                                  # 国内独立索引，按 IP 限流
    "shenma": 2,                                             # 同上：AliWAF 惩罚页按 IP 限流
    "brave": 2, "yandex": 2,                                 # 国际，常被反爬/地区挡
    "bocha": 2, "deepseek": 2, "firecrawl": 2,               # keyed 其余
    "seznam": 3, "marginalia": 3,                            # 小众/浅索引，垫底
}
_DEFAULT_TIER = 2  # 未登记的名字（engines= 点了新引擎）从中间起，不武断抬也不武断压


def priority_tier(backend: str) -> int:
    """一家引擎的可信度层级；未登记的名字给中间档。"""
    return _PRIORITY_TIERS.get(backend, _DEFAULT_TIER)

# 国内裸网可达的引擎（其余要 VPN/代理）。只用于 `dhole -v` 那行说明与文档措辞 ——
# 网络可达性是环境问题（实测 brave 有时直连也通），所以别把它当抓取策略用。
_CN_DIRECT = frozenset({"baidu", "bing", "so360", "sogou", "shenma", "yandex"})

# 每个后端所属的索引家族（按底层索引/提供方归，用于共识）。duckduckgo 与 bing 都
# 返回的同一个 URL 只算**一个**家族（都是 Bing 的索引）；duckduckgo 与 brave 都返回
# 则是**两个**家族 = 更强的权威信号。
_INDEX_FAMILY = {
    "duckduckgo": "bing", "bing": "bing",
    # bing_global 是 bing 的国际索引：同一家族的第二个入口。家族按**底层索引**归，
    # 不按入口归 —— 与 bing 同源的 URL 同时出现在两边时必须算一个家族。
    "bing_global": "bing",
    "brave": "brave",
    "yandex": "yandex",
    "sogou": "sogou",
    "baidu": "baidu", "so360": "so360",
    # yahoo 与 bing/bing_global/ddg 共用 Bing 索引：多一个入口，不多一个家族。
    "yahoo": "bing",
    # marginalia 是自己的爬虫、自己的索引：独立家族。
    "marginalia": "marginalia",
    # seznam 是捷克门户自有索引（实测全球查询里混着捷克站，自有索引的形状）：
    # 独立家族 —— 宁可按独立计也不并进 bing（并错会把虚的共识当真的）。
    "seznam": "seznam",
    # 神马是 UC/阿里的移动搜索自有索引（夸克 quark.sm.cn 同一套基础设施，
    # 一个家族一个入口，不另开席）。
    "shenma": "shenma",
    # brave_api 是 Brave 官方付费 API；与免密抓取版 brave 同一套索引 ——
    # 同一 URL 两边都出现时是一个家族答的话，不是两个（yahoo 之于 bing 同理）。
    "brave_api": "brave",
}

# 谁的索引答得出中文。与 `_CN_DIRECT` 不是一回事：那张表说的是"这台机器直连拨不拨得通"
# （环境问题，实测会飘），这张表说的是"中文长尾在谁的库里"（索引归属）。2026-10-11 全真
# 模拟 S3 量的正是这两者的差：混合搜索里 baidu 被"凑够数就取消"提前砍掉，两轮中文查询
# 全 low，手动 `engines=["baidu"]` 一次就出公众号与知乎 —— 而那一刻还在飞的那几家境外
# 索引，填满的只是英文那一半的配额。
_CN_INDEX_ENGINES = frozenset({"baidu", "sogou", "so360", "shenma"})

# 中日韩字符，不是"中文语言检测"：一条混着术语的查询（`PCIe 7.0 规范 发布 128GT/s`）
# 英文索引也能答一半，但它要的中文那一半只有上面那三家有。判据只问"有没有这些字符"，
# 所以一条纯英文查询永远走不到这里 —— 英文那一侧的行为逐字不变，与既有那条
# 「快引擎填满配额就该早退」的钉子同一条命。
_CJK_RE = re.compile(
    "[\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")


# 2.4 · 语言车道。查询自己的字符构成是唯一免费的语种信号（`region` 参数说的是
# 「结果给哪个市场」，管不到引擎偏好）。实测（2026-10，提升计划 2.4 的现场）：英文查询
# `transformer attention mechanism survey` 回 8 条全中文站点——默认池里境内三家 +
# cn.bing 在本网络快、先答、先填满配额，境外索引还在飞就被「够数早退」砍掉。车道只做
# 两件事，都在 metasearch 层：早退别砍 preferred 车道里一家都没答话的那侧（等到第一
# 家答完即可，硬期限照旧兜底），以及合并序 preferred 车道在前（稳定分区，不是重排，
# 一条不丢）。
#
# 两条线来自提升计划（>30% / <5%），中间地带是混合查询（中文意图 + 英文术语，
# `PCIe 7.0 规范 发布 128GT/s`）—— 它不选边，行为与从前逐字相同。bing 归中文车道是
# 因为它是 cn.bing 那套境内索引（`_INDEX_FAMILY` 注释：与 bing_global 几乎不重合），
# 与「这台机器直连拨不拨得通」的 `_CN_DIRECT` 不是一回事。
_CN_LANE = frozenset({"baidu", "sogou", "so360", "shenma", "bing"})
_EN_LANE = frozenset({"bing_global", "duckduckgo", "yahoo", "brave",
                      "yandex", "marginalia", "seznam"})


def language_lane(query: str) -> frozenset:
    """这条查询的 preferred 引擎车道；空集 = 中间地带，不选边。"""
    stripped = "".join((query or "").split())
    if not stripped:
        return frozenset()
    ratio = len(_CJK_RE.findall(stripped)) / len(stripped)
    if ratio > 0.30:
        return _CN_LANE
    if ratio < 0.05:
        return _EN_LANE
    return frozenset()


def cn_index_engines(query: str) -> frozenset:
    """仍在飞、而这一轮还不能不等的那几家（非 CJK 查询交回空集）。

    交回集合而不是布尔，是因为等谁必须说得出名字：`_stop_laggards` 那一刀原来不分
    对象，一家还没答话的引擎只要不在 keyed 名单里就取消掉，并把它的下落写成
    `preempted`（"别人够说了，不等它了"）。中文查询里那一顶帽子扣到 baidu 头上，
    就是 S3 两连败的全部机制。
    """
    return _CN_INDEX_ENGINES if _CJK_RE.search(query or "") else frozenset()

_FRESHNESS_TO_TIMELIMIT = {"day": "d", "week": "w", "month": "m", "year": "y"}

# 哪些后端真的会把日期约束发上线。16.0 实测：逐家对
# build_payload(timelimit=None) 与 build_payload(timelimit='w') 做差分；测试会拿这些
# payload 钉住这张表，所以某家引擎加上或丢掉这颗参数时表不会漂。没有列在这里的都收下
# `timelimit` 却忽略它 —— 这就是响应要点名"谁被过滤了"、而不是声称整轮都被过滤的原因。
_DATE_AWARE_ENGINES = frozenset({
    "bing", "bing_global",     # filters=ex1:"ez{d|w|m|y}"
    "brave",                   # tf=p{d|w|m|y}
    "duckduckgo",              # df={d|w|m|y}
    "baidu",                   # gpc=stf=<起>,<止>|stf_type=2（期4.4 接线，判据是差分）
    "bocha",                   # keyed: freshness=oneWeek
    "tavily",                  # keyed: time_range=week
    "exa",                     # keyed: startPublishedDate=<now-7d>（绝对窗口）
    "firecrawl",               # keyed: tbs=qdr:w
    "brave_api",               # keyed: freshness=pw（官方 API 的短码词表）
})


def date_support(engines) -> tuple:
    """一轮池子里 ``（能被日期过滤的, 不能的）`` 两份名单。"""
    pool = set(engines or ())
    return sorted(pool & _DATE_AWARE_ENGINES), sorted(pool - _DATE_AWARE_ENGINES)


# 收了 `timelimit` 形参、payload 里却一个字节都不落的引擎，此前只在吞掉它的那一行留了
# 一句 `noqa: ARG002`。这一族其实分两种，而**责任方不同**，响应里的说法必须跟着分：
#
#   · 在下面列出的：dhole 自己的代码点名了那个时间参数（就是那句"未实现"注释里的名字），
#     所以这是 **dhole 没接线**，不是那家的能力问题 —— 报告不许替第三方断言"它不能按
#     日期过滤"。
#   · 不在下面、也不在 `_DATE_AWARE_ENGINES` 的：dhole 不知道该家有什么可发的日期参数，
#     于是只说"dhole 没给它发日期过滤" —— 一句只关于我们线路的事实。
#
# 值 = dhole 注释里点名的参数名（一条自述，不是核实过的上游文档结论）。加一家就必须同时
# 改它自己那行注释，`test_a_date_claim_blames_the_side_that_is_ours.py` 会钉住这两处。
#
# 期4.4 之后这张表**是空的**：baidu 是它唯一的住户，而 `gpc=stf=` 现在真的差分得出
# 一颗键（`Baidu.build_payload`），于是它搬进了 `_DATE_AWARE_ENGINES`。表留着而不是
# 删掉，因为它同时是那句归责措辞的唯一作者 —— 下一次有人点名一家"参数存在但没接线"，
# 就得在这里登记一行，并在它自己的 `noqa` 注释里写同名参数。
_DATE_PARAM_UNWIRED: dict[str, str] = {}


def split_date_blindness(blind) -> tuple[list[str], list[str]]:
    """把"没收到日期过滤"的引擎按责任方分成 (dhole 没接线的, 线路里没有这一说的)。"""
    names = [n for n in blind if n]
    return ([n for n in names if n in _DATE_PARAM_UNWIRED],
            [n for n in names if n not in _DATE_PARAM_UNWIRED])


@dataclass
class RawResult:
    """One merged result before wire compaction.

    The field comments record provenance: ``consensus`` counts independent index
    families, ``sources`` lists every backend that returned the URL, and ``neural_raw`` is
    the un-normalised rerank score (``None`` = no model measured it this round).
    """

    title: str
    url: str
    snippet: str
    source: str              # 去重胜出的后端（第一个交回该 URL 的）
    position: int = 0        # 合并序里的位置，从 1 起
    consensus: int = 1       # 交回该 URL 的独立索引家族数
    sources: tuple = ()      # 交回该 URL 的全部后端（由 multi_search 写入）
    # 交叉编码器的**未归一** sigmoid，由 reranker.rerank() 盖在行上；None = 这一轮
    # 没有模型量过它。它必须跟着行走完整条链，理由是那句 badge 的合法性：链上流转的
    # 分数是集合内归一的（第一名按构造恒为 1.0，min/max 相同时全体为 1.0），而一个
    # "集合内最好"的数永远说不出"这一整轮都不相关"。要让用户可见的 `high` 由证据铸、
    # 而不是由位置铸，证据就得活着走到铸 badge 那一步（体验修复刀2-S1）。
    # 带在行上而不是第三个元组元素：`rerank()` 交回 (result, score) 对是它 docstring
    # 里写明的兼容契约，五处测试桩也按二元组喂 —— 改元组数会把"加一条证据"变成一次
    # 破坏性改动。
    neural_raw: Optional[float] = None
    # 期4.1（同上例：三颗都是带默认值的**字段**，元组宽度一字不动）。值是
    # `search_metasearch.extract_kept` 用 `serp_dates.read_row` 读好的 ISO 串，
    # 精度取 day|month|year|relative，basis 是命中的版面原文。空串 = 这一行没有日期
    # （线上零字节）。只有实测过容器的四家会落值，keyed API 交回空串。
    published: str = ""
    published_precision: str = ""
    published_basis: str = ""
    # 来自垂直源（arXiv/GDELT/Stack Exchange/GitHub/HN）的行。它们与网页行共用重排
    # 与日期账本，但**不是**索引家族：engines_consensus 保持为空，也不许按一个家族计。
    vertical: bool = False
    vertical_kind: str = ""


@dataclass
class EngineReport:
    """What became of one engine this round.

    ``status`` keeps the upstream's raw token; ``ok``/``blocked``/``preempted`` are its
    lossy view, kept for the gates that already read them. ``item_nodes``/``usable`` of
    ``-1`` mean no parser was observed this round, which is a different fact from
    "parser observed, produced 0 rows".
    """

    name: str
    ok: bool = False        # 解析出至少 1 条结果
    blocked: bool = False   # 限流 / CAPTCHA / 被拒 / 超时 / 报错
    preempted: bool = False # 已有足够后端交回结果而被取消（不是 blocked）
    error: str = ""
    # 上游 metasearch 的原始状态 token（ok/empty/blocked/circuit_open/timeout/
    # error:*/init_error:*/no_key:*/preempted）。三个布尔把它有损地折叠过：
    # "empty"（引擎答了、解析出 0 条）此前既不进 ok 也不进 blocked，于是在
    # engines_used / engine_blocked 两个列表里同时消失 —— 解析器坏了的形态正是
    # 这样。留着原 token 让下游能把它单独报出来，而不必再发明第四个布尔。
    status: str = ""
    # 该引擎最近一轮的解析产出（来自 metasearch 的产出统计）。gate 需要它们才能把
    # "这个查询真没结果"和"我们的解析器跟不上页面了"分开 —— 前者改写查询有用，
    # 后者再打一轮只是给同一个坏掉的解析器重复加压。-1 = 没观测。
    item_nodes: int = -1
    usable: int = -1
    #: 引擎自滤之前、href/title 都成立的行数。与 usable 一起才分得清"解析器没取出"
    #: （raw=0）与"后处理把可用行全丢了"（raw>0、usable=0）。-1 = 没观测。
    raw_usable: int = -1
    yield_verdict: str = ""


def _engine_yield() -> dict:
    """metasearch 层的每引擎产出快照。惰性取，拿不到就当作无观测。

    注意 `_metasearch` 缓存的是**函数**不是模块（`from … import metasearch`），
    在它身上找 engine_health 只会静默拿到空字典 —— 那样 gate 就永远看不到产出，
    且因为异常被吞掉，看起来"什么都没坏"。所以这里直接 import 模块。
    """
    try:
        from dhole_mcp import search_metasearch
        return search_metasearch.engine_health()
    except Exception:
        return {}


def _cooldowns() -> dict:
    """仍在冷却的引擎（{backend: 剩余秒数}）；没有观测 -> {}。"""
    try:
        from dhole_mcp import search_metasearch
        return search_metasearch.cooldowns()
    except Exception:
        return {}


def _refusal(name: str, why: str, common: dict, cooldowns: dict) -> EngineReport:
    """一行"这家这一轮没答"，加上它真的在冷却时的那句"还要几秒"。

    从前这一格有两位作者：`circuit_open` 自己拼 "; retried in Ns"，`blocked` 与 `timeout`
    一个字都不拼 —— 于是点名 engines=[brave] 的人拿到 "blocked/captcha (circuit opened)"
    加一句 "retry in a moment"，而同一时刻池路径的邻居写着 "retried in 46s"（R01-016）。
    数只有一个出处（`search_metasearch.cooldowns()`），说它的人也必须只有一个。

    量不到就一个字不写：这一族在替身层与"这一轮没人被拒"时表是空的，而一句关于没量过的
    事的秒数比不报更坏（F11 立的门槛）。`preempted` 不在这里 —— 它不是"等一会再问"，
    是配额一到就取消，重发必然撞上同一个停止点。
    """
    left = cooldowns.get(name, 0)
    if left:
        why = f"{why}; retried in {int(round(left))}s"
    return EngineReport(name=name, blocked=True, error=f"{why}", **common)


def _normalize_domain(value: str) -> str:
    """交回可比较的主机名，去掉只影响观感的开头 ``www.``。"""
    value = value.strip()
    if not value:
        return ""
    try:
        parsed = urlparse(value if "://" in value or value.startswith("//") else f"//{value}")
        host = parsed.hostname or ""
    except ValueError:
        return ""
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _is_domain_or_subdomain(host: str, domain: str) -> bool:
    return bool(domain) and (host == domain or host.endswith(f".{domain}"))


def _passes_site_filter(url: str, site: Optional[str], exclude_sites: Optional[list[str]]) -> bool:
    try:
        host = _normalize_domain(urlparse(url).hostname or "")
    except ValueError:
        return False
    if site and not _is_domain_or_subdomain(host, _normalize_domain(site)):
        return False
    for ex in exclude_sites or []:
        if _is_domain_or_subdomain(host, _normalize_domain(ex)):
            return False
    return True


async def fetch_source_for_similar(url: str, *, timeout: int = 10, max_chars: int = 4000
                                   ) -> dict:
    """为 find_similar 抓一个 URL：走一次性的冒充 HTTP 抓取（primp）—— 单张任意
    页面，不是反复命中的引擎，所以元搜索的后端轮转在这里不适用。

    交回的是**它看见了什么**，不是两件词的省略：`{"title","body","status","reason","detail"}`，
    `reason` ∈ `"" | no-response | no-bytes | blocked | http-error | no-text`。从前这里把五种
    事压成 `('','')`，门里就只剩下「(blocked or offline)」这种两可的话，`retryable` 也只能整颗
    True（评审 R02-138）。`title`/`body` 在拒绝那几支照旧是空的 —— 空的正文是**结果**，
    而原因必须另有一格，因为「没有正文」有四条不同的出路。
    """
    seen = {"title": "", "body": "", "status": 0, "reason": "", "detail": ""}
    try:
        from dhole_mcp.fetcher import HTTPSession
        from dhole_mcp.search_metasearch import _PROXY as _p
        from bs4 import BeautifulSoup
        # 轮转池留在这里（不从已移除的 SERL 模块导入）
        _pool = ["chrome", "safari", "firefox", "edge"]
        async with HTTPSession(impersonate=_pool, proxy=_p,
                               stealthy_headers=True, retries=1) as sess:
            resp = await sess.get(url, timeout=timeout)
            # 取回层的 Response  mimic 的是 scrapling 的脸：状态码在 `.status` 上
            # （`status_code` 是 primp 自己那层）—— 只读后者会把 404 读成 0 再报成 200。
            seen["status"] = int(getattr(resp, "status", None)
                                 or getattr(resp, "status_code", 0) or 0)
            text = (getattr(resp, "body", None) or b"").decode(
                getattr(resp, "encoding", None) or "utf-8", errors="replace")
    except Exception as e:
        seen["reason"], seen["detail"] = "no-response", str(e)[:200]
        return seen
    if not text:
        seen["reason"] = "http-error" if seen["status"] >= 400 else "no-bytes"
        return seen
    # 轻量的拦截页启发式
    low = text[:4000].lower()
    if any(m in low for m in ("access denied", "are you a robot", "captcha", "403 forbidden")):
        seen["reason"] = "blocked"
        return seen
    title = ""
    try:
        soup = BeautifulSoup(text[:60000], "lxml")
        t = soup.find("title")
        if t:
            title = t.get_text(" ", strip=True)
    except Exception:
        pass
    try:
        import trafilatura
        body = (trafilatura.extract(text[:60000], include_comments=False,
                                    include_tables=False) or "")
    except Exception:
        body = ""
    seen["title"], seen["body"] = title, body[:max_chars]
    if not seen["body"]:
        seen["reason"] = "http-error" if seen["status"] >= 400 else "no-text"
    return seen


async def multi_search(
    query: str,
    max_results: int = 10,
    *,
    engines: Optional[list[str]] = None,
    site: Optional[str] = None,
    exclude_sites: Optional[list[str]] = None,
    region: str = "us-en",
    freshness: Optional[str] = None,
    page: int = 0,
    server=None,  # 为签名兼容而接受；搜索 100% 走 HTTP（不开浏览器）
    query_map: Optional[dict[str, str]] = None,
    round_stats: Optional[dict] = None,
) -> tuple[list[RawResult], list[EngineReport]]:
    """并行跑免密元搜索后端；交回 (ranked, reports)。

    `engines` 选后端（None = 完整默认池）。`freshness` 映射到各引擎的时间过滤。
    `site` / `exclude_sites` 既作查询前缀（让认 site: 的后端在上游过滤），也在最终
    URL 上复查（给不认的后端兜底）。`page` 是 0 起（dhole API）-> 给后端时 1 起。
    `server` 不参与（为调用点兼容保留；搜索从不碰浏览器）。

    `round_stats` 是一只**调用方传进来的袋子**：metasearch 把"这一轮的墙钟账"（为第二个
    家族多等的那一段）写进去。它不走返回值，因为 `(ranked, reports)` 是这个适配层的既有
    契约（五处测试桩与 `dhole engines` 探针按二元组解包）；它也绝不走模块级全局，因为
    搜索会并发，一个全局数会把上一轮的等待报进下一次响应里。
    """
    # 把 site:/-site: 前缀拼进查询（尽力的上游过滤）。
    def _apply_site(q: str) -> str:
        if site:
            q = f"site:{site} {q}"
        for ex in exclude_sites or []:
            q = f"-site:{ex} {q}"
        return q

    q = _apply_site(query)
    # 对 query_map 里每个按引擎的查询同样套 site: 过滤（扇出）。
    if query_map:
        query_map = {eng: _apply_site(qq) for eng, qq in query_map.items()}
    timelimit = _FRESHNESS_TO_TIMELIMIT.get(freshness) if freshness else None
    backend_page = page + 1  # dhole 从 0 起 -> 后端从 1 起

    # 把 dhole 的引擎名映射到元搜索后端（'auto'/None/旧名由它处理）。
    mapped = list(engines) if engines else None

    metasearch = _get_metasearch()
    results_dicts, status = await metasearch(
        q, max_results, region=region, timelimit=timelimit,
        page=backend_page, engines=mapped, query_map=query_map,
        round_stats=round_stats,
    )

    # 映射成带跨后端共识的 RawResult，并套上最终的 site 过滤。
    ranked: list[RawResult] = []
    for i, d in enumerate(results_dicts, start=1):
        url = d.get("href", "")
        if not _passes_site_filter(url, site, exclude_sites):
            continue
        backends = d.get("backends") or [d.get("backend", "")]
        families = {_INDEX_FAMILY.get(b, b) for b in backends}
        ranked.append(RawResult(
            title=d.get("title", ""),
            url=url,
            snippet=d.get("body", ""),
            source=d.get("backend", backends[0] if backends else ""),
            position=i,
            consensus=len(families),
            sources=tuple(backends),
            # 期4.1：日期三颗跟着行走完 metasearch → RawResult → SearchResult 这条链
            # （搬运，不判断；唯一的作者仍是 serp_dates.read_row）。keyed API 的行没有
            # 这几颗 → 空串，缺席即零字节。
            published=d.get("published", "") or "",
            published_precision=d.get("published_precision", "") or "",
            published_basis=d.get("published_basis", "") or "",
        ))

    # 按后端从元搜索状态生成报告。每个分支都带上原始 token：
    # 布尔是有损折叠，empty 就是被折掉的那一格。
    yield_rows = _engine_yield()
    cooldowns = _cooldowns()
    reports: list[EngineReport] = []
    for name, st in status.items():
        y = yield_rows.get(name, {}) if isinstance(yield_rows, dict) else {}

        def _y(key: str, default: int) -> int:
            # 不能用 `y.get(k) or default`：0 条容器 / 0 条可用正是这里的信号本身，
            # `or` 会把它变成"没观测"，判据就永远看不到漂移。
            try:
                return int(y[key])  # type: ignore[literal-required]
            except (KeyError, TypeError, ValueError):
                return default

        common = {
            "status": st,
            "item_nodes": _y("last_nodes", -1),
            "usable": _y("last", -1),
            "raw_usable": _y("last_raw", -1),
            "yield_verdict": str(y.get("verdict", "") or ""),
        }
        if st == "ok":
            reports.append(EngineReport(name=name, ok=True, **common))
        elif st == "preempted":
            # 被取消 ≠ 被拦。第一轮的测试者正是把这一格读成"引擎被墙"，进而误判
            # dhole 没有走 VPN。写清楚它不是失败，也不涉及网络。
            reports.append(EngineReport(name=name, preempted=True,
                                        error="preempted - other engines met the result "
                                              "quota first, so this one was cancelled "
                                              "mid-flight (not a block, not a failure)",
                                        **common))
        elif st == "blocked":
            reports.append(_refusal(name, "blocked/captcha (circuit opened)",
                                    common, cooldowns))
        elif st == "circuit_open":
            reports.append(_refusal(name, "circuit open (recently blocked; skipped)",
                                    common, cooldowns))
        elif st == "timeout":
            reports.append(_refusal(name, "timed out", common, cooldowns))
        elif st.startswith("error") or st.startswith("init_error") or st.startswith("no_key"):
            reports.append(EngineReport(name=name, blocked=True, error=st, **common))
        else:  # "empty" —— 引擎答了但一条可用结果都没解析出来
            # "查了，没有" 与 "根本没看到结果页" 是两件事：状态码是 3xx 时上游没给
            # 正文，而 metasearch 把这两种一起记成 empty（实测 bing_global 直连被
            # www.bing.com 地域跳转挡掉，每轮白等 1-5 秒，读起来却是"这个查询没结果"）。
            # token 保持 "empty" 不动（它同时是 engine_empty 的判据），只把原因写进
            # error —— 分类不许为了换个字符串而把引擎从两个列表里同时抹掉。
            code = y.get("http")
            why = "no results"
            if isinstance(code, int) and code >= 300:
                why = f"no parsable results (HTTP {code}"
                why += ", redirect not followed)" if code < 400 else ")"
            reports.append(EngineReport(name=name, error=why, **common))

    return ranked, reports
