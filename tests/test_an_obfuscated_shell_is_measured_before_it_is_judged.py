r"""R1 第一刀（measure）：混淆壳页先被**量到**，结论位一个字不动。

实测（2026-10-09，北极星那家）：`status: 200`、`content_ok: true`，正文 3,559 个字符
—— 全抽到了，抽取是忠实的，而那几千个字里没有一句人话：它是一枚打包过的 JS 挑战。
批量读五个链接时它计入 `successful: 2`。这不是"缺一个检测器"，是**长度这把尺量不到
形状**：正文长过每一道闸，于是每一道闸都放行。

为什么分两笔提交（这附近被咬过两次，README 里 D-38/D-41 那条血脉）：`_is_js_shell`
旁边动第二把尺，风险不是"漏判"而是"过度开火" —— 图集、仪表盘、changelog、表格、
日语法语的短词正文、以及**任何把数据当正文的 JSON**都会开始被叫成壳。所以第一笔只
把判定当**上报字段**发出去（`metadata.obfuscated_body`），在语料上把每一发命中留下
名字；假阳性为零，第二笔才让它翻 `content_ok`（那一笔会同时重基 `tests/mcp_snapshot.py`
的 isError 计数，并且要读那个 diff）。

判据的两条腿（缺一条就不算，这是这一批的核心论点：单信号就翻结论位的闸一定吃掉合法
页面）：
  ① 结构证据 —— 长 hex/base64 串、`_0x`/`arg1=`/`getRender`/`String.fromCharCode`
     这类混淆器与 WAF 留下的字面标记、连续 `\xNN` 转义；
  ② 词形合法度低 —— token 平均很长，或"长 token + 大小写混排"两样都占。
一篇讲 base64 的教程有 ①，它的正文过不了 ②；一段没有标记的长表格有 ②，它没有 ①。

本文件里有一条**故意写下今天的错**：`test_measure_arm_moves_no_verdict` 断言混淆载荷
现在仍然是 `content_ok=true`。第二笔提交要把那句断言翻过来 —— 那不是测试坏了，那是
那一刀唯一的可观察变化，所以它必须先在这里被写下来、被命名。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import (
    _obfuscation_gate_applies, looks_obfuscated,
)
from dhole_mcp.server import _attach_dom_visible_chars, _with_agent_hints
from dhole_mcp.trafilatura_extractor import extract_content_from_html

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "evals"))

import fixture_site as fs  # noqa: E402

URL = "https://public-flaky.test/trace"

# 真语料：三份入库页面（GNU 长正文、quotes 首页、books.toscrape 的 travel 列表）。
# 这一族页面正是历史上被误伤过的形状 —— 豁免表如果只做"看起来像代码就不报"，
# 第一个红的是它们。
CORPUS = ("gnu_free_sw_definition.html", "quotes_index.html", "travel_2.html")


def _markdown(name: str) -> str:
    html = (ROOT / "page_fixtures" / name).read_text(encoding="utf-8",
                                                     errors="replace")
    return extract_content_from_html(html, f"https://corpus.test/{name}") or ""


def _http_page(html: str, *, fetcher: str = "http", status: int = 200,
               content_type: str = "text/html; charset=utf-8",
               content: list | None = None, **updates) -> ResponseModel:
    """按真抓取后的信封形状搭一发：正文走公共抽取入口，DOM 可见字符由
    `_translate_response` 那一处量好（绕过量尺就是测一个产品里不存在的形状）。"""
    md = content if content is not None else [
        extract_content_from_html(html, URL) or ""]
    out = ResponseModel(status=status, content=list(md), url=URL,
                        fetcher_used=fetcher, content_type=content_type,
                        total_size_bytes=max(len(html.encode("utf-8")), 3_559),
                        **updates)
    return _attach_dom_visible_chars(out, html)


def the_target(**kw) -> ResponseModel:
    """R1 关心的那一发：HTTP 档、body 够大、抽取没塌、正文全是打包过的脚本。"""
    return _http_page(fs._obfuscated_shell_page().decode(), **kw)


class TestTheDetectorReadsShapeNotLength:

    def test_the_obfuscated_target_is_named_by_its_own_evidence(self):
        text = extract_content_from_html(fs._obfuscated_shell_page().decode(), URL)
        assert len(text) > 2_000, (
            f"这枚靶穿过的是每一道长度闸，不是恰好没过 200 —— 只有 {len(text)} 字"
            "就说明靶退回了 /shell 那一形")
        finding = looks_obfuscated(text)
        assert finding, text[:300]
        assert any("hex" in s for s in finding["signals"]), finding
        assert any("arg1=" in s for s in finding["signals"]), finding

    def test_it_is_deterministic(self):
        """靶站无状态：同一份字节每次派生同一个载荷，否则基线量的是抖动。"""
        first = fs._obfuscated_shell_page()
        assert first == fs._obfuscated_shell_page()

    def test_prose_with_base64_in_it_is_not_a_shell(self):
        """① 单独成立时不许报 —— 一篇讲编码的教程就是这一形。"""
        # 真 base64（大小写 + 数字都齐），所以结构证据成立；正文的词形是正常英文散文。
        blob = ("aGVsbG9Xb3JsZDEyMzQ1Njc4OTBBQkpDREVGSEdKS0xN" * 2)
        prose = ("The base64 alphabet covers A through Z, both cases, the digits, and "
                 "two punctuation characters; a tutorial quotes one run of padding "
                 "characters exactly once while explaining the format. Readers of this "
                 "article are shown a long token so the description of the encoding "
                 "table makes literal sense here, and the rest of the article keeps "
                 "discussing why padding exists at all, in ordinary English words. "
                 * 3)
        assert looks_obfuscated(prose + blob) == {}, "教程被判成了壳"

    def test_long_words_alone_are_not_a_shell(self):
        """② 单独成立时不许报：反爬之外还有整页是超长短语的语言与术语表。"""
        tokens = " ".join(
            "internationalization" + "s" * i for i in range(60))
        assert looks_obfuscated(tokens) == {}

    def test_an_all_lowercase_run_is_not_a_coded_block(self):
        """一跑长字母还**不是**编码块：base64 的字母表里数字与大小写都齐，人起的标识符
        名没有。这一条钉的是 `_encoded_runs` 那三类字符的要求 —— 摘掉它，一份全是
        `SomeLongCamelCaseIdentifier` 的 CSS 类名表或 changelog 就会被叫成壳，而它的
        词形恰好把 ② 也喂饱了（长、大小写混排）。
        """
        ident = "SomeLongCamelCaseIdentifierNameWithoutAnyDigitsHere"
        page = (ident + " ; ") * 60
        assert looks_obfuscated(page) == {}, looks_obfuscated(page)

    def test_short_input_claims_nothing(self):
        assert looks_obfuscated("arg1=0123456789abcdef0123456789abcdef") == {}

    def test_the_sidecar_corpus_is_clean(self):
        """正向锁：三份真页面一个都不报。这是 measure 那一刀要看的数。"""
        hits = {name: looks_obfuscated(_markdown(name)) for name in CORPUS}
        assert all(not v for v in hits.values()), {
            k: v for k, v in hits.items() if v}

    def test_every_legitimate_target_site_page_is_clean(self):
        """靶站每一页（除那枚故意混淆的）都不许报。

        这条不是形式主义：measure 那一刀唯一的产出就是"命中清单"，而清单上只要出现
        一个非靶页的名字，第二笔就不能翻 `content_ok`。写下来而不是靠人跑一遍脚本，
        是因为下一个改阈值的人会先跑测试。
        """
        pages = {}
        for name in dir(fs):
            value = getattr(fs, name)
            if name == "_obfuscated_shell_page" or not isinstance(value, bytes):
                continue
            pages[name] = value
        assert len(pages) >= 6, sorted(pages)
        for name, html in pages.items():
            md = extract_content_from_html(html.decode("utf-8", "replace"),
                                           "https://corpus.test/") or ""
            assert not looks_obfuscated(md), (name, len(md), md[:160])

    def test_a_json_api_body_is_not_reported_at_the_gate(self):
        """类型豁免走的是闸门，不是检测器：数据文档里的长串就是数据本身。"""
        payload = json.dumps({"items": [{"id": "0f1e2d3c4b5a6978" * 4,
                                         "sig": "QUFBQUFB" * 12}
                                        for _ in range(40)]})
        out = _http_page("<html><body>x</body></html>",
                         content=[payload], content_type="application/json")
        assert not _obfuscation_gate_applies(out)


class TestTheExemptionsComeBeforeTheVerdict:
    """`_obfuscation_gate_applies` 的四道前置：每一道都对应一次真误伤。"""

    def _obfuscated(self, **kw):
        return the_target(**kw)

    def test_the_target_page_is_carrying_the_payload(self):
        """先确认靶没脱靶：这一发的正文确实长得像混淆载荷。"""
        assert looks_obfuscated("\n".join(self._obfuscated().content))

    def test_the_stealthy_tier_is_never_called_a_shell(self):
        """浏览器档的低文字页是合法页面（图集、canvas）—— `_JS_SHELL` 同一条教训。"""
        assert not _obfuscation_gate_applies(self._obfuscated(fetcher="stealthy"))

    def test_a_data_document_is_exempt_before_the_detector_runs(self):
        assert not _obfuscation_gate_applies(
            self._obfuscated(content_type="application/json"))

    def test_a_small_body_is_not_enough_evidence(self):
        out = self._obfuscated()
        out.total_size_bytes = 1_200
        assert not _obfuscation_gate_applies(out)

    def test_a_collapsed_extraction_is_a_different_fact(self):
        """DOM 里有字而抽取没交回 = 抽取器的问题，那条路已经有自己的 token。"""
        out = self._obfuscated(content=["arg1=" + "0f" * 800])
        out = _attach_dom_visible_chars(out, "<html><body>" + "字 " * 40_000
                                        + "</body></html>")
        assert not _obfuscation_gate_applies(out), "塌陷被抢着报成了混淆"

    def test_a_failure_that_already_has_a_name_is_not_double_labelled(self):
        out = self._obfuscated(error="js_shell_detected: 48,000-byte body, 120 chars")
        assert not _obfuscation_gate_applies(out)


class TestMeasureArmMovesNoVerdict:
    """这一笔提交唯一的可观察变化是那颗字段。"""

    def test_measure_arm_moves_no_verdict(self):
        out = _with_agent_hints(the_target())
        assert out.content_ok is True, (
            "content_ok 在第二笔之前就翻了 —— 那一笔的编号写在提交信息里，不在这")
        assert "obfuscated_body" in (out.metadata or {}), out.metadata
        assert out.summary and "obfuscated" not in out.summary.lower(), out.summary

    def test_the_findings_are_the_same_numbers_the_detector_returned(self):
        finding = looks_obfuscated("\n".join(the_target().content))
        out = _with_agent_hints(the_target())
        assert out.metadata["obfuscated_body"] == finding

    def test_a_clean_page_gets_no_new_metadata_key(self):
        out = _with_agent_hints(_http_page(
            (ROOT / "page_fixtures" / "gnu_free_sw_definition.html")
            .read_text(encoding="utf-8", errors="replace")))
        assert "obfuscated_body" not in (out.metadata or {}), out.metadata
