"""test_quality.py —— 由 3 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
import inspect
import pytest
from dhole_mcp import params, quality, server as server_mod
from dhole_mcp.quality import _agent_hints, _browser_cannot_help
import socket
from dhole_mcp import feed as F
from dhole_mcp.quality import classify_error_text, row_diagnosis
from dhole_mcp.server import MasterFetchServer, ResponseModel
from types import SimpleNamespace
from dhole_mcp.quality import _is_over_budget


# ============================================================================
# 来源：test_advice_never_recommends_the_tier_that_just_failed.py
# ============================================================================
# 批次 3：建议不许推荐一件它刚证明做不到的事。

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


# ============================================================================
# 来源：test_a_dead_feed_names_the_dead_thing_not_python.py
# ============================================================================
# 一条读不到的 feed，说的必须是"读不到的那件事"，不是 Python 的内部话。
# 重名消歧：URL → URL__a_dead_feed_names_the_de_7c2d

URL__a_dead_feed_names_the_de_7c2d = "https://no-such-host-xyzq.invalid/feed"
# 逐字取自实测现场（判据认的就是这串，不要美化）
DNS_AS_TLS = ("error sending request for url "
              f"({URL__a_dead_feed_names_the_de_7c2d}) > tls handshake eof")
TLS_REAL = ("error sending request for url (https://a.example.com/feed) "
            "> invalid peer certificate: UnknownIssuer")


class _Raises:
    """假 HTTPSession：`get` 抛出现在 `_grab` 的 try 里，让 except 分支走真路。"""

    def __init__(self, text):
        self.text = text

    async def get(self, url, **kw):
        raise RuntimeError(self.text)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


def _run_grab(monkeypatch, text, resolves):
    """跑一次真的 `_grab` 失败路径，只把"名字解析得出吗"换成可控的桩。"""
    monkeypatch.setattr("dhole_mcp.fetcher.HTTPSession", lambda **kw: _Raises(text))
    if resolves:
        monkeypatch.setattr(socket, "getaddrinfo",
                            lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    else:
        def _fail(*a, **k):
            raise socket.gaierror(11001, "getaddrinfo failed")
        monkeypatch.setattr(socket, "getaddrinfo", _fail)
    return asyncio.run(F._grab(URL__a_dead_feed_names_the_de_7c2d, 5))[3]


# ─── 1 · 类名不再当答案 ───────────────────────────────────────────────────────

def test_the_exception_class_name_stops_leaking(monkeypatch):
    out = _run_grab(monkeypatch, DNS_AS_TLS, resolves=False)
    assert "RequestError" not in out and "RuntimeError" not in out, out
    assert out.startswith("network_error: "), out


# ─── 2 · 外衣改判，但真 TLS 不许被改跑 ───────────────────────────────────────

def test_a_name_that_does_not_resolve_is_called_dns(monkeypatch):
    out = _run_grab(monkeypatch, DNS_AS_TLS, resolves=False)
    assert "dns_failure" in out, out
    assert "tls" not in out.lower(), f"库的外衣还挂在回执上：{out}"
    assert "do NOT retry" in out, "dns 的出路要说清重发不会变"


def test_a_name_that_resolves_keeps_the_tls_verdict(monkeypatch):
    """反向：不许有人拿"失败都算 DNS"图省事。证书坏了就该说证书。"""
    out = _run_grab(monkeypatch, TLS_REAL, resolves=True)
    assert "tls_error" in out, out
    assert "dns" not in out.lower(), out


def test_a_refused_port_stays_refused(monkeypatch):
    out = _run_grab(monkeypatch,
                    "error sending request for url (https://a.example.com/feed) "
                    "> os error 10061", resolves=True)
    assert "connection_refused" in out, out


def test_the_name_check_never_opens_a_port(monkeypatch):
    """`getaddrinfo(host, None)` —— 端口那一格必须是空的。"""
    seen = {}

    def _spy(host, port, *a, **k):
        seen["host"], seen["port"] = host, port
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", _spy)
    assert asyncio.run(F._name_resolves("https://someone.example.com/feed")) is True
    assert seen["port"] is None, f"拨端口了：{seen}"
    assert seen["host"] == "someone.example.com"


def test_an_unreadable_host_keeps_the_original_verdict():
    """读不出主机名时不许改判 —— 那时"名字不存在"根本没有依据。"""
    assert asyncio.run(F._name_resolves("not-a-url")) is True


# ─── 3 · 行上有 diagnosis，且与信封读同一张表 ────────────────────────────────

def test_a_not_a_feed_row_gets_a_real_code():
    """`not a feed: …` 落到首词兜底时量出来的 code 是 "not" —— 兜底规则发明的词。"""
    diag = row_diagnosis("not a feed: StartTag: invalid element name, line 1, "
                         "column 2 (the response is an HTML page)")
    assert diag["code"] == "not_a_feed", diag
    assert diag["retryable"] is False
    assert "feed" in diag["recover_with"]


def test_a_successful_row_carries_no_diagnosis():
    """缺席即成功 —— 这一约定不许在第二条通道上变。"""
    assert row_diagnosis("") is None


def test_raw_library_text_becomes_a_real_code_not_error():
    """HTTP 档同款形状的串：code 不许是 "error"，布尔不许与出路相反。"""
    code, retryable, recover = classify_error_text(DNS_AS_TLS)
    assert code != "error", (code, retryable, recover)
    assert code in ("dns_failure", "tls_error", "timeout", "connection_reset",
                    "connection_refused", "unknown"), code


def test_row_and_envelope_agree_on_every_shape():
    """同一个 code 表不许有两个读者：`_diagnose` 与 `row_diagnosis` 必须同答。"""
    from dhole_mcp import quality as Q
    cases = ["network_error: dns_failure - x", DNS_AS_TLS, "not a feed: StartTag: …",
             "invalid_request: bad since", "HTTP 500", "all_tiers_failed: …",
             "browser_binary_missing: no chrome", "feed too large (9 bytes, cap 1)",
             "timeout: the 1500ms call budget ran out",
             # 状态码那一族换了一位作者之后，两只手仍要同答（R02-134 · F116）：
             # 404 不许多发一次、503 可以 —— 布尔不许在这一格各判一遍。
             "http_error_404: server returned error status",
             "http_error_503: server returned error status",
             "body_too_large: the feed document is 20 bytes, over the 10 cap",
             "invalid_request: since must be a date string (ISO-8601 or an RSS timestamp)"]
    # 只比"有 error 的串"：空 error 时 `_diagnose` 转去读 page_type / content_ok
    # （no_content、truncated 那一族），而行级判据手上只有那一串 —— 两边不该同答。
    for err in cases:
        from_env = Q._diagnose(ResponseModel(url="https://x.test/", status=0,
                                             content=[], error=err))
        from_row = row_diagnosis(err)
        assert (from_env or {}).get("code") == (from_row or {}).get("code"), (
            f"{err[:45]!r} 两条路径各认出一个 code："
            f"{(from_env or {}).get('code')} vs {(from_row or {}).get('code')}")
        assert (from_env or {}).get("retryable") == (from_row or {}).get("retryable"), (
            f"{err[:45]!r} 的 retryable 在两处相反")


# ─── 4 · 端到端：feed_read 的失败行与成功行 ──────────────────────────────────

def test_a_dead_feed_row_arrives_with_the_three_fields(monkeypatch):
    monkeypatch.setattr(F, "_grab", lambda *a, **k: asyncio.sleep(
        0, result=(b"", 0, "", "network_error: dns_failure - DNS resolution failed. "
                               "Verify the URL is correct; do NOT retry.", {})))
    (row,) = asyncio.run(MasterFetchServer().feed_read(urls=[URL__a_dead_feed_names_the_de_7c2d]))
    assert row["error"].startswith("network_error:")
    assert row["diagnosis"] == {"code": "network", "retryable": False,
                                "recover_with": row["diagnosis"]["recover_with"]}, \
        row["diagnosis"]
    assert row["items"] == []


def test_a_live_feed_row_still_carries_items_and_no_diagnosis(monkeypatch):
    """反向：结构化判据不许把成功行弄坏，也不许给成功行挂一格空的 diagnosis。"""
    body = (b"<?xml version='1.0'?><rss version='2.0'><channel><title>t</title>"
            b"<item><title>one</title><link>https://a.example.com/1</link>"
            b"<guid>g1</guid></item></channel></rss>")
    monkeypatch.setattr(F, "_grab",
                        lambda *a, **k: asyncio.sleep(0, result=(body, 200, "utf-8", "", {})))
    (row,) = asyncio.run(MasterFetchServer().feed_read(
        urls=["https://a.example.com/feed"]))
    assert row["error"] == "" and row["diagnosis"] is None
    assert len(row["items"]) == 1, "成功行被这次改动弄坏了"


def test_the_first_word_fallback_only_serves_real_codes():
    """首词兜底的本职：`nested_no_match: …` 这种自带 code 的串。

    它同时也会把一句英文的第一个词当 code（"not a feed" → "not"），那正是这一族要
    进表的原因 —— 兜底是给**已经按 code 写的**错误用的，不是给散文用的。
    """
    assert row_diagnosis("nested_no_match: nothing carried the shape")["code"] ==         "nested_no_match"
    assert row_diagnosis("") is None


def test_every_feed_failure_shape_names_a_code(monkeypatch):
    """三个产地（since 拒绝 / 抓取失败 / 解析失败）都要有判据，不是只补我量到那一处。"""
    ok_body = (b"<?xml version='1.0'?><rss><channel><title>t</title></channel></rss>")
    cases = {
        "since 被拒": dict(urls=[URL__a_dead_feed_names_the_de_7c2d], since="not-a-date"),
        "抓取失败": dict(urls=[URL__a_dead_feed_names_the_de_7c2d]),
        "不是 feed": dict(urls=[URL__a_dead_feed_names_the_de_7c2d]),
    }
    def _grab_factory(which):
        async def _g(url, timeout, headers=None):
            if which == "抓取失败":
                return b"", 0, "", ("network_error: connection_refused - target refused"), {}
            return ok_body, 200, "utf-8", "", {}
        return _g

    for which, kw in cases.items():
        monkeypatch.setattr(F, "_grab", _grab_factory(which))
        (row,) = asyncio.run(MasterFetchServer().feed_read(**kw))
        if row["error"]:
            assert row["diagnosis"] and row["diagnosis"]["code"] not in ("", "not",
                                                                        "error"), (
                f"{which}：行上的 code 是兜底发明的词 —— {row['diagnosis']}")


# ─── 5 · 站点**答过**的状态码只许有一位句子作者（R02-134 · F116）─────────────
#
# 现场（`review/.runtime/p1/r282_same_404_across_doors.json` · 本机靶站 · 零外网）：同一
# 件事实「origin 回了 404」有两位句子作者 —— 在册的那位写 `http_error_404: server returned
# error status`（`quality._detect_content_issue`，`web_read` 的信封与 `web_crawl` 的行都从
# 它取词），第二位是 `feed._grab` 的裸 `HTTP 404`。裸的那一句既不在三张表里也不带自记号，
# 于是 `row_diagnosis` 认不出、那一行**整格没有把手**，而这扇门的 `next_action` 正指着
# 「逐行的 diagnosis」。r284 又量出同族两支：`feed too large (…)` 与 `since …` 两句由
# feed 自家写、code 却由首词兜底发明（`feed` / `since`）—— 那是同一件事的第二种坏法。
#
# 边界（不许顺手做的两件事）：
#   · 不许把裸的 `HTTP 404` 塞进 `_DIAGNOSIS_PREFIXES`（那是给第二位作者补户口，从此一件
#     事实两个名字）；要改的是**产地**，让它问那位在册作者；
#   · `retryable` 不许在这一格另判一遍：那颗数说什么，与同一封回执 `next_action` 说的
#     「Retry once after a pause」（5xx）/「wait before retrying」（429）必须是同一句话，
#     否则两只手又互相否决（D5）。404 那一支照旧 false —— 站方说「没有这个页面」，重发
#     同一发不会变。

class _Answers:
    """假 HTTPSession：`get` 交回一个**答过的**状态码（不是异常，也没有正文）。"""

    def __init__(self, status, body=b""):
        self.status, self.body = status, body

    async def get(self, url, **kw):
        return _Answered(self.status, self.body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


class _Answered:
    status_code = 0
    body = b""
    headers = None
    encoding = "utf-8"

    def __init__(self, status, body):
        self.status_code, self.body, self.encoding = status, body, "utf-8"
        self.headers = {}


def _grab_answer(monkeypatch, status, body=b"", cap=None):
    """跑一次真的 `_grab`（只有 session 是桩），把上线那一格的原句读回来。"""
    monkeypatch.setattr("dhole_mcp.fetcher.HTTPSession", lambda **kw: _Answers(status, body))
    if cap is not None:
        monkeypatch.setattr(F, "_MAX_FEED_BYTES", cap)
    return asyncio.run(F._grab(URL__a_dead_feed_names_the_de_7c2d, 5))


def test_an_answered_status_is_named_by_the_registered_author(monkeypatch):
    """feed 的行不许自己造一句「HTTP 404」：它要问那位在册作者，取到同一串。"""
    from dhole_mcp.quality import status_error_sentence

    err = _grab_answer(monkeypatch, 404)[3]
    assert err == status_error_sentence(404), err
    assert err != "HTTP 404", "第二位作者还在写裸句子"
    diag = row_diagnosis(err)
    assert diag and diag["code"] == "http_error_404", diag
    assert diag["retryable"] is False, diag


def test_the_same_answered_status_is_named_once_in_every_channel(monkeypatch):
    """同一颗 404 在 feed 行 / web_read 信封 / 爬行的行上是同一个名字（三处都读作者）。"""
    from dhole_mcp import quality as Q
    from dhole_mcp.models import ResponseModel

    feed_err = _grab_answer(monkeypatch, 404)[3]
    web_read_err = Q._detect_content_issue(
        ResponseModel(url="https://origin.test/page", status=404, content=[], error=""))
    assert feed_err == web_read_err, (feed_err[:70], web_read_err[:70])
    assert row_diagnosis(feed_err)["code"] == row_diagnosis(web_read_err)["code"]


@pytest.mark.parametrize("status", [500, 502, 503, 408, 429])
def test_a_status_that_invites_a_retry_says_so_in_both_hands(monkeypatch, status):
    """布尔不许比建议保守：同一封回执里 `next_action` 让人等一会儿再发，`retryable` 就是真。"""
    from dhole_mcp import quality as Q
    from dhole_mcp.models import ResponseModel

    err = _grab_answer(monkeypatch, status)[3]
    diag = row_diagnosis(err)
    assert diag and diag["code"] == f"http_error_{status}", diag
    assert diag["retryable"] is True, f"{status} 说「重发不会变」：{diag}"
    assert diag["recover_with"], f"说要重发却不指杠杆：{diag}"
    advice = Q._agent_hints(ResponseModel(url="https://origin.test/page", status=status,
                                         content=[], error=err))[1]
    assert "retry" in advice.lower(), f"两只手对不上：{advice[:120]} vs {diag}"


@pytest.mark.parametrize("status", [404, 410, 451])
def test_a_permanent_answer_is_not_invited_into_a_second_try(monkeypatch, status):
    """反向锁：不许有人为了让 5xx 说真话而把这族一并翻成「可重试」。"""
    err = _grab_answer(monkeypatch, status)[3]
    diag = row_diagnosis(err)
    assert diag and diag["code"] == f"http_error_{status}", diag
    assert diag["retryable"] is False, f"{status} 的把手在劝人重发同一发：{diag}"


def test_a_feed_bigger_than_its_own_cap_is_named_by_a_token(monkeypatch):
    """`feed too large (…)` 的 code 从前是兜底按第一个词造的（`feed`）—— 名字要住在作者侧。"""
    err = _grab_answer(monkeypatch, 200, body=b"x" * 40, cap=10)[3]
    assert err.startswith("body_too_large:"), err
    assert not err.startswith("feed too large"), err
    diag = row_diagnosis(err)
    assert diag["code"] == "body_too_large", diag
    assert diag["retryable"] is False, diag


def test_a_rejected_since_is_named_as_the_parameter_refusal_it_is():
    """`since` 读不出日期是**参数**拒绝：它的名字与该门别面的参数拒绝同一颗，不是 `since`。"""
    row = asyncio.run(F.fetch_feed(URL__a_dead_feed_names_the_de_7c2d, since="not-a-date"))
    assert row.error.startswith("invalid_request: "), row.error
    assert row.diagnosis["code"] == "invalid_request", row.diagnosis
    again = asyncio.run(F.fetch_feed(URL__a_dead_feed_names_the_de_7c2d, since=12345))
    assert again.diagnosis["code"] == "invalid_request", again.diagnosis


# ============================================================================
# 来源：test_the_backstop_speaks_for_the_tier_it_cancelled.py
# ============================================================================
# R01-010（并入 R01-014）· 掐掉别人的那一层不许替别人出主意。
# 重名消歧：URL → URL__the_backstop_speaks_for__adbb

URL__the_backstop_speaks_for__adbb = "https://docs.example.com/manual.pdf"
SIZE_BYTES = 163_840
SIZE_HUMAN = "160.0KB"          # `_human_bytes` 印出来的样子
CAP_S = server_mod._HTTP_ATTEMPT_CAP_MS // 1000

_TIER_BASE = dict(
    extraction_type="markdown", css_selector=None, cache_ttl=0, offset=0,
    headless=True, real_chrome=False, proxy=None, solve_cloudflare=False,
    block_webrtc=False, hide_canvas=False, extra_headers=None,
    useragent=None, cookies=None,
)


# ─── 1 · 那一条缝：层学到的事实必须能到达背板的那句话 ─────────────────

def _srv_with_a_hanging_download(monkeypatch, *, size=SIZE_BYTES, size_note=""):
    """真的升级阶梯 + 假网络：尺寸预检定在下载之前，下载那一发永远不回来。

    `_document_size` 摘的是模块名（`_auto_escalate` 在函数体里按名字取它），摘错地方
    这条用例就会去拨真网络；`get` 挂死则是靶站上那 40s 下载的等价物。
    """

    async def _size(url, proxy, headers, cookies, useragent, budget_s=3.0):
        return size, size_note

    async def _hang(*a, **k):
        await asyncio.sleep(30)

    async def _no_session(**kw):
        return "auto-under-test"

    async def _installed(real_chrome=False):
        return SimpleNamespace(ok=True, detail="", waited_s=0.0)

    monkeypatch.setattr(server_mod, "_document_size", _size)
    monkeypatch.setattr(server_mod, "ensure_chromium_async", _installed)
    monkeypatch.setattr(server_mod, "_browser_deps_available", lambda: True)
    srv = server_mod.MasterFetchServer(cache_ttl=0)
    srv.get = _hang
    srv.stealthy_fetch = _hang
    srv._ensure_auto_session = _no_session
    return srv


async def _cut_midway(srv, *, backstop_ms=1500, tier_ms=5000):
    """让背板赢下这场竞速 —— 真实缺陷里它本来就赢（这是设计，见其 docstring）。

    阶梯自己那份预算给得比背板宽，谁赢就不再取决于毫秒级的调度顺序；被测的是
    **背板那封响应有没有拿到阶梯登进去的事实**。
    """
    return await srv._within_call_budget(
        srv._auto_escalate(URL__the_backstop_speaks_for__adbb, timeout=tier_ms, **_TIER_BASE),
        URL__the_backstop_speaks_for__adbb, backstop_ms, "fetch tiers")


@pytest.mark.asyncio
async def test_the_size_measured_before_the_download_survives_the_cut(monkeypatch):
    """R01-014 那一半：尺寸在失败**之前**就该在回执里，因为它早就量好了。"""
    srv = _srv_with_a_hanging_download(monkeypatch)
    out = await _cut_midway(srv)

    assert SIZE_HUMAN in out.next_action, out.next_action
    assert "document download" in out.next_action, out.next_action


@pytest.mark.asyncio
async def test_the_cut_stays_the_terminal_answer_it_has_always_been(monkeypatch):
    """反向守卫：换建议不许动那条机器可读的边 —— 每一档都靠 "timeout: the " 收尾。"""
    srv = _srv_with_a_hanging_download(monkeypatch)
    out = await _cut_midway(srv)

    assert out.error.startswith("timeout: the "), out.error
    assert _is_over_budget(out)
    assert out.status == 0 and out.content == [], out


@pytest.mark.asyncio
async def test_the_twin_and_the_prose_recommend_the_same_move(monkeypatch):
    """两只手不许各说一套：信封里那颗也得带着"一次尝试钳 30s"这颗事实。

    修前这里读到的是通用前缀表给的 `recover_with="raise timeout, or switch source"`，
    而同一封的 next_action 说的是另一件事 —— R01-044 归的那条根因在建议位的形状。
    """
    srv = _srv_with_a_hanging_download(monkeypatch)
    out = await _cut_midway(srv)

    lever = out.diagnosis["recover_with"]
    assert f"caps at {CAP_S}s" in lever, lever
    assert lever in out.next_action, (lever, out.next_action)
    assert out.diagnosis["code"] == "timeout"
    assert out.diagnosis["retryable"] is False, out.diagnosis


@pytest.mark.asyncio
async def test_a_download_whose_size_the_host_never_told_still_says_so(monkeypatch):
    """量不到尺寸是一个事实，不是空白：那句 note 必须原样上回执。"""
    srv = _srv_with_a_hanging_download(monkeypatch, size=None,
                                       size_note="host reported no size (chunked or range refused)")
    out = await _cut_midway(srv)

    assert "document download" in out.next_action, out.next_action
    assert "host reported no size" in out.next_action, out.next_action


@pytest.mark.asyncio
async def test_a_call_that_never_touched_a_document_keeps_the_generic_lever(monkeypatch):
    """反向守卫：HTML 那一发抬 `timeout=` 是真有用的，这刀不删一句有用的话。"""
    async def _html(*a, **k):
        await asyncio.sleep(30)

    srv = _srv_with_a_hanging_download(monkeypatch)
    srv.get = _html
    html_url = "https://news.example.com/story"
    out = await srv._within_call_budget(
        srv._auto_escalate(html_url, timeout=5000, **_TIER_BASE),
        html_url, 1500, "fetch tiers")

    assert "document" not in out.next_action, out.next_action
    assert "Raise timeout" in out.next_action, out.next_action


@pytest.mark.asyncio
async def test_each_concurrent_call_carries_only_its_own_facts(monkeypatch):
    """账本按调用作用域走：一份 PDF 的结论不许串到同一时刻的另一发 HTML 上。"""
    srv = _srv_with_a_hanging_download(monkeypatch)
    html_url = "https://news.example.com/story"

    async def _hang_html(*a, **k):
        await asyncio.sleep(30)

    doc = srv._within_call_budget(
        srv._auto_escalate(URL__the_backstop_speaks_for__adbb, timeout=5000, **_TIER_BASE), URL__the_backstop_speaks_for__adbb, 1500, "fetch tiers")
    # 同一 TaskGroup 之外并发两发：两把 `_within_call_budget` 各有各的账本
    srv2 = _srv_with_a_hanging_download(monkeypatch)
    srv2.get = _hang_html
    page = srv2._within_call_budget(
        srv2._auto_escalate(html_url, timeout=5000, **_TIER_BASE),
        html_url, 1500, "fetch tiers")
    doc_out, page_out = await asyncio.gather(doc, page)

    assert SIZE_HUMAN in doc_out.next_action, doc_out.next_action
    assert "document" not in page_out.next_action, page_out.next_action


# ─── 2 · 同一根因的第二条出口：那一层自己答的那一发 ──────────────────

# 现场的第二发（`sessions/P02-s2c-pdf-retry.json`）：这一次没有被背板掐，是 HTTP 档
# 自己拿着一次 status=0 的网络错误回来，`next_action` 换成了专项那句（"a bigger
# timeout= buys parsing room"）—— 而信封里那只手原封不动还是通用前缀表的
# "Retry once with a longer timeout"。真 CLI 复验时又量到一次（见 fix 文档）：两封的
# prose 都对了，`diagnosis.recover_with` 还在劝人抬那颗拧不动的旋钮。
_TRANSPORT_TIMEOUT = ("error decoding response body > request or response body error"
                      " > operation timed out")


async def _tier_answers_a_dead_download(monkeypatch, *, timeout_ms=30_000):
    """阶梯自己收尾的那一发：尺寸预检定到了，下载那一发以 status=0 的网络错误回来。"""

    async def _size(url, proxy, headers, cookies, useragent, budget_s=3.0):
        return SIZE_BYTES, ""

    async def _dead(*a, **k):
        return ResponseModel(url=URL__the_backstop_speaks_for__adbb, status=0, content=[], error=_TRANSPORT_TIMEOUT,
                             content_type="application/pdf", fetcher_used="http")

    monkeypatch.setattr(server_mod, "_document_size", _size)
    srv = server_mod.MasterFetchServer(cache_ttl=0)
    srv.get = _dead
    return await srv._auto_escalate(URL__the_backstop_speaks_for__adbb, timeout=timeout_ms, **_TIER_BASE)


@pytest.mark.asyncio
async def test_the_tier_that_answered_its_own_timeout_agrees_with_its_twin(monkeypatch):
    srv = await _tier_answers_a_dead_download(monkeypatch)

    assert SIZE_HUMAN in srv.next_action, srv.next_action
    assert "raise timeout" not in srv.next_action.lower(), srv.next_action
    # 这一条是复验量出来的：prose 早就修对了，孪生还在替通用表说话
    assert "longer timeout" not in srv.diagnosis["recover_with"].lower(), srv.diagnosis
    assert f"{CAP_S}s" in srv.diagnosis["recover_with"], srv.diagnosis
    assert srv.diagnosis["retryable"] is False, srv.diagnosis
    assert srv.diagnosis["recover_with"] in srv.next_action, (srv.diagnosis, srv.next_action)


@pytest.mark.asyncio
async def test_a_document_under_the_cap_still_hears_the_lever_that_works(monkeypatch):
    """反向守卫：12s 预算那一发，抬到 30s 是真有效的 —— 两只手都要说出来，不是都闭嘴。"""
    srv = await _tier_answers_a_dead_download(monkeypatch, timeout_ms=12_000)

    assert "raise timeout" in srv.next_action.lower(), srv.next_action
    assert f"caps at {CAP_S}s" in srv.next_action, srv.next_action
    assert srv.diagnosis["recover_with"] in srv.next_action, (srv.diagnosis, srv.next_action)


# ─── 3 · 那句杠杆：一个作者，两个分支都要说得出 ──────────────────────

def test_a_document_already_at_the_attempt_cap_is_not_told_to_raise_timeout():
    """现场那一发的形状（预算=30000=钳顶）：这封响应里不许再出现"抬抬"。

    背板那发在单元里等 31.5s 才回来，所以这一条按**同一个作者**量：`_cancelled_tier_advice`
    就是背板在 TimeoutError 分支里调的那一个函数，靶站复验再量整条线。
    """
    facts = server_mod._TierFacts()
    facts.mark_document(SIZE_BYTES, "")
    prose, twin = server_mod._cancelled_tier_advice(URL__the_backstop_speaks_for__adbb, facts, 30_000, "fetch tiers")

    assert "raise timeout" not in prose.lower(), prose
    assert f"{CAP_S}s" in prose, prose
    assert "switch source" in prose and "parse" in prose, prose
    # 孪生引用的就是散文里那一句杠杆 —— 一个作者，两只手
    assert twin["recover_with"] in prose, (twin, prose)
    assert twin["retryable"] is False


def test_a_document_under_the_cap_is_still_told_the_lever_works():
    """另一半：12s 预算里抬上去是真有效的 —— 不许把这刀做成"永远劝人换源"。"""
    facts = server_mod._TierFacts()
    facts.mark_document(SIZE_BYTES, "")
    prose, twin = server_mod._cancelled_tier_advice(URL__the_backstop_speaks_for__adbb, facts, 12_000, "fetch tiers")

    assert "raise timeout" in prose.lower(), prose
    assert f"caps at {CAP_S}s" in prose, prose
    assert twin["recover_with"] in prose, (twin, prose)


def test_a_non_document_call_asks_for_no_advice_at_all():
    """背板的兜底不许变成第二个建议作者：没登记过事实就交白卷，通用那句照旧。"""
    prose, twin = server_mod._cancelled_tier_advice(
        "https://news.example.com/story", server_mod._TierFacts(), 30_000, "fetch tiers")
    assert prose == "" and twin is None


# ─── 4 · 建议的形状：句首不许是小写 ─────────────────────────────────

def test_an_advice_that_opens_a_field_does_not_open_with_a_lower_case_word():
    """建议是由"分句"拼出来的，而分句从前是为 "; " 之后写的 —— 搬到句首就成了小写句首。

    真 CLI 复验量到的两封（`r010-verify.json`）：

    ```
    Budget exhausted after 32033ms at the fetch tiers. the fetch tiers on a document
    download; size probe failed (TimeoutError); …
    the HTTP tier on a document download; this host has taken 33s …   ← next_action 的第一个词
    ```

    后者在这一刀之前就在 wire 上（P02-s2c 那一发的 `next_action` 正是以小写的 "the" 开
    头），前者是背板这条新接上的路把它带到 head 之后。句号之后一个小写词，读者会当成
    上一句没写完 —— 与本刀同一族的文案账，一起收。
    """
    from dhole_mcp.quality import _over_budget_result

    facts = server_mod._TierFacts()
    facts.mark_document(SIZE_BYTES, "")
    for budget in (30_000, 12_000):
        prose, _twin = server_mod._cancelled_tier_advice(URL__the_backstop_speaks_for__adbb, facts, budget, "fetch tiers")
        assert prose[:1].isupper(), prose
        out = _over_budget_result(URL__the_backstop_speaks_for__adbb, budget, budget + 2033, "fetch tiers", "", prose)
        # head 之后那一句的句首 —— 不按 ". " 切句子，建议里有 "e.g." 那样的缩写，
        # 切出来的"句子"不是句子（这条守卫量的是字段边界，不是全文分句）。
        head_end = out.next_action.index(". ") + 2
        assert out.next_action[head_end:head_end + 1].isupper(), out.next_action


def test_the_generic_advice_opens_its_field_with_a_capital_too():
    """通用那一句的"已经到顶"分支同样是分句搬句首 —— 同一个规矩，不留第二个例外。"""
    from dhole_mcp.quality import _budget_advice
    from dhole_mcp.security import TIMEOUT_MAX_MS

    advice = _budget_advice("fetch tiers", "", TIMEOUT_MAX_MS)
    assert "nothing left to raise" in advice, advice
    assert advice[:1].isupper(), advice
