# -*- coding: utf-8 -*-
"""R01-036（吸收 R01-003）· focus 交回的"相关块"必须说得出它到底找没找到。

现场（P08 谢临，S3a / S3b 两次同形态；逐字见 `sessions/P08-实录.md`）：
他要核对一句引文是否出自某页，`web_read(focus="CRF DBSCAN k-means IoU 0.71 田块边界分割精度对比")`
交回 `[Focus: …; showing 6 of 105 blocks by BM25 relevance]`，留下的是页面标题、导航和
"关注阿里云公众号 + 客服电话 4008013260"。**那句话根本不在页面上**，而响应的形状读起来
像"按相关性找到了 6 块"。他的结论是："这两条我自己打开浏览器看" —— 核对引用的工具
退化成搜索结果，一条待审引用被放弃。

修前用同一份 fixture 量到的机制（决定性，不是猜测）：

```
所有块得分 0.0 → keep 为空 → 走 fallback（取前 5 个"最接近的"）
头部照旧写 "showing 5 of 28 blocks by BM25 relevance"
stats = {scorer: bm25, total: 28, kept: 5}   # 没有任何一处说"这是掐来的，不是找到的"
查询里 3 个词在这一页的出现次数：0 / 0 / 0
```

R01-003（focus 按字面 token 丢代码块）与本条同一个缺件：**分数与阈值的语义没被交出去**。
本刀一起补：块的来源（cut / closest）与点名的词在本页的字面存在性。神经档早有自己的
`_INTENT_MISS_FLOOR` 一句，BM25 档此前什么都没有。
"""

from __future__ import annotations

from dhole_mcp.focus import focus_content

MISSING = "quantum tunneling astrophysics"
# 28 块的一页，与上面那句共享 0 个词 —— 修前它交出 5 块并称之为 BM25 相关结果
PAGE = "\n\n".join([
    "# 基于深度学习的田块边界提取方法",
    "本文综述遥感影像中田块边界提取的研究进展。",
    "## 1 引言",
    "农田地块信息是农业遥感的基础数据。",
    "## 2 方法",
    "模型采用编码器-解码器结构，损失函数为交叉熵。",
    "## 3 结果",
    "实验表明该方法边界连续、破碎化减少。",
    "关注我们获取更多信息，客服电话 4008013260。",
] * 4)

# 一半词在页上、一半不在（P08 那一发的形状：问的是专有名词，页上只有通用词）
PARTIAL = "CRF DBSCAN 田块边界"


def _header(out: str) -> str:
    return out.split("\n\n")[0]


# ─── A · 一块都没过阈值时，不许把掐来的前几块叫作"相关" ────────────────────

def test_a_page_with_no_match_does_not_claim_relevance_selection():
    stats: dict = {}
    head = _header(focus_content(PAGE, MISSING, stats=stats))
    low = head.lower()
    assert "by bm25 relevance" not in low, (
        f"什么都没找到时，头部不许再宣布'按 BM25 相关性留下 N 块'：{head!r}")
    assert "not match" in low or "no match" in low or "nothing" in low, head


def test_the_fallback_is_named_in_the_stats_the_caller_reads():
    stats: dict = {}
    focus_content(PAGE, MISSING, stats=stats)
    assert stats.get("selection") == "closest", stats
    assert stats.get("terms_total") == 3, stats
    assert stats.get("terms_matched") == 0, stats
    assert len(stats.get("terms_absent") or []) == 3, stats


def test_the_words_that_are_not_on_the_page_are_named():
    """点名缺席的词是这一刀的核心：读者要的是"这句话到底在不在这一页"。

    不许只查"这个词出现在头部里" —— 头部本来就引用了整个 query，那种断言是假绿
    （§11.7）。要查的是它被说成**不在这一页**。
    """
    head = _header(focus_content(PAGE, MISSING))
    low = head.lower()
    assert "occur" in low or "found" in low, f"没有'在不在这一页'这一句: {head!r}"
    after = low.split("occur", 1)[-1] if "occur" in low else low
    for word in ("quantum", "tunneling", "astrophysics"):
        assert word in after, (
            f"缺席的词必须点名在'不在页上'那句之后，找到的是 {after!r}")


def test_the_best_mode_says_the_same_thing():
    """mode='best' 自己写头部 —— 同一件事不许有两个作者、两种说法。"""
    stats: dict = {}
    head = _header(focus_content(PAGE, MISSING, mode="best", stats=stats))
    assert stats.get("selection") == "closest", stats
    assert "by bm25 relevance" not in head.lower(), head


CODE_PAGE = PAGE + "\n\n```python\nfor i in range(3):\n    print(i)\n```"


def test_the_header_clauses_are_separate_sentences():
    """三段话（相关性/来源 → 缺席的点名词 → 删掉的结构块）实测黏成过一句：
    "…shares no token with your query 5 of the 6 words you named occur nowhere…"
    （真 CLI 的 S3a）。拼接的标点由一处作者负责，不许各段自己带。"""
    head = _header(focus_content(CODE_PAGE, MISSING))
    low = head.lower()
    assert "dropped" in low, f"用例前提：这一发必须真删过一个代码块: {head!r}"
    assert ".;" not in low and ";." not in low, head
    idx = low.find("none of the")
    assert idx > 0, head
    assert low[:idx].rstrip()[-1] in ".:;", (
        f"'缺席的那些词'那句前面必须是句子结束符: {head!r}")


# ─── B · 不许把"没找到"修成"什么都算没找到" ────────────────────────────────

def test_a_real_hit_is_still_called_a_hit():
    page = PAGE + "\n\n## 4 对比\nquantum tunneling 的 astrophysics 观测综述见本节。"
    stats: dict = {}
    head = _header(focus_content(page, MISSING, stats=stats))
    assert stats.get("selection") == "cut", stats
    assert stats.get("terms_matched") == 3, stats
    assert not stats.get("terms_absent"), stats
    assert "showing" in head.lower() and "of" in head.lower(), head


def test_a_partial_query_names_only_the_words_that_are_missing():
    stats: dict = {}
    head = _header(focus_content(PAGE, PARTIAL, stats=stats))
    absent = stats.get("terms_absent") or []
    assert "crf" in absent and "dbscan" in absent, stats
    assert not any("田块" in t for t in absent), (
        f"页上有的词不许被说成缺席：{absent!r}")
    assert stats.get("terms_matched", 0) >= 1, stats
    assert "quantum" not in head, head


def test_a_call_without_a_query_still_says_nothing_about_matches():
    """focus='' 那一支、单块页那一支都是 no-op：不许在这些形状上编造命中判定。"""
    stats: dict = {}
    assert focus_content(PAGE, "", stats=stats) == PAGE
    assert stats.get("selection") is None, stats
    st2: dict = {}
    assert focus_content("just one block of text", MISSING, stats=st2) == \
        "just one block of text"
    assert st2.get("ignored"), st2
    assert st2.get("terms_absent") is None, st2


# ─── C · 这两个数要上得了线，也要给出下一步 ────────────────────────────────

def _read_dispatch(monkeypatch, body: str, args: dict) -> dict:
    """真 `_apply_chunking`（focus 就在那一步跑），只替掉浏览器与 HTML→正文。"""
    import asyncio
    from types import SimpleNamespace

    from dhole_mcp import server as server_mod
    from dhole_mcp.models import ResponseModel
    from dhole_mcp.server import MasterFetchServer

    class _Sess:
        _is_alive = True
        cdp_attached = False
        launch_note = ""
        _hide_canvas = False
        _block_webrtc = False

        async def seed_cookies(self, url, cookies):
            pass

        async def harvest_cookies(self, url):
            return {}

        async def fetch(self, url, **kw):
            return SimpleNamespace(url=url)

    async def fake_get(self, sid, expected_type=None):
        return server_mod._SessionEntry(session=_Sess(), session_type="stealthy")

    async def auto(self, **kw):
        return "auto-under-test"

    async def named(self, sid, **kw):
        return sid

    monkeypatch.setattr(MasterFetchServer, "_get_session", fake_get)
    monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session", auto)
    monkeypatch.setattr(MasterFetchServer, "_ensure_session_browser", named)
    monkeypatch.setattr(server_mod, "_browser_deps_available", lambda: True)
    monkeypatch.setattr(server_mod, "_launch_identity", lambda **kw: ("same",))
    monkeypatch.setattr(server_mod, "_session_identity", lambda s: ("same",))
    monkeypatch.setattr(
        server_mod, "_translate_response",
        lambda page, extraction_type="markdown", *a, **kw: ResponseModel(
            url=page.url, status=200, content=[body], fetcher_used="stealthy",
            content_type="text/html", extracted_type="markdown", content_ok=True))
    out = asyncio.run(MasterFetchServer()._dispatch(
        "web_read", dict(args, force_fetcher="stealthy")))
    return out[1] if isinstance(out, tuple) and len(out) == 2 else {}


def test_the_verdict_reaches_the_envelope(monkeypatch):
    url = "https://example.com/field-blocks-" + "abc123"
    pay = _read_dispatch(monkeypatch, PAGE, {"url": url, "focus": MISSING})
    assert pay.get("focus_terms_total") == 3, pay
    assert pay.get("focus_terms_absent") == 3, pay
    assert pay.get("focus_selection") == "closest", pay
    na = pay.get("next_action") or ""
    assert "not" in na.lower() and ("cite" in na.lower() or "match" in na.lower()), na
    assert "focus=''" in na, f"出路要给出现成的那一步: {na}"


def test_a_hit_does_not_borrow_the_miss_advice(monkeypatch):
    url = "https://example.com/field-blocks-hit-77"
    body = PAGE + "\n\n## 4 对比\nquantum tunneling 与 astrophysics 的观测对照。"
    pay = _read_dispatch(monkeypatch, body, {"url": url, "focus": MISSING})
    assert pay.get("focus_selection") == "cut", pay
    assert pay.get("focus_terms_absent") in (0, None), pay
    na = (pay.get("next_action") or "").lower()
    assert "not a match" not in na and "not cite" not in na and "nowhere" not in na, na


def test_a_partial_miss_also_says_this_page_does_not_carry_it(monkeypatch):
    """P08 那一发实测走的是这一支：6 个块确实**过了阈值**（靠页面上有的那几个通用词），
    而他要核对的那 5 个专名一个字都不在这一页上。`selection=='cut'` 不是"找到了"。
    """
    pay = _read_dispatch(monkeypatch, PAGE,
                         {"url": "https://example.com/partial-88", "focus": PARTIAL})
    assert pay.get("focus_terms_absent") == 2, pay
    na = (pay.get("next_action") or "").lower()
    assert "crf" in na and "dbscan" in na, f"缺席的词必须点名到建议位: {na}"
    assert "cite" in na or "verify" in na, na
    assert "focus=''" in na, na


def test_a_call_without_focus_carries_none_of_these_numbers(monkeypatch):
    pay = _read_dispatch(monkeypatch, PAGE, {"url": "https://example.com/nf-9"})
    for key in ("focus_terms_total", "focus_terms_absent", "focus_selection"):
        assert not pay.get(key), f"{key} 出现在一发没跑 focus 的响应里: {pay.get(key)!r}"
