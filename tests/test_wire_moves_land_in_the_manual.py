"""搬家不是丢：每一条从常驻层搬走的说法，都必须真的能在手册里读到。

B5-17 把 connect 面从 44,016 字压到 40,010。用户划的刀是"用法细节可以离开 wire，落点
= 同一份语料两个门（`sys_guide` 与 `dhole skill install`）"，仓库自己那张
`test_wire_shape` 的挂牌写的也是同一句话：**搬家，不是缩写**。

那么这条守卫就是这个批次唯一的防伪手段。删掉的句子如果语料里没有，`sys_guide` 也
答不出来，那一次搬家就不是优化而是失传 —— 而且它不会报错，只会让下一个 agent 从零
重新发明一遍踩过的坑。所以下面这张表逐条点名"wire 上再也没有、语料里必须有"的说法，
用词边界匹配，缺一条红一条。

反证同批钉住：把语料里的那句删掉，守卫必须能看见（历史上覆盖率守卫就是这么伪装成
通过的 —— W10 那次把描述里的键名抹掉，覆盖率反而一片绿）。
"""

from __future__ import annotations

import pytest

from dhole_mcp import guide
from dhole_mcp.server import DHOLE_INSTRUCTIONS, MasterFetchServer

#: 每条 = (wire 上已删的说法, 语料里必须出现的原文片段)
MOVED_LESSONS = [
    # web_read：三档与代价
    ("archive 档要花 10-30 秒", "10-30s"),
    ("neural focus 每页 12.5 秒 CPU", "12.5s"),
    ("css_selector 只作用 HTML，别的整篇返回", "applies to HTML only"),
    ("pages 只裁提取、文件仍整篇下载", "only trims **extraction**"),
    ("check_login 要配 cache_ttl=0 才不吃缓存", "cache_ttl=0"),
    # web_read.schema：整段交给手册的那颗
    ("不写 type 只回第一条", "FIRST match"),
    ("写的 type: array 才回全部", "returns all of them"),
    ("自造关键字不起作用而不是降级", "any other JSON-Schema keyword is ignored"),
    ("重复记录必须要容器选择器", "one record per container"),
    ("每个字段最多 200 条", "Max 200 records per field"),
    # CDP：为什么第一次要人批
    ("CDP 一挂就带上那台 profile 全部登录态", "logged-in"),
    ("授权记在 cdp_hosts.json，删它即撤销", "cdp_hosts.json"),
    # web_act：动作链的形状与懒加载
    ("一个对象只能带一个动作键", "one action key per object"),
    ("snapshot 先数、按 index 打、expect 校验", "expect"),
    ("isTrusted 门控的懒加载不吃合成滚动", "isTrusted"),
    ("没有视觉模型，读图的是你自己", "no vision model"),
    # web_request：为什么 GET 被拒
    ("GET 被拒：缓存/升档/存档归 web_read", "web_read owns"),
    ("写请求只发一次、不重试", "sent once"),
    # 爬与 discover 的经济账
    ("整轮没有字符预算，只有每页 20000", "20000"),
    ("Crawl-delay 不是这里的旋钮", "Crawl-delay"),
    # 搜索
    ("consensus 数的是索引家族不是原始命中", "FAMILIES"),
    ("引擎池的上限与带 key 的引擎", "Keyed backends"),
    # 状态与身份
    ("auth/cookies 必须同对象带 host", "per host"),
    ("identity_applied 只回名字和 host 范围", "identity_applied"),
    ("凭据的值不出进程", "Values never leave the process"),
    # 订阅
    ("since 传上一次最新的 published 值", "items_older_than_since"),
    ("没有日期的条目是留下并计数，不是丢", "undated"),
    # instructions：第四刀搬走的逐字段读法，落点是 topic='fields' 那一卷
    ("is_stale + content_age_days 说时效", "content_age_days"),
    ("quality_score 只属于 PDF", "quality_score"),
    ("original_url 只在重定向时出现", "original_url"),
    ("fetcher_used / escalation_path 说哪一档答的", "escalation_path"),
]


def _corpus() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))


@pytest.mark.parametrize("label,needle", MOVED_LESSONS,
                         ids=[name for name, _ in MOVED_LESSONS])
def test_a_moved_lesson_still_has_a_home(label, needle):
    """搬走的说法必须还在语料里，而且还在某一条 `sys_guide` 门后。"""
    assert needle in _corpus(), (
        f"「{label}」已经从常驻层删掉了，但手册里没有对应的说法（找过：{needle!r}）。"
        "补进 references/ 对应那一卷，否则这次搬家是失传。")


def test_the_topic_door_reaches_the_volume_that_carries_it():
    """落在文件里还不够，得是 `sys_guide` 真能送出去的那一卷 —— topic 表若指错文件，
    知识就在磁盘上而门后没有。"""
    served = set()
    for topic in guide.TOPICS:
        _head, body = guide.read(topic)
        served.add(body)
    blob = "\n".join(served)
    assert blob, "sys_guide 一个字都没送出去：topic 表与磁盘脱钩了"
    missing = [n for _l, n in MOVED_LESSONS if n not in blob]
    assert not missing, f"这些说法在磁盘上但 sys_guide 读不到: {missing}"


def test_the_guard_notices_a_deleted_lesson():
    """反证：把语料里的一句抹掉，上面的覆盖率守卫就该红 —— 它必须真的在看。"""
    corpus = _corpus()
    stripped = corpus.replace("no vision model", "", 1)
    assert "no vision model" in corpus and "no vision model" not in stripped
    assert any("no vision model" not in stripped for _l, n in MOVED_LESSONS if n == "no vision model")


def test_the_wire_no_longer_pays_for_what_moved():
    """另一半账：搬走的东西确实不在常驻层了，否则这条守卫是在给重复文本发通行证。"""
    wire = DHOLE_INSTRUCTIONS + "\n".join(
        (td["description"] or "") + "".join(
            (v or {}).get("description", "") for v in
            (td.get("inputSchema", {}).get("properties") or {}).values())
        for td in MasterFetchServer._TOOL_DEFS)
    for needle in ("12.5s", "FIRST match", "isTrusted", "cdp_hosts.json",
                   "content_age_days", "escalation_path"):
        assert needle not in wire, f"{needle!r} 还在常驻层，搬家没搬完"


def test_connect_stays_inside_the_packed_budget():
    """常驻层的总账：一次连接到底发多少字。

    尺子用 `tests/tool_payload_measure.py` 同一套算法（compact JSON，不美化），
    数字只能往下走。上限钉在实测值 +0 的位置：任何一句新描述写进常驻层都会顶红它，
    于是"要不要为这句话付每一次连接"变成一次显式决定，而不是顺手写进去的结果。
    """
    import json

    # 与 `tests/tool_payload_measure.py` 同一把尺子：MCP 发的是 `{"tools":[...]}`，
    # 那层信封的 10 个字符也算连接期开销。
    tools = json.dumps({"tools": MasterFetchServer._TOOL_DEFS}, ensure_ascii=False,
                       separators=(",", ":"))
    total = len(tools) + len(DHOLE_INSTRUCTIONS)
    assert total <= CONNECT_CEILING, (
        f"常驻层现在 {total:,} 字，上限 {CONNECT_CEILING:,}。要抬上限就是一次产品决定："
        "要么把新句子搬进 sys_guide 语料（按需付），要么说清为什么它必须每次连接都到。")


#: 实测的棘轮位。B5-17 四刀：44,016 → 40,010（instructions 3,532 → 3,070）；
#: 17.0 移除四颗状态工具：40,010 → 33,099（其中 `sys_state` 描述与那四行路由文本
#: 一起收走）；2026-10-04 G25 评审的写操作确认门：33,099 → 33,460（+361）——
#: web_request 的 confirm 属性与描述句必须住在连接期：藏在签名里的 confirm 会被
#: dispatch 当表外名拒掉，拒绝消息教的重试根本到不了闸门，所以这不是「搬去
#: sys_guide 按需付」能解决的句子，是它必须每次连接都到的显式决定（棘轮第一次
#: 上抬，理由即此）。同日 sys_state 名册补全（cdp_hosts / config 两类进册）以
#: 自身描述收紧 + ANTI_DETECT_DESC（wire 上两份）对冲，33,460 → 33,452——棘轮
#: 随实测回落。2026-10-04 G25 阶段5（M1/M2/M4/M5 四项）第一刀真往下：web_act
#: 的 anti_detect 换短指针（完整阶梯只住 web_read 一处，-83）、captcha 纪律归一
#: （墙的清单归 web_screenshot，-41）、doc_parse 扩展名清单归 file_path（-58）、
#: fetch_content 每页帽 8000→3000（响应侧，不占这页）——33,452 → 33,270。棘轮
#: 随实测回落。改它必须写理由 —— 要往上抬就是「这句话值不值每次连接」的一次
#: 显式决定，要往下砍就是一次瘦身。
#: 2026-10-05 G25 阶段6（G1，评审 High）：web_read 的 extraction_type 枚举加
#: 'check'（「这页变了吗」回执：带源站校验子回源，304 即没变，200 逐字比对给
#: 分叉窗口）。评审原文把它记在预算账内（"枚举零参数成本、wire 约＋40"），实测
#: +66（枚举 10 + 指针描述 56）——33,270 → 33,336。这是那笔账的兑现，不是新词
#: 挤占：换到的是一次几百字节的变更探测，替掉整页重取。
#: 同日阶段7（A1/A2，评审 High/Medium）：web_screenshot 并入 web_act 的截图动作
#: （四颗捕获旋钮 full_page/save_to/image_type/quality 进动作对象，纪律文案归
#: sys_guide behavior 卷）＋ web_resolve 下线（web_read 同样跟随重定向并回报落点）。
#: 13 → 11 颗，实测 -3,014 —— 33,336 → 30,322。棘轮随实测大幅回落；web_act 自己
#: +301（旋钮进描述），instructions -56（两条死路由句出、一句截图进 web_act 的）。
#: 2026-10-05 批次 3：doc_parse 补 `offset`。这不是"又想到一句文案"，是一条被实测
#: 抓到的自相矛盾：截断回执里印着 `Next offset: N`，而 doc_parse 自己**拒收** N（入参
#: 名单只有 file_path/cwd/encoding）。建议做一次调用方不许做的事，就得为那句建议付
#: 一次往返 —— 上限里买的就是"印出来的数字能原样传回去"这一件事。实测 +226，
#: 30,294 → 30,520。
#: 2026-10-05 批次 6c：12 处 wire `enum` 数组从 schema 里摘掉（实测 -319，连同把值集
#: 写进描述的重排），换成 dhole 自己的校验器（extraction_type / focus_mode /
#: focus_engine 三颗新写；force_fetcher / anti_detect / mode / freshness 四颗本来就有
#: —— 摘 enum 之前先逐颗确认这一点，否则非法值第一次落到按字面量写的内部代码）。理由：
#: 宿主那道 JSON-Schema 门拒收非法值时**不列合法值**（实测原话 "must be equal to one
#: of the allowed values"），而 dhole 的报错印出整份列表加方向（`Invalid freshness:
#: 'decade' (day|week|month|year)`）。Python 侧本来就是宽类型，枚举只让那句更有用的错误
#: 永无机会运行（房内主张见 params.py 关于 `Literal` 只是文档那段）。值集没有消失：
#: 它从数组搬进了描述散文（`'day', 'week', 'month' or 'year'`）与拒本文案两处，由
#: tests/test_the_enum_argument_values_are_checked_by_dhole.py 双向守着"每颗参数要么
#: 自己点全、要么指着点全的那一份"。
#: 三次合账：上一记实测位 30,294（web_act 的 actions 增列 hover/drag，账写在
#: tests/tool_defs_lock.json 的 comment 里），30,294 +226 -319 = 30,201，批次 5 的
#: `max_total_chars` 再 +273 → **30,474**。前两次加参数同时删了更贵的东西、净降；
#: 这一笔是这把尺子立起来之后**第一次上抬**，而抬它的理由就是它省下的那笔：没有
#: 整轮帽，默认尺寸的爬取交回 324,001 字符（实测那次 223,599），宿主把载荷截断落盘，
#: 调用方付的是整轮 token 加一次重爬。273 字每次连接，买的是"一次调用的结果读得完"
#: —— 房内规矩问的那句"这句话值不值每次连接"，这是它值的那一种。
#: 2026-10-05 批次 7：CDP 的授权对象从"调试端口主机"改成"这一次要点名去读的那个站"
#: （实测：名册 ["127.0.0.1"] 而围栏拿目标主机比对，attach 每一次都撞死）。措辞随之
#: 从 "the FIRST attach to a host" 改成 "for a site"，并把 "until it says true" 收成
#: "without it" —— 净 -7，30,474 → **30,467**。一次修 bug 顺带把话说短了，这是棘轮
#: 该往下走的那种。
CONNECT_CEILING = 30_467


def test_the_price_table_in_the_manual_is_the_current_one():
    """配置那一卷里的价目表必须等于尺子此刻的读数。

    这卷是 `sys_guide(topic='config')` 送出去的：操作员照它决定要不要裁注册面。
    一个过期的"总开销"不会报错，只会让人按错的价做决定 —— 所以这里只认逐字符相等，
    而且连价目表里那颗最贵的（`web_read`）一起钉住：它变了而文档没变，就是文档在说谎。
    """
    import json

    from dhole_mcp.server import DHOLE_INSTRUCTIONS as IN, MasterFetchServer

    defs = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}

    def wire(sel):
        return len(json.dumps({"tools": [defs[n] for n in sel]}, ensure_ascii=False,
                              separators=(",", ":"))) + len(IN)

    allnames = list(defs)
    total = wire(allnames)
    # 单价的口径写死在这里：注册这颗工具带来的**总开销增量**（含它前面那个逗号）。
    # 手册里的价目表按同一个定义写，否则同一颗工具会有三个"成本"。
    web_read = total - wire([n for n in allnames if n != "web_read"])
    cfg = (guide._ROOT / "references" / "configuration.md").read_text(encoding="utf-8")
    assert f"**{total:,} chars**" in cfg, (
        f"配置手册写的连接期总开销不是实测值（现在量到 {total:,}）—— 改文档，"
        "或说明为什么这次改动不该进 wire")
    assert f"`web_read` {web_read:,}" in cfg, (
        f"价目表里 web_read 的单价过期了（按增量口径实测 {web_read:,}）")
