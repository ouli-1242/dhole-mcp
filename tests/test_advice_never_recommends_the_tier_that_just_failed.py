"""批次 3：建议不许推荐一件它刚证明做不到的事。

2026-10-05 那一轮真实使用里，浏览器层整体起不来（`browser_binary_missing`），而**每一条**
壳页 / 403 / bot_wall 的 `next_action` 都在说"re-fetch auto-escalates to the stealthy
browser"、"Retry once with force_fetcher='stealthy'"。那是调用方当时唯一拿到手的建议，
命中率 0%。事实一直写在信封上 —— `escalation_path="browser_binary_missing"` 在跳过
stealthy **之前**就写好了（`server.py` 的升级段），而 `_finalize_result` 跑 `_agent_hints`
在它**之后** —— 所以这一刀不需要新状态，只需要读它。

判据：

1. `_browser_cannot_help` 认两种来源：被**故意**跳过（数据文档，`_never_escalate`），
   和**试过了而起不来**（escalation_path / error 里的那两个记号）。
2. 记号在场时，js_shell / bot_challenge / bot_wall / 403 四类建议一律不再点名
   stealthy 或 force_fetcher='stealthy'；换成"这一档现在不行"+ `dhole --doctor`。
3. 记号不在场时，原来的建议一个字都不许变弱 —— 把豁免做成万能开关，等于把一台
   好好的机器也降级成"别试浏览器"。
4. 批次 2 的 `extraction_collapsed` 不许把调用方送回同一条抽取管路（再抓 / 起浏览器），
   唯一真换结果的旋钮是 extraction_type。
5. `doc_parse` 印出来的那个 `Next offset: N` 必须传得回去：白名单、wire、类型表、
   分发四处同颗；而且真续得上（第二次调用从 N 开始，内容与第一次不重叠）。
6. 形状守卫：`next_action` 里"建议换档"的句子必须在 `_browser_cannot_help` 的 else 枝上，
   不许有人把它抄回一条无条件的话。
7. （R6）还有**第三种**"这一档帮不上"：它跑了、答了、交回来的就是手里这份。前六节
   的门只认"没跑"，于是 `fetcher_used="stealthy"` 的回执还在叫调用方去重试那一档
   —— 判据在 §7，含 rule 3 的镜像（那一档没跑过时原建议一个字不许变弱）。
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from dhole_mcp import params, quality, server as server_mod
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _agent_hints, _browser_cannot_help

URL = "https://example.com/page"
MARKS = ("browser_binary_missing", "browser_unavailable")


def _shell(**updates) -> ResponseModel:
    """A JS-shell answer of the shape quality.py actually receives."""
    base = dict(url=URL, status=200, content=["<div id=root></div>"],
                fetcher_used="http", extracted_type="markdown",
                total_size_bytes=48_000, page_type="js_shell",
                error="js_shell_detected: 48,000-byte body with 120 chars of text")
    base.update(updates)
    return ResponseModel(**base)


def _hint(result: ResponseModel) -> str:
    return _agent_hints(result)[1]


def _call(name: str, args: dict) -> dict:
    """The dispatcher's own two-step: validate, then coerce types.

    分两层不是洁癖：`_validate_tool_args` 管枚举与列表形状，`_coerce_arg_types` 管
    数字/布尔形状，测试只调前者就会把"字符串客户端的 12000"当成没被读过来骗过自己。
    """
    return params._coerce_arg_types(name, params._validate_tool_args(name, args))


# ─── 1 · 两种"帮不上忙"的来源 ────────────────────────────────────

@pytest.mark.parametrize("mark", MARKS)
def test_a_recorded_browser_skip_is_read_from_either_field(mark):
    via_path = _shell(escalation_path=f"http->stealthy:{mark}")
    via_error = _shell(error=f"js_shell_detected ({mark}: chromium-1228 missing)")
    assert _browser_cannot_help(via_path, via_path.url)
    assert _browser_cannot_help(via_error, via_error.url)


def test_a_data_document_is_still_a_refusal_not_a_failure():
    """被故意跳过的那一半（XML/JSON/PDF/非 GET）不许因为换了实现而失守。"""
    xml = _shell(url="https://docs.example.com/sitemap.xml", content_type="application/xml")
    assert _browser_cannot_help(xml, xml.url), "数据文档现在会被建议去渲染"


def test_a_plain_html_shell_with_a_working_browser_is_not_exempted():
    assert not _browser_cannot_help(_shell(), URL), (
        "没有任何记号却判成'浏览器帮不上'—— 那是把一台好机器也劝退了")


# ─── 2 · 记号在场：四类建议都不再点名那一档 ─────────────────────

def test_js_shell_advice_stops_promising_a_tier_that_was_skipped():
    hint = _hint(_shell(escalation_path=f"http->stealthy:{MARKS[0]}"))
    assert "force_fetcher='stealthy'" not in hint and "auto-escalates" not in hint, hint
    assert "not available" in hint, hint
    assert "dhole --doctor" in hint, "拒绝要给一条能查的路，而不是只说不行"


def test_bot_challenge_and_js_shell_share_the_same_refusal():
    challenged = _shell(error="bot_challenge_detected: cloudflare interstitial",
                        escalation_path=MARKS[0])
    hint = _hint(challenged)
    assert "stealthy" not in hint.replace("browser tier", ""), hint
    assert "dhole --doctor" in hint


def test_bot_wall_advice_names_the_knob_only_to_rule_it_out():
    """点名那颗旋钮是允许的 —— 只要那句话是"这次别用"，而不是"用一次试试"。

    判据写成"不许出现 force_fetcher='stealthy'"会把好文案一起杀掉：告诉调用方哪条路
    已经堵住，正是他能自己核对的那句。要禁的是那条祈使句。
    """
    wall = _shell(error="bot_wall_detected: captcha on the body",
                  escalation_path=MARKS[1])
    hint = _hint(wall)
    assert "Retry once with force_fetcher='stealthy'" not in hint, hint
    assert "cannot help this time" in hint, hint


def test_a_403_does_not_send_the_caller_to_the_tier_that_just_failed():
    denied = _shell(status=403, error="http_error_403",
                    escalation_path=MARKS[0], content=["Forbidden"],
                    page_type="unknown")
    hint = _hint(denied)
    assert "Retry once with force_fetcher='stealthy'" not in hint, hint
    assert "do not spend a call on force_fetcher='stealthy'" in hint, hint
    assert "switch source" in hint, hint


def test_the_skip_itself_is_explained_rather_than_recommended_around():
    """错误里就是那个记号时，建议是"别再用那一档"，并说明其它能力仍在线。"""
    hint = _hint(_shell(status=0, error=f"{MARKS[0]}: chromium install failed",
                        content=[], page_type="unknown", fetcher_used="none"))
    assert "do NOT re-fetch with force_fetcher='stealthy'" in hint, hint
    assert "search, plain reads, docs and feeds all still work" in hint, hint


# ─── 3 · 记号不在场：原来的建议不许变弱 ─────────────────────────

def test_a_recoverable_shell_still_gets_the_browser_suggestion():
    hint = _hint(_shell())
    assert "re-fetch auto-escalates to the stealthy browser" in hint, hint


@pytest.mark.parametrize("error", ["bot_wall_detected: captcha on the body"])
def test_a_recoverable_wall_still_gets_the_retry(error):
    assert "force_fetcher='stealthy'" in _hint(_shell(error=error,
                                                      page_type="captcha"))


def test_a_403_with_a_working_browser_still_gets_the_retry():
    hint = _hint(_shell(status=403, error="http_error_403", content=["Forbidden"],
                        page_type="unknown"))
    assert "Retry once with force_fetcher='stealthy'" in hint, hint


def test_the_render_of_a_data_document_is_still_refused_in_words():
    """被拒的那一句要说清"渲染会拿回浏览器自己的查看器" —— 这是理由，不是搪塞。"""
    xml = _shell(url="https://docs.example.com/sitemap.xml",
                 content_type="application/xml")
    hint = _hint(xml)
    assert "dhole will NOT render it" in hint, hint
    assert "viewer markup" in hint, hint


# ─── 4 · 抽取塌陷不是"再抓一次"能解决的 ─────────────────────────

def test_collapsed_extraction_points_at_the_one_knob_that_changes_the_result():
    collapsed = _shell(error="extraction_collapsed: markup holds 1,374 visible chars, "
                             "the extractor returned 196", page_type="list",
                       content=["£9.99 In stock"] * 11)
    hint = _hint(collapsed)
    assert "extraction_type='html'" in hint, hint
    assert "stealthy" not in hint and "escalat" not in hint, hint


# ─── 5 · doc_parse 的 offset 是真能传回去的那一颗 ────────────────

def test_the_offset_it_prints_is_one_it_accepts():
    """这一条就是事故本身：截断标记印 `Next offset: N`，而这颗工具原先拒收 N。

    四处必须同颗 —— 白名单、wire 属性、类型归一表、分发读法。少一处就是"印出来的
    数字传不回去"，而那句话是响应里教调用方做的事。
    """
    assert "offset" in server_mod._TOP_LEVEL_ARGS["doc_parse"], "白名单没放行"
    defs = {d["name"]: d for d in server_mod.MasterFetchServer._TOOL_DEFS}
    props = defs["doc_parse"]["inputSchema"]["properties"]
    assert "offset" in props, "wire 上看不见这颗参数"
    assert "enum" not in props["offset"]
    assert params._ARG_TYPES["doc_parse"]["offset"] == "int", "字符串客户端发不回数字"
    source = inspect.getsource(server_mod.MasterFetchServer._dispatch)
    assert 'args.get("offset"' in source, "分发器没有真的把这颗读出来"


def test_doc_parse_continues_where_the_note_said(tmp_path):
    """两刀拼起来必须**恰好**是整页：不重叠、不缺口、第二刀收口。

    这条不拿源文件比对（markdown 规范化会让字节对不上），而是拿两次响应自己的数字：
    `next_offset` 承诺的是"从这里往下读"，那就检查第一刀的正文 + 第二刀的正文长度
    正好等于 `total_extracted_chars`。帽是 40,000 字（`MAX_CONTENT_CHARS`，这颗工具
    没有自己的尺寸参数），所以夹具要落进 (40k, 80k] —— 短文件从来不截断，也就从来
    没人发现续读那颗参数不存在。

    R01-024 之后那句话不再接在正文尾巴上（它在 `truncation_note` 里），所以这一条
    从前要先 `split("\\n\\n[Truncated:")` 剪掉旁注才能拿到正文 —— 现在拿到的就是正文，
    断言反而更硬：`len(first.content[0])` 直接参与拼合，没有一个字符是被剪掉的。
    """
    text = "\n".join(f"line-{i:05d} {'x' * 50}" for i in range(1_100))
    doc = tmp_path / "long.md"
    doc.write_text(text, encoding="utf-8")

    first = asyncio.run(server_mod.MasterFetchServer().doc_parse(str(doc)))
    assert first.is_truncated and first.next_offset > 0, (
        f"这份 {len(text):,} 字的文件没被截断，测不到续读")
    body = first.content[0]
    assert f"Next offset: {first.next_offset}]" in first.truncation_note, "那颗数字没被说出口"
    assert "[Truncated" not in body, "旁注不许再混在这页自己的文字里交付"

    second = asyncio.run(server_mod.MasterFetchServer().doc_parse(
        str(doc), offset=first.next_offset))
    assert second.content, second.summary
    assert second.total_extracted_chars == first.total_extracted_chars, (
        "两刀量到的不是同一份全文")
    assert not second.is_truncated and (second.next_offset or 0) == 0, (
        "读到结尾了还在给下一刀的地址")
    assert len(body) + len(second.content[0]) == first.total_extracted_chars, (
        f"第一刀 {len(body):,} + 第二刀 {len(second.content[0]):,} ≠ 全文 "
        f"{first.total_extracted_chars:,} —— 那颗 offset 要么重叠要么缺口")


def test_an_out_of_range_offset_says_the_page_is_already_exhausted(tmp_path):
    """offset 越过全文长度不是一句 IndexError，是一次能读懂的收口。"""
    doc = tmp_path / "short.md"
    doc.write_text("just a little text", encoding="utf-8")
    r = asyncio.run(server_mod.MasterFetchServer().doc_parse(str(doc), offset=99_999))
    assert not r.is_truncated
    assert (r.next_offset or 0) == 0, "已经读完还在给下一刀的地址"


def test_a_string_offset_argument_is_read_as_a_number_not_a_crash():
    """`offset="12000"` 是字符串客户端的写法：它必须被读成数字，不能进切片下标。"""
    assert _call("doc_parse", {"file_path": "a.md", "offset": "12000"})["offset"] == 12000
    with pytest.raises(ValueError, match="offset"):
        params._coerce_arg_types("doc_parse", {"file_path": "a.md",
                                               "offset": "second page"})


# ─── 6 · 形状守卫 ───────────────────────────────────────────────

def test_recommending_the_failed_tier_is_gated_not_hardcoded():
    """每一条"换那一档试试"的话都必须站在 `_browser_cannot_help` 的 else 枝上。

    数一下：门有 4 处（js_shell/bot_wall/403 + `_never_escalate` 那条共用的外层门），
    建议文案里点名 stealthy 的地方不许多过门 —— 多出来的那一处就是有人抄了一份无条件的话。
    """
    source = inspect.getsource(quality._agent_hints)
    gates = source.count("_browser_cannot_help(result")
    assert gates >= 3, f"门比上一次少了（现在 {gates} 处）"
    assert source.count("force_fetcher='stealthy'") <= gates + 2, (
        "有点名 stealthy 的句子没在门上")
    # R6 加的那一道门：认"档跑了、答了、答案是垃圾"的门也必须至少有同样的三处，
    # 否则"已经升过级"这一族又会退回到叫调用方去重试那一档。
    spent = source.count("_render_tier_was_spent(result)")
    assert spent >= 3, f"'那一档已经交过这份答案'的门只剩 {spent} 处"


# ─── 7 · 档跑了、答了、答案就是手里这份（R6）───────────────────
#
# 上面 1–6 全是**这一档没跑**的形状：起不来、被跳过、被拒升。R5/C13 量到的是第三种，
# 而门认不出它：`fetcher_used="stealthy"` + `escalation_path="http→stealthy(all_failed)"`
# —— 浏览器档跑了、答了、交回一张壳页。回执于是自己打自己：`error` 说这是 shell，
# `next_action` 还在说 "re-fetch auto-escalates to the stealthy browser"。那次换档重试
# 实测烧掉 31.5s 到超时，而它照的就是这条建议。
#
# 判据仍然是 rule 3 那一族：只认**这次信封上真跑过的档**，别把一台好机器也劝退。

SPENT = "http→stealthy(all_failed)"


def _rendered_shell(**updates) -> ResponseModel:
    """浏览器交回来的那张壳页 —— 与 `_shell()` 只差 `fetcher_used` 与行程单。"""
    base = dict(fetcher_used="stealthy", escalation_path=SPENT)
    base.update(updates)
    return _shell(**base)


class TestWhichTiersRan:
    """`_tiers_that_ran` 认的是 server 写下的形状，两种 skip 写法都得认。"""

    @pytest.mark.parametrize("path, http, stealthy", [
        ("http→stealthy", True, True),
        (SPENT, True, True),
        ("http→stealthy→archive.org", True, True),
        ("direct:http", True, False),
        ("direct:stealthy", False, True),
        ("http(stealthy_skipped_no_budget)", True, False),
        ("preflight:timeout(skipped_stealthy)", False, False),
        ("preflight:dns_unresolvable(skipped_http+stealthy)", False, False),
        ("http(private_target_no_answer)", True, False),
        ("http->stealthy:browser_binary_missing", True, True),
        ("", False, False),
    ])
    def test_the_itinerary_is_read_as_written(self, path, http, stealthy):
        assert quality._tiers_that_ran(_shell(escalation_path=path,
                                              fetcher_used="")) == (http, stealthy), path

    def test_the_answer_came_from_the_tier_that_is_claiming_it(self):
        """行程单缺席时，`fetcher_used` 自己就是证据。"""
        assert quality._tiers_that_ran(_shell(escalation_path="",
                                              fetcher_used="stealthy")) == (False, True)

    def test_a_cache_hit_claims_no_tier_for_this_call(self):
        assert quality._tiers_that_ran(_shell(escalation_path="",
                                              fetcher_used="cache")) == (False, False)

    def test_spent_means_ran_and_not_already_exempted(self):
        assert quality._render_tier_was_spent(_rendered_shell()) is True
        # 同一份行程单 + 起不来的记号 = 那一档没真答过
        assert quality._render_tier_was_spent(
            _rendered_shell(escalation_path=f"http->stealthy:{MARKS[0]}")) is False
        assert quality._render_tier_was_spent(
            _shell(escalation_path="preflight:timeout(skipped_stealthy)")) is False


class TestSpentTierIsNoLongerRecommended:

    def test_a_rendered_shell_does_not_get_sent_back_to_the_same_tier(self):
        hint = _hint(_rendered_shell())
        assert "auto-escalates" not in hint and "Retry once with force_fetcher" not in hint, hint
        assert "ALREADY rendered" in hint, hint
        assert "extraction_type='html'" in hint, "拒绝要给一条还能做的事"

    def test_a_rendered_bot_wall_says_the_tier_is_not_the_untried_option(self):
        hint = _hint(_rendered_shell(error="bot_wall_detected: captcha on the body",
                                     page_type="captcha"))
        assert "Retry once with force_fetcher='stealthy'" not in hint, hint
        assert "no tier left to escalate to" in hint, hint

    def test_a_rendered_403_does_not_promise_the_render_again(self):
        hint = _hint(_rendered_shell(status=403, error="http_error_403",
                                     content=["Forbidden"], page_type="unknown"))
        assert "Retry once with force_fetcher='stealthy'" not in hint, hint
        assert "already made this request" in hint, hint

    def test_the_machine_readable_twin_agrees_with_the_sentence(self):
        """`next_action` 与 `diagnosis.recover_with` 是同一个决定的两个落点。

        表里那两句模板（"retry with force_fetcher='stealthy'"）写的是这一族的通用
        杠杆，它们不认识"这一次那根杠杆已经拉过"。两只手互相否决正是 D5 点名的形状。
        """
        for err, page in (("js_shell_detected: 48,000-byte body", "js_shell"),
                          ("bot_wall_detected: captcha", "captcha")):
            d = quality._diagnose(_rendered_shell(error=err, page_type=page))
            assert d and "force_fetcher='stealthy'" not in d["recover_with"], (err, d)
            assert "no untried tier is left" in d["recover_with"], (err, d)

    def test_retryable_is_not_collateral_of_a_reworded_hint(self):
        """建议改了口，`retryable` 不跟着翻 —— 墙撤不撤是站点的事。

        这一条是给"顺手把布尔也改了"留的桩：`retryable` 的唯一依据是这次调用还剩
        什么可动，而这里的"不能重试"曾经专属于"这台机器起不来浏览器"。
        """
        d = quality._diagnose(_rendered_shell())
        assert d["retryable"] is True, d


class TestUnspentTierStillGetsTheRetry:
    """rule 3 在这一族上的镜像：那一档没跑过时，原建议一个字不许变弱。"""

    def test_an_http_only_shell_still_hears_about_the_browser(self):
        hint = _hint(_shell(escalation_path="direct:http"))
        assert "re-fetch auto-escalates to the stealthy browser" in hint, hint

    def test_a_shell_the_probe_skipped_still_has_its_untried_tier(self):
        """预检跳掉两档 ≠ 档跑过 —— 强制那一档正是剩下的出路。"""
        hint = _hint(_shell(escalation_path="preflight:timeout(skipped_stealthy)"))
        assert "re-fetch auto-escalates to the stealthy browser" in hint, hint

    def test_a_403_from_the_http_tier_still_gets_the_retry(self):
        hint = _hint(_shell(status=403, error="http_error_403", content=["Forbidden"],
                            page_type="unknown", escalation_path="direct:http"))
        assert "Retry once with force_fetcher='stealthy'" in hint, hint

    def test_a_bot_wall_from_the_http_tier_still_gets_the_retry(self):
        hint = _hint(_shell(error="bot_wall_detected: captcha", page_type="captcha",
                            escalation_path="direct:http"))
        assert "Retry once with force_fetcher='stealthy'" in hint, hint
