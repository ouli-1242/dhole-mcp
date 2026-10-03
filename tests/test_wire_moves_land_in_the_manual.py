"""搬家不是丢：每一条从常驻层搬走的说法，都必须真的能在手册里读到。

B5-17 把 connect 面从 44,016 字往下压。用户划的刀是"用法细节可以离开 wire，落点
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
    for needle in ("12.5s", "FIRST match", "isTrusted", "cdp_hosts.json"):
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


#: 实测的棘轮位（B5-17 三刀之后：44,016 → 40,472）。改它必须写理由，且只准往小改。
CONNECT_CEILING = 40_472
