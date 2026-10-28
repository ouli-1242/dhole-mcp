"""test_server_6.py —— 由 5 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
import pathlib
from dhole_mcp import charset as charset_mod
from dhole_mcp.server import MasterFetchServer
import inspect
import json
import re
import pytest
from dhole_mcp import security as security_mod
from dhole_mcp import server as server_mod
from dhole_mcp import fetcher as FT
from dhole_mcp import search as search_mod
from dhole_mcp.server import MasterFetchServer as Server
import os
import subprocess
import sys
import shutil
import tempfile
from dhole_mcp import paths as paths_mod


# ============================================================================
# 来源：test_charset_honesty.py
# ============================================================================
# 批次 6e：一次猜出来的编码不许长得像一次测量。

# The two samples from the 2026-10-05 measurement, plus a control that decodes
# correctly. What GB18030 makes of the two legacy CJK streams is DERIVED here
# (encode in the real charset, read strictly as gb18030) rather than pasted as a
# block of mojibake: the derivation is the boundary itself, and it cannot rot.
JA_TEXT = "名前,都市\n東京,日本\n"
KO_TEXT = "한국어 텍스트입니다\n이것은 테스트입니다\n"
ZH_TEXT = "姓名,年龄\n张三,30\n"

JA_AS_GB18030 = JA_TEXT.encode("shift_jis").decode("gb18030")
KO_AS_GB18030 = KO_TEXT.encode("euc-kr").decode("gb18030")


def _parse(tmp_path, name, payload: bytes, **kwargs):
    path = tmp_path / name
    path.write_bytes(payload)
    return asyncio.run(MasterFetchServer().doc_parse(file_path=str(path), **kwargs))


# ─── 1 · "猜的"与"说得出是谁猜的"之间的那条线 ───


class TestTheGuessIsNamedOnlyWhereItIsTrue:
    def test_strict_gb18030_with_no_declaration_is_a_guess(self):
        assert charset_mod.encoding_basis(
            ZH_TEXT.encode("gbk"), "", "gb18030"
        ) == "guessed"

    def test_a_declared_charset_is_a_verdict_not_a_guess(self):
        """Passing encoding= is the caller taking the reading. Saying "guessed"
        there would make the remedy look like the thing still in doubt.

        2026-10-14（R02-021）改过一次输入，没改这句意图：这一族名字与赢家的**配对**必须
        是解码器真能产出的那一族。原来这格喂的是 (`declared='gbk'`, `used='gb18030'`) ——
        gbk 明明解得开这份 gbk 字节，`decode_file_bytes` 就会直接返回 gbk，这对名字在线
        上凑不出来。现在按线上样子写：点中了是 verdict，点不开而由检测顶上是 fallback
        （后者正是那一发被静默掉的那一支，另加一条测试钉住）。
        """
        for declared in ("gbk", "GBK", " gbk "):
            assert (
                charset_mod.encoding_basis(ZH_TEXT.encode("gbk"), declared, "gbk")
                == "declared"
            )
        body = KO_TEXT.encode("euc-kr")
        assert charset_mod.encoding_basis(body, "shift_jis", "gb18030") == "fallback"

    def test_a_bom_or_a_utf8_read_states_itself(self):
        body = ZH_TEXT.encode("utf-16")
        assert charset_mod.encoding_basis(body, "", "utf-16") == ""
        assert charset_mod.encoding_basis(
            ZH_TEXT.encode("utf-8-sig"), "", "utf-8-sig"
        ) == ""

    def test_a_utf16_body_with_a_stripped_bom_is_not_called_a_guess(self):
        """The NUL head is the case where gb18030 "succeeds" on someone else's
        bytes; charset.py already handles it, and the flag would be redundant
        next to a decoder that does not need one."""
        assert (
            charset_mod.encoding_basis(
                ZH_TEXT.encode("utf-16-le"), "", "gb18030"
            )
            == ""
        )

    def test_the_helper_is_the_only_place_that_decides(self):
        """One sentence, one owner: charset decides, parse ships the flag, server
        names the metadata keys, quality says the sentence. A second module
        computing "was this gb18030 / did the named charset run?" is a second
        opinion that will drift."""
        src = pathlib.Path(charset_mod.__file__).parent
        read = {p.name: p.read_text("utf-8") for p in src.glob("*.py")}
        deciders = sorted(name for name, text in read.items()
                          if "def encoding_basis" in text)
        assert deciders == ["charset.py"], deciders
        carriers = sorted(name for name, text in read.items()
                          if name != "charset.py" and "encoding_basis" in text)
        assert carriers == ["parse.py", "quality.py", "server.py"], carriers
        # 「你点的那一颗名字」也只许有一个作者：装配在 parse，落键在 server，说话在 quality
        asked = sorted(name for name, text in read.items()
                       if "encoding_asked" in text)
        assert asked == ["parse.py", "quality.py", "server.py"], asked


# ─── 2 · 乱码不再顶着绿灯 ───


class TestAWrongButCleanReadingSaysSo:
    def test_the_derived_mojibake_is_what_dhole_actually_returns(self):
        """The fixture above derives the wrong reading instead of pasting it, so
        pin the derivation to the decoder: one strict utf-8 miss, then
        gb18030 - damage 0, which is the whole reason no cheap check exists."""
        for text, enc in ((JA_TEXT, "shift_jis"), (KO_TEXT, "euc-kr")):
            body = text.encode(enc)
            assert charset_mod.decode_file_bytes(body, "") == (
                body.decode("gb18030"), "gb18030", 0
            )

    def test_a_shift_jis_file_no_longer_comes_back_as_silence(self, tmp_path):
        out = _parse(tmp_path, "sjis.csv", JA_TEXT.encode("shift_jis"))
        assert out.metadata["encoding"] == "gb18030"
        assert out.metadata["encoding_basis"] == "guessed"
        assert JA_AS_GB18030[:2] in out.content[0]
        assert JA_TEXT.split(",")[0] not in out.content[0]
        assert out.error == ""

    def test_the_hint_names_the_guess_and_hands_over_the_remedy(self, tmp_path):
        out = _parse(tmp_path, "sjis.csv", JA_TEXT.encode("shift_jis"))
        hint = out.next_action
        assert "charset here is a guess" in hint, hint
        assert "encoding=" in hint, hint
        for value in ("shift_jis", "euc-kr", "big5", "gbk"):
            assert value in hint, f"the value list is the actionable half: {hint}"
        # The claim stops at "nobody declared it". Anything stronger would be a
        # check dhole did not run.
        assert "encoding_undecodable" not in out.error

    def test_an_euc_kr_html_page_is_told_the_same_way(self, tmp_path):
        html = f"<html><body><p>{KO_TEXT}</p></body></html>"
        out = _parse(tmp_path, "euckr.html", html.encode("euc-kr"))
        assert out.metadata.get("encoding_basis") == "guessed"
        assert "charset here is a guess" in out.next_action
        assert KO_AS_GB18030[:3] in out.content[0]

    def test_content_ok_is_not_flipped_and_stays_the_whole_reason(
        self, tmp_path
    ):
        """The half the plan insists on. A GBK file is the same shape (nobody
        declared, strict gb18030 won) and is read CORRECTLY - so a false verdict
        here would punish the file that needs no help to make the one that does
        visible."""
        out = _parse(tmp_path, "sjis.csv", JA_TEXT.encode("shift_jis"))
        assert out.content_ok is True, "the note is on the guess, not on the read"
        assert "not being doubted" in out.next_action

        right = _parse(tmp_path, "gbk.csv", ZH_TEXT.encode("gbk"))
        assert right.content_ok is True and right.error == ""
        assert right.metadata.get("encoding_basis") == "guessed"
        assert "姓名" in right.content[0]


# ─── 3 · 解药真的走得通 ───


class TestTheRemedyTurnsTheGuessIntoAVerdict:
    def test_declaring_the_charset_returns_the_original_language(self, tmp_path):
        out = _parse(tmp_path, "sjis.csv", JA_TEXT.encode("shift_jis"),
                     encoding="shift_jis")
        assert "名前" in out.content[0] and "東京" in out.content[0]
        assert out.metadata["encoding"] == "shift_jis"

    def test_a_declared_read_says_nothing_about_a_guess(self, tmp_path):
        """职责：点了名而那颗确实在跑 —— 这一发永远不许被叫成"猜"。

        R02-F8 改的是这颗**值**（同一批字节的第二种干净读法现在要交代，basis 写成
        `contested`），不改这一格的职责：`guessed` 那一句在这发里一个字也不许出现。
        """
        out = _parse(tmp_path, "euckr.csv", KO_TEXT.encode("euc-kr"),
                     encoding="euc-kr")
        assert "한국어" in out.content[0]
        assert out.metadata.get("encoding_basis") != "guessed"
        assert "charset here is a guess" not in out.next_action


# ─── 4 · 不该说话的时候一个字都不说 ───


class TestTheNoteStaysQuietWhenNothingWasGuessed:
    def test_a_utf8_file_has_no_basis_and_no_hint(self, tmp_path):
        out = _parse(tmp_path, "utf8.csv", ZH_TEXT.encode("utf-8"))
        assert out.metadata["encoding"] == "utf-8"
        assert "encoding_basis" not in out.metadata
        assert "charset here is a guess" not in out.next_action

    def test_a_bom_states_the_charset_by_itself(self, tmp_path):
        out = _parse(tmp_path, "u16.csv", ZH_TEXT.encode("utf-16"))
        assert out.metadata["encoding"] == "utf-16"
        assert "encoding_basis" not in out.metadata

    def test_a_damaged_decode_keeps_its_own_stronger_message(self, tmp_path):
        """Big5-as-GB18030 lands in the PUA and is already reported as
        `encoding_undecodable`. Stacking "this was a guess" under a sentence
        that says the guess FAILED would read like two verdicts about one file."""
        out = _parse(tmp_path, "big5.csv", "姓名,年齡\n張三,30\n".encode("big5"))
        assert out.error.startswith("encoding_undecodable")
        assert out.content_ok is False
        assert "charset here is a guess" not in out.next_action


# ============================================================================
# 来源：test_a_number_over_the_timeout_ceiling_names_its_own_knob.py
# ============================================================================
# 一颗超过帽的 `timeout=`：谁被拒、拒的那句点名谁、四扇是不是同一位作者写的（评审 R02-093 · R02-096 · R02-097）。

DEAD = "http://example.invalid/"
CEILING = "Timeout exceeds maximum"
URL_BOILERPLATE = "takes an absolute http(s) URL"
READ_CLAUSE = "Nothing was fetched."
WRITE_CLAUSE = "Nothing was sent."
ACT_CLAUSE = "Nothing was run."


async def _wire(tool: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport
    async with InMemoryTransport(MasterFetchServer()._mcp_server()) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
            text = "".join(getattr(b, "text", "") for b in res.content)
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return {"_not_json": text[:400]}


def _call(tool, args):
    return asyncio.run(_wire(tool, args))


# 四扇收这颗参数的门（`web_search`/`doc_parse` 不收，另有一格反证钉着它们不该被卷进来）
CEILING_LEGS = [
    pytest.param("web_read", {"url": DEAD, "timeout": 999999}, READ_CLAUSE, id="web_read 999999"),
    pytest.param("web_read", {"url": DEAD, "timeout": 120001}, READ_CLAUSE, id="web_read 刚过帽"),
    pytest.param("web_act", {"url": DEAD, "actions": [{"click": {"selector": "b"}}],
                             "timeout": 999999}, ACT_CLAUSE, id="web_act"),
    pytest.param("web_request", {"url": DEAD, "method": "POST", "body": {"a": 1},
                                 "confirm": True, "timeout": 999999}, WRITE_CLAUSE,
                 id="web_request POST"),
    pytest.param("web_discover", {"url": DEAD, "timeout": 999999}, READ_CLAUSE, id="web_discover"),
    pytest.param("web_crawl", {"url": DEAD, "timeout": 999999}, READ_CLAUSE, id="web_crawl"),
    # 同一颗预算的第二族成因（`r113_feed_prefix.json`：0 与 -5 在三扇上撞出的形状与超帽逐字相同 ——
    # read 念 URL 样板、discover 具名拒、crawl 怪网络），所以判据按区间钉，不按方向钉。
    pytest.param("web_read", {"url": DEAD, "timeout": 0}, READ_CLAUSE, id="web_read 非正数"),
    pytest.param("web_discover", {"url": DEAD, "timeout": 0}, READ_CLAUSE, id="web_discover 0"),
    pytest.param("web_crawl", {"url": DEAD, "timeout": 0}, READ_CLAUSE, id="web_crawl 0"),
]


class TestTheCeilingNamesItsOwnKnob:
    """每一条超帽拒绝都要点名 `timeout`、说本门的话、并且不念 URL 那句样板。"""

    @pytest.mark.parametrize("tool,args,clause", CEILING_LEGS)
    def test_the_way_out_points_at_the_knob_that_was_wrong(self, tool, args, clause):
        p = _call(tool, args)
        err, way = p.get("error") or "", p.get("next_action") or ""
        # 成因有两种（超帽 / 非正数），识别针由产品那**一份定义**给出 ——
        # 这里不写死 "Timeout exceeds maximum"，否则这一格只在超帽那一支上有效（覆盖面按方向枚举，
        # 正是 R02-096 那把的病根）。
        assert any(n in err for n in security_mod.TIMEOUT_REFUSAL_NEEDLES), p
        assert "timeout" in way.lower(), f"出路没点名那颗参数: {way}"
        assert URL_BOILERPLATE not in way, f"出路念的是 URL 样板: {way}"

    @pytest.mark.parametrize("tool,args,clause", CEILING_LEGS)
    def test_the_door_still_declares_itself(self, tool, args, clause):
        p = _call(tool, args)
        texts = f"{p.get('error')} {p.get('next_action')}"
        assert clause in texts, texts
        assert texts.count(clause) == 1, f"同一句说了两遍: {texts}"

    @pytest.mark.parametrize("tool,args,clause", CEILING_LEGS)
    def test_the_refusal_travels_the_standard_envelope(self, tool, args, clause):
        """`error` 的前缀也是契约：宿主按 `invalid_request:` 归类失败（R02-097 那支漂了的前缀）。

        两种信封各有自己的"什么都没发生"格子：读正文那扇看 `status=0`，爬行/地图那两扇没有
        `status`，看的是 `pages` 空 + `pages_crawled=0`。按信封判，不要拿一颗模型的字段去问另一颗
        （那一格原本会永远红/永远绿，取决于 `p.get` 的默认值）。
        """
        p = _call(tool, args)
        assert str(p.get("error", "")).startswith("invalid_request:"), p
        if "pages" in p:
            assert p["pages"] == [] and p.get("pages_crawled") == 0, p
        else:
            assert p.get("status") == 0, p

    def test_the_crawl_stops_at_the_gate_and_does_not_blame_the_site(self):
        """R02-093 那一支的形状：门口不验时，页级汇总把超帽说成「站点慢、抬大 timeout 重试」。

        修后这一发要停在门口：一个包都没付（`pages` 空、`pages_crawled=0`），
        且那句话不能出现在出路里。这是**形状**判据，不是把网络错误一律禁掉 ——
        真的网络慢仍然会说它自己的话（`_no_request` 那类断言归 `test_bug_report2` 管）。
        """
        p = _call("web_crawl", {"url": DEAD, "timeout": 999999})
        texts = json.dumps(p, ensure_ascii=False)
        assert CEILING in (p.get("error") or ""), texts[:300]
        assert p.get("pages") == [] and p.get("pages_crawled") == 0, texts[:300]
        for phrase in ("network errors", "site may be slow", "switch sources",
                       "longer timeout"):
            assert phrase not in texts, f"还在怪站点: {phrase} · {texts[:300]}"

    @pytest.mark.parametrize("tool,args,clause", [
        pytest.param("feed_read", {"urls": [DEAD], "timeout": 999999}, READ_CLAUSE,
                     id="feed_read 的门话与前缀"),
    ])
    def test_feed_read_speaks_the_same_envelope(self, tool, args, clause):
        """`feed_read timeout: …` 那种漂了的前缀（R02-097）：宿主按 `invalid_request:` 归类。"""
        p = _call(tool, args)
        way = p.get("next_action") or ""
        assert str(p.get("error", "")).startswith("invalid_request:"), p
        assert "timeout" in way.lower(), way
        assert clause in f"{p.get('error')} {way}", p
        # 这颗错讲的是数字，不是那颗 `urls`：用法模板不能顶替它（与 URL 样板同一条纪律）
        assert "1-50 absolute" not in way, way

    @pytest.mark.parametrize("args", [
        pytest.param({"urls": [f"{DEAD}f{i}" for i in range(51)]}, id="51 颗 urls"),
        pytest.param({"urls": []}, id="一颗也没给"),
    ])
    def test_the_other_feed_read_refusals_travel_the_same_envelope(self, args):
        """同一封里另外两颗 ValueError 也没带前缀（`r113_feed_prefix.json` 实测两发）。

        判据与上面那颗同一条：这一扇被拒的回执要能被 `invalid_request:` 认出来、要说本门的话、
        出路要点名 `urls` 这一颗。不顺手改判据以外的东西。
        """
        p = _call("feed_read", args)
        way = p.get("next_action") or ""
        assert str(p.get("error", "")).startswith("invalid_request:"), p
        assert READ_CLAUSE in f"{p.get('error')} {way}", p
        assert "urls" in way.lower(), way


class TestOneAuthorForTheCeiling:
    """这句话只许有一位作者：四扇共用 security 里那份常量，门话按门装配。"""

    def _literals(self, code, doc=None) -> list:
        found = []
        for const in code.co_consts:
            if isinstance(const, str):
                if const != doc:
                    found.append(const)
            elif hasattr(const, "co_consts"):
                found.extend(self._literals(const))
        return found

    def _callables(self, mod):
        """类方法也要数：上一把它派生的那支尺只数模块级函数，于是它**永远绿**（㉞ 第四次撞这条）。"""
        for _, member in inspect.getmembers(mod, inspect.isfunction):
            yield member
        for _, klass in inspect.getmembers(mod, inspect.isclass):
            for _, meth in inspect.getmembers(klass, inspect.isfunction):
                yield meth

    def test_the_ceiling_sentence_has_one_author(self):
        """按常量池数：把预算区间写全的那句话，跨模块只许住在 `security` 那一颗常量里。

        `from … import TIMEOUT_CEILING_RECOVERY` 会把同一个**对象**挂到 server 的命名空间里，
        那不是第二位作者 —— 判据数的是"又写了一遍"，所以按对象身份跳过转发名（否则这一格会永远红，
        而永远红的格子与永远绿的格子一样没用）。
        """
        forwarded = {id(v) for v in vars(security_mod).values() if isinstance(v, str)}
        authors = []
        for fn in self._callables(server_mod):
            for const in self._literals(fn.__code__, fn.__doc__):
                if re.search(r"whole budget in milliseconds|1-120000", const):
                    authors.append((server_mod.__name__, fn.__qualname__, const[:70]))
        for name, val in vars(server_mod).items():
            if isinstance(val, str) and id(val) not in forwarded and \
                    re.search(r"whole budget in milliseconds|1-120000", val):
                authors.append((server_mod.__name__, name, val[:70]))
        assert authors == [], f"预算那句话还有别处自己写: {authors}"

    def test_the_ruler_finds_the_pre_fix_author(self):
        """反证：同一把尺在**修前那处**要数得到人 —— 数不到就是尺子瞎了，不是产品干净了。"""
        pre_fix = ("timeout= is this call's whole budget in milliseconds: 1-120000. "
                   "On a sitemap map it is also the clock that one index request runs on.")
        assert re.search(r"whole budget in milliseconds|1-120000", pre_fix)

    def test_the_needle_is_the_constant_the_handlers_read(self):
        """识别针只许一份定义（与 `RESOLVER_GUARD_NEEDLE` / `PRIVATE_ADDRESS_REFUSAL_PREFIXES` 同一惯例）。"""
        assert hasattr(security_mod, "TIMEOUT_REFUSAL_NEEDLES"), "缺一组共享的识别针"
        needles = security_mod.TIMEOUT_REFUSAL_NEEDLES
        assert CEILING in " ".join(needles), needles
        assert any("positive" in n for n in needles), needles
        assert hasattr(security_mod, "TIMEOUT_CEILING_RECOVERY"), "缺一份共享的出路"
        # 出路要点名那颗参数本身，且不念 URL 样板（R02-096 的病根）
        rec = security_mod.TIMEOUT_CEILING_RECOVERY
        assert "timeout=" in rec and URL_BOILERPLATE not in rec, rec

    def test_the_default_url_template_is_still_there_for_url_errors(self):
        """反证：这一把不许把默认模板打死 —— URL 自己写坏时那句话仍然是对的出路。"""
        p = _call("web_read", {"url": "ftp://example.com/"})
        assert URL_BOILERPLATE in (p.get("next_action") or ""), p


class TestTheDoorsThatDoNotTakeIt:
    """不收这颗参数的两扇不能被卷进来（它们撞的是陌生参数名那一层）。"""

    @pytest.mark.parametrize("tool,args", [
        pytest.param("web_search", {"query": "widgets", "timeout": 999999}, id="web_search"),
        pytest.param("doc_parse", {"file_path": "none.pdf", "timeout": 999999}, id="doc_parse"),
    ])
    def test_a_door_without_the_knob_says_so(self, tool, args):
        p = _call(tool, args)
        assert "Unsupported argument(s)" in (p.get("error") or ""), p
        assert CEILING not in (p.get("error") or ""), p


# ============================================================================
# 来源：test_a_source_leg_says_which_way_it_failed.py
# ============================================================================
# 17.0 R02-138 + R02-140 + R02-149：`find_similar` 的源页那条腿要说得出它看见了什么。

URL = "https://src.test/page"
CLAUSE = "Nothing was searched - no engine was asked."
# 五种"源页这条腿没拿到可比对的正文"的形状：(站点什么, 期望 code, 期望 retryable)
_PAIRS = [
    # blocked 那颗的 retryable 交回表的答案（True：一面墙下次不一定还在）—— 这一把不替表改判，
    # 只让建议位跟着它写（R02-137 那条法的形状见下面那枚腿）。
    pytest.param("blocked", "bot_wall", True, id="命中 blocked 字样"),
    pytest.param("refused", "connection_refused", False, id="连不上（异常）"),
    pytest.param("empty", "no_content", False, id="200 但零字节"),
    pytest.param("no-text", "extraction_collapsed", False, id="有字节抽不出正文"),
    pytest.param("gone", "http_error_404", False, id="404 且没正文"),
]
KINDS = [p.values[0] for p in _PAIRS]


class _Raises:
    def __init__(self, text):
        self.text = text

    async def get(self, url, **kw):
        raise RuntimeError(self.text)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


class _Answered:
    def __init__(self, status, body):
        self.status = status
        self.status_code = status
        self.body = body
        self.encoding = "utf-8"
        self.headers = {}


class _Answers:
    def __init__(self, status, body):
        self.status, self.body = status, body

    async def get(self, url, **kw):
        return _Answered(self.status, self.body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


def _plant(monkeypatch, kind):
    """把取回那一层换成一种站到的形状（其余全走真路）。"""
    if kind == "blocked":
        body = (b"<html><head><title>Hold on</title></head><body>"
                b"<p>Access Denied: please verify you are a human.</p></body></html>")
        monkeypatch.setattr(FT, "HTTPSession", lambda **kw: _Answers(200, body))
    elif kind == "refused":
        monkeypatch.setattr(
            FT, "HTTPSession",
            lambda **kw: _Raises("[Errno 111] Connection refused"))
    elif kind == "empty":
        monkeypatch.setattr(FT, "HTTPSession", lambda **kw: _Answers(200, b""))
    elif kind == "no-text":
        monkeypatch.setattr(FT, "HTTPSession", lambda **kw: _Answers(
            200, b"<html><body><img src='/a.png'></body></html>"))
    elif kind == "gone":
        monkeypatch.setattr(FT, "HTTPSession", lambda **kw: _Answers(
            404, b"<html><body><img src='/x.png'></body></html>"))
    elif kind == "gone-with-title":
        monkeypatch.setattr(FT, "HTTPSession", lambda **kw: _Answers(
            404, b"<html><head><title>404</title></head><body>"
                  b"<p>The page you asked for is gone. It once described how to configure "
                  b"a local fetch tool, with several paragraphs of real prose about proxies, "
                  b"timeouts, and cache lifetimes, long enough to be extracted.</p></body></html>"))
    else:
        raise AssertionError(kind)


def _ask(monkeypatch, kind):
    """跑一次真的门（只有取回层与引擎是桩），读回源页这条腿交出的那一封。"""
    _plant(monkeypatch, kind)
    asked: list = []

    async def spy(query, max_results, **kw):
        asked.append(query)
        return [], []

    real = search_mod.multi_search
    search_mod.multi_search = spy
    try:
        out = asyncio.run(Server().web_search(mode="find_similar", url=URL,
                                              cache_ttl=0))
    finally:
        search_mod.multi_search = real
    return out, asked


class TestEachFailureSaysWhichOne:
    @pytest.mark.parametrize("kind,code,retryable", _PAIRS)
    def test_the_error_leads_with_the_registered_name_for_that_fact(
            self, monkeypatch, kind, code, retryable):
        out, _ = _ask(monkeypatch, kind)
        assert out.error, "这一形状本该被拒，却回了没有 error 的包"
        assert (out.diagnosis or {}).get("code") == code, (kind, out.error, out.diagnosis)
        assert (out.diagnosis or {}).get("retryable") is retryable, (
            kind, out.diagnosis)

    @pytest.mark.parametrize("kind", KINDS)
    def test_the_five_failures_are_not_one_sentence(self, monkeypatch, kind):
        """同一句话装五副形状就是 R02-138：这里钉"五副各一句"。"""
        out, _ = _ask(monkeypatch, kind)
        assert "blocked or offline" not in out.error, out.error
        assert "两可" not in out.error

    def test_no_second_sentence_for_the_same_two_facts(self, monkeypatch):
        """五句要各不相同，也不许两副形状共用一句（0 字节与 404 没正文不是同一件事）。"""
        errs = [_ask(monkeypatch, k)[0].error for k in KINDS]
        assert len(set(errs)) == len(errs), errs


class TestAdviceAndVerdictAgree:
    """R02-137 那条法在这一族上也要成立：写着「Retry」的回执不许报 retryable=false，反之亦然。"""

    @pytest.mark.parametrize("kind", KINDS)
    def test_the_way_out_and_the_handle_answer_the_same_question(self, monkeypatch, kind):
        out, _ = _ask(monkeypatch, kind)
        na = (out.next_action or "").lower()
        says_retry = "retry" in na
        assert says_retry == bool((out.diagnosis or {}).get("retryable")), (
            kind, na[:120], out.diagnosis)


class TestTheEnvelopeIsAssembledNotHandmade:
    @pytest.mark.parametrize("kind", KINDS)
    def test_a_source_leg_refusal_declares_at_its_head(self, monkeypatch, kind):
        out, _ = _ask(monkeypatch, kind)
        assert (out.next_action or "").startswith(CLAUSE), (kind, out.next_action)

    @pytest.mark.parametrize("kind", KINDS)
    def test_a_source_leg_refusal_labels_the_usage_log_line(self, monkeypatch, kind):
        out, _ = _ask(monkeypatch, kind)
        assert (out.summary or "").strip(), (kind, out.summary)

    def test_the_door_does_not_hand_make_a_refusal_envelope(self):
        """拒绝信封由装配位交出：门里不许出现 `return SearchResponseModel(error=<字面句>)`。

        钉的是形状不是文本 —— 把那句搬回门里手搓（哪怕换了措辞、哪怕补了 summary），这里照样红。
        """
        src = inspect.getsource(Server.web_search)
        for i, line in enumerate(src.splitlines(), 1):
            if "SearchResponseModel(" in line:
                tail = "\n".join(src.splitlines()[i - 1:i + 6])
                assert not re.search(r"error=\s*f?[\"']", tail), (
                    f"门里第 {i} 行手搓了一封带字面 error 的拒绝信封：{line.strip()}")


class TestASourceThatAnsweredBadlyIsStillSaidOutLoud:
    """R02-149：404 页带着可读正文时门可以继续用它 —— 但这一件必须写在回执里。"""

    def test_the_404_source_page_is_declared(self, monkeypatch):
        out, asked = _ask(monkeypatch, "gone-with-title")
        assert len(asked) == 1, f"这一形状本该继续搜（行为不改），问到 {asked}"
        blob = " ".join(filter(None, [out.summary, out.next_action, out.fetch_hint]))
        assert "404" in blob, (out.summary, out.next_action, out.fetch_hint)

    def test_declaring_the_404_costs_no_extra_request(self, monkeypatch):
        """报出口不等于替调用方再抓一次：引擎那边只许被问一次，取回层只许被走一次。"""
        calls: list = []
        _plant(monkeypatch, "gone-with-title")
        real_get = _Answers.get

        async def counting_get(self, url, **kw):
            calls.append(url)
            return await real_get(self, url, **kw)

        monkeypatch.setattr(_Answers, "get", counting_get)
        n_engine = []

        async def spy(query, max_results, **kw):
            n_engine.append(query)
            return [], []

        real = search_mod.multi_search
        search_mod.multi_search = spy
        try:
            asyncio.run(Server().web_search(mode="find_similar", url=URL, cache_ttl=0))
        finally:
            search_mod.multi_search = real
        assert len(calls) == 1 and len(n_engine) == 1, (calls, n_engine)


# ============================================================================
# 来源：test_enabled_tools.py
# ============================================================================
# Connect-time token spend: DHOLE_TOOLS registers only a subset of the tools.

def _run_fresh(code: str, env_value: "str | None") -> subprocess.CompletedProcess:
    """Run ``code`` in a fresh interpreter with DHOLE_TOOLS set to env_value.

    ``None`` means the variable is absent. The parent's DHOLE_TOOLS (if the
    operator exported one) is always scrubbed first, so the "unset" case is
    really unset.
    """
    env = {k: v for k, v in os.environ.items() if k != "DHOLE_TOOLS"}
    if env_value is not None:
        env["DHOLE_TOOLS"] = env_value
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env, timeout=180)


_ENABLED_SNIPPET = (
    "import json, dhole_mcp.server as s;"
    "print(json.dumps({'enabled': sorted(s._ENABLED_TOOLS),"
    " 'all': sorted(s._TOP_LEVEL_ARGS)}))"
)


class TestTheEnvVarIsParsed:

    @pytest.mark.parametrize("value", ["web_read", " web_read ,web_search ,"])
    def test_a_subset_registers_exactly_those_tools(self, value):
        out = _run_fresh(_ENABLED_SNIPPET, value)
        assert out.returncode == 0, out.stderr[-600:]
        payload = json.loads(out.stdout)
        assert payload["enabled"] == sorted(
            n.strip() for n in value.split(",") if n.strip())
        assert set(payload["all"]) == {d["name"] for d in MasterFetchServer._TOOL_DEFS}, (
            "登记的全集与 _TOOL_DEFS 对不上 —— 默认关闭的那几颗也必须在里面，"
            "否则守卫覆盖不到它们")

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_unset_means_every_tool(self, value):
        """17.0：未设 DHOLE_TOOLS = 全部工具（没有 opt-in 组；数量由上一条钉住）。"""
        out = _run_fresh(_ENABLED_SNIPPET, value)
        assert out.returncode == 0, out.stderr[-600:]
        payload = json.loads(out.stdout)
        assert set(payload["enabled"]) == set(payload["all"])

    def test_a_stale_tasks_flag_enables_nothing_extra(self):
        """DHOLE_TASKS 已注销：设与不设，enabled 集合逐字节相同。"""
        env = {k: v for k, v in os.environ.items()
               if k not in ("DHOLE_TOOLS", "DHOLE_TASKS", "DHOLE_VAULT")}
        out_plain = subprocess.run([sys.executable, "-c", _ENABLED_SNIPPET],
                                   capture_output=True, text=True, env=env,
                                   timeout=180)
        env["DHOLE_TASKS"] = "0"
        out_stale = subprocess.run([sys.executable, "-c", _ENABLED_SNIPPET],
                                   capture_output=True, text=True, env=env,
                                   timeout=180)
        assert out_plain.returncode == 0 and out_stale.returncode == 0
        assert json.loads(out_plain.stdout) == json.loads(out_stale.stdout)

    @pytest.mark.parametrize("value,bad", [
        ("smart_frobnicate", "smart_frobnicate"),
        ("web_read,bogus", "bogus"),
    ])
    def test_an_unknown_name_raises_at_import_naming_it(self, value, bad):
        """A typo must kill startup loudly, not silently drop a capability."""
        out = _run_fresh("import dhole_mcp.server", value)
        assert out.returncode != 0, "未知工具名必须在 import 期炸掉"
        assert "DHOLE_TOOLS" in out.stderr and bad in out.stderr
        assert "Valid names" in out.stderr, "报错必须列出合法名字，让人一次改对"

    def test_a_value_with_no_names_raises_instead_of_enabling_nothing(self):
        out = _run_fresh("import dhole_mcp.server", ",,,")
        assert out.returncode != 0
        assert "names no tools" in out.stderr


class TestTheWireSetIsFiltered:

    def test_only_enabled_tools_are_advertised_in_schema_order(self):
        code = (
            "import json, dhole_mcp.server as s;"
            "print(json.dumps([td['name'] for td in"
            " s.MasterFetchServer._enabled_tool_defs()]))"
        )
        out = _run_fresh(code, "web_read,web_search")
        assert out.returncode == 0, out.stderr[-600:]
        assert json.loads(out.stdout) == ["web_read", "web_search"], (
            "广告集被过滤，且保持 _TOOL_DEFS 的顺序")


class TestDispatchRefusesDisabledTools:

    CODE = (
        "import asyncio, dhole_mcp.server as s\n"
        "async def main():\n"
        "    srv = s.MasterFetchServer(cache_ttl=0)\n"
        "    try:\n"
        "        await srv._dispatch('web_crawl', {'url': 'https://example.com/'})\n"
        "        print('CRAWL: dispatched')\n"
        "    except ValueError as e:\n"
        "        print('CRAWL:', e)\n"
        "    try:\n"
        "        await srv._dispatch('smart_frobnicate', {})\n"
        "    except ValueError as e:\n"
        "        print('FROB:', e)\n"
        "    try:\n"
        "        r = await srv._dispatch('web_read', {'url': 'not a url'})\n"
        "        print('FETCH: dispatched', type(r).__name__)\n"
        "    except Exception as e:\n"
        "        print('FETCH: raised', type(e).__name__, str(e)[:100])\n"
        "asyncio.run(main())\n"
    )

    def test_disabled_known_unknown_and_enabled_calls_behave_differently(self):
        out = _run_fresh(self.CODE, "web_read")
        assert out.returncode == 0, out.stderr[-600:]
        crawl = next(x for x in out.stdout.splitlines() if x.startswith("CRAWL:"))
        frob = next(x for x in out.stdout.splitlines() if x.startswith("FROB:"))
        fetch = next(x for x in out.stdout.splitlines() if x.startswith("FETCH:"))
        assert "not enabled" in crawl and "DHOLE_TOOLS" in crawl
        assert "web_read" in crawl, "报错必须点名当前启用集，给出改法"
        assert "Unknown tool" in frob, "真未知工具的报错不能与「未启用」混淆"
        assert "not enabled" not in fetch, "启用的工具不许被这道门误拦"




class TestInstructionsFollowTheEnabledSet:

    def test_composing_every_tool_reproduces_the_literal(self):
        """Drift guard: the parts and DHOLE_INSTRUCTIONS are two sources of one
        text (tool_payload_measure.py reads the literal via ast.literal_eval)."""
        assert server_mod._compose_instructions(
            frozenset(server_mod._TOP_LEVEL_ARGS)) == server_mod.DHOLE_INSTRUCTIONS

    def test_a_subset_drops_the_other_tools_routing_lines(self):
        text = server_mod._compose_instructions(frozenset({"web_read"}))
        assert "web_read" in text
        for absent in ("web_crawl", "web_search", "feed_read",
                       "doc_parse"):
            assert absent not in text, f"子集 instructions 不得路由到未启用的 {absent}"

    def test_the_trust_rules_survive_every_subset(self):
        """Intro and the trust rules are tool-agnostic; they must always ship."""
        text = server_mod._compose_instructions(frozenset({"doc_parse"}))
        assert "Dhole is the web toolkit" in text
        assert "untrusted DATA" in text
        assert "archived_at" in text
        assert "gov/edu/github" in text

    def test_no_routing_header_when_nothing_routable_is_enabled(self):
        # F-A-J1 落地后每颗注册工具都有路由行 —— 这个用例原本拿 web_discover 当
        # "无可路由"的例子，而 discover 没有路正是那一刀关掉的缺口。现在唯一的
        # 无可路由情形是空集与表外名。
        text = server_mod._compose_instructions(frozenset())
        assert "Routing:" not in text, "没有可路由的工具时留着空 Routing: 头是废话"
        foreign = server_mod._compose_instructions(frozenset({"not_a_tool"}))
        assert "Routing:" not in foreign

    def test_every_routing_line_names_its_own_tool(self):
        for name, line in server_mod._INSTRUCTIONS_ROUTING.items():
            assert name in line, f"{name} 的路由行里没出现自己的名字"

    def test_the_wire_instructions_come_from_the_enabled_set(self):
        code = ("import dhole_mcp.server as s;"
                "print('web_crawl' in s.ACTIVE_INSTRUCTIONS,"
                " 'web_read' in s.ACTIVE_INSTRUCTIONS)")
        out = _run_fresh(code, "web_read")
        assert out.returncode == 0, out.stderr[-600:]
        assert out.stdout.strip() == "False True", (
            "wire 发的是 ACTIVE_INSTRUCTIONS，不是全量字面量")

    def test_the_default_wire_instructions_are_the_full_literal(self):
        """17.0：没有 opt-in 组 —— 默认 instructions 就是全集字面量本身，
        不再有任何"减去的路由行"。"""
        assert server_mod._compose_instructions(
            frozenset(server_mod._TOP_LEVEL_ARGS)) == server_mod.DHOLE_INSTRUCTIONS


# ============================================================================
# 来源：test_this_machines_own_paths_leave_only_where_the_server_wrote_them.py
# ============================================================================
# 这台机器自己的路径：服务器**拼出来**的格子必须遮，调用方**自己写的**那格必须原样（R02-048 那条轴收口 · 2026-10-18 · r223c）.

HOME = os.path.expanduser("~")
CWD = os.getcwd()
USER = (os.environ.get("USERNAME") or os.path.basename(HOME)).strip()
#: 用户名按**子串**数 —— 与遮蔽器自己的规矩同族（`_identity_names` 是整串替换，
#: 「出现在任何一段里都要折」，R02-090）。r223 那把针要求尾分隔符，于是看不见
#: `probe-of-<user>` 与 `%2F<user>%2F` 这两种形状，把假绿当成了读数（B-79）。
USER_SEG = re.compile(re.escape(USER), re.I)
FOREIGN = "D:\\qoder-r224-elsewhere\\report.csv"   # 不属于这两根，也不是别台机器的根：调用方自己编的


def _needles(node, path=""):
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            out += _needles(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += _needles(v, f"{path}[{i}]")
    elif isinstance(node, str):
        low = node.lower()
        for root in (HOME, HOME.replace("\\", "/"), CWD, CWD.replace("\\", "/")):
            if root and root.lower() in low:
                out.append((path, root))
                break
        if USER_SEG.search(node):
            out.append((path, f"/{USER}/"))
    return out


def _dump(obj) -> dict:
    return json.loads(json.dumps(
        {k: v for k, v in obj.model_dump().items() if k not in ("content",)},
        default=str))


def _local(path: str):
    return asyncio.run(MasterFetchServer().doc_parse(path))


def test_a_file_under_the_home_reads_back_with_the_home_folded(tmp_path):
    """成功面：`doc_parse` 的 `url` 由服务器拼，家目录那一段不许跟着出门（F25 那格的正面）。"""
    p = os.path.join(HOME, "qoder-r224-probe.txt")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("AAA1 BBB2 CCC3 DDD4 EEE5 FFF6\n" + "q" * 60)
    try:
        r = _local(p)
        assert not r.error, r.error
        assert _needles(_dump(r)) == [], _needles(_dump(r))
        assert "~" in (r.url or ""), r.url
    finally:
        os.remove(p)


def _probe_base_outside_both_roots():
    """挑一个「两根之外」的落脚处，并记下哪一层是我们自己造的（B-82）。

    这一格的判据要求用户名住在**两根之外**（R02-090 那根针测的就是这件事），所以不能简单挪进
    `tmp_path`：本仓跑套件时 `TMP` 常指着家目录底下，那颗名字就落回两根之一，针于是量不到它本来
    要量的东西。从前这里写死 `D:\\tmp\\qoder-r224\\probe-of-<user>`，读数是对的，但它**跑完不删**
    —— 与本仓 conftest 那条「测试不许在别人的机器上留东西」同一条轴。现在两个都要：造出来的层
    由调用方在 finally 里收回去。
    """
    roots = [os.path.realpath(r).lower() for r in (HOME, CWD)]

    def outside(p):
        real = os.path.realpath(p).lower()
        return not any(real.startswith(root) or root.startswith(real) for root in roots)

    cands = [tempfile.gettempdir()]
    home_drive = os.path.splitdrive(os.path.realpath(HOME))[0].rstrip(":\\").upper()
    for letter in ("D", "E", "F", "G"):
        if f"{letter}:" != home_drive and os.path.exists(f"{letter}:\\"):
            cands.append(f"{letter}:\\dhole-test-probe")
            break
    for cand in cands:
        if not cand or not outside(cand):
            continue
        if os.path.isdir(cand):
            return cand, False
        try:
            os.makedirs(cand, exist_ok=True)
        except OSError:
            continue
        return cand, True
    return None, False


def test_a_username_segment_outside_both_roots_is_folded_on_the_success_face():
    """R02-090 那根针在**成功面**也成立：用户名住在两根之外（别的盘的临时根）也要折。"""
    base, created = _probe_base_outside_both_roots()
    if base is None:
        pytest.skip("这台机器上找不到既在两根之外、又能建的落脚处 —— 这一格量不到它要量的东西，"
                    "不许改判到家目录里")
    d = os.path.join(base, f"probe-of-{USER}")
    p = os.path.join(d, "y.txt")
    try:
        os.makedirs(d, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("CCC3 DDD4 EEE5 FFF6\n" + "w" * 40)
        r = _local(p)
        assert not r.error, r.error
        assert _needles(_dump(r)) == [], _needles(_dump(r))
        assert "<user>" in (r.url or "").lower(), r.url
    finally:
        shutil.rmtree(d, ignore_errors=True)
        if created:
            try:
                os.rmdir(base)
            except OSError:
                pass


def test_a_missing_path_under_the_home_stays_folded_all_the_way_down():
    """拒绝面：`error` 遮过而 `url` 没遮，正是 R02-048 当年那一封的形状 —— 两头一起数。"""
    r = _local(os.path.join(HOME, "nope-224.docx"))
    assert str(r.error or "").startswith("File not found"), r.error
    assert _needles(_dump(r)) == [], _needles(_dump(r))


def test_a_path_that_is_not_this_machine_comes_back_verbatim():
    """不许遮过头：调用方自己编的地址（不属于这两根）原样交回 —— 遮它等于改他的问题。"""
    r = _local(FOREIGN)
    assert str(r.error or "").startswith("File not found"), r.error
    assert FOREIGN.replace("\\", "/") in (r.url or "").replace("\\", "/"), r.url
    low = (r.url or "").lower().replace("\\", "/")
    assert "qoder-r224-elsewhere/report.csv" in low, r.url
    assert "<server cwd>" not in low and "~" not in low, (
        f"调用方自己编的地址被遮过了: {r.url}")


@pytest.mark.parametrize("door,field", [
    ("web_read", "url"), ("web_crawl", "start_url"), ("web_discover", "start_url"),
])
def test_the_identity_field_answers_what_that_call_looked_at(door, field):
    """请求身份那一格交回调用方自己打的原文（判决的另一半：把它遮成别的样子是假话）。

    打的是 `file://` —— 网络门在拨号前就拒（scheme 闸），所以这一格不需要网络也不需要私网豁免，
    而 `url` 那一格照样要答「这一发去看的是哪个地址」。
    """
    target = "file:///" + os.path.join(HOME, "r224-identity.txt").replace("\\", "/")
    fn = getattr(MasterFetchServer(), door)
    r = asyncio.run(fn(target)) if door == "web_read" else asyncio.run(fn(url=target))
    got = str(getattr(r, field, "") or "")
    assert got.replace("\\", "/") == target.replace("\\", "/"), (door, field, got)
    assert "file" in str(r.error or "").lower(), r.error


def test_the_masker_is_the_single_author_of_the_folded_face():
    """归属锁（F93 通则）：把遮蔽器换成恒等函数，拼出来的那几格要跟着漏 —— 漏了才知道尺子在数谁。"""
    p = os.path.join(HOME, "qoder-r224-probe2.txt")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("AAA1 BBB2\n" + "e" * 60)
    original = paths_mod.mask_machine_paths
    try:
        paths_mod.mask_machine_paths = lambda text: text
        r = _local(p)
        leaked = _needles(_dump(r))
    finally:
        paths_mod.mask_machine_paths = original
    assert leaked, "恒等遮蔽器下这格居然没漏 ⇒ 这一格根本不是由遮蔽器写的（尺子数错了作者）"
    clean = _needles(_dump(_local(p)))
    assert clean == [], f"还原之后仍漏: {clean}"
