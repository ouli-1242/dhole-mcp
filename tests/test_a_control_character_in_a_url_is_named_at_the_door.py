# -*- coding: utf-8 -*-
"""一颗控制字符进了 URL，门口要说它在那儿，而不是让解析与传输各自替它"修好"（评审 R02-099 · P3）。

现场（本把的量具，HEAD 115064a · 9202 pid 22648 · 零外网 —— 全部打在 127.0.0.1:8400 一台自造的
echo 服务上；存档 `sessions/P02-R41-control-char-dependency.jsonl` · 读数 `r146_control_char_dependency.json`
/ `r147_door_shapes.json`）：

| 送出去的形状 | 门口 `validate_url` | 它自己的解析视图 | **被问的那台服务器收到** |
|---|---|---|---|
| `/r146x`（基准） | 放行，原样交回 | `/r146x` | `GET /r146x` · 14 颗头 |
| `/r146\\nx`（LF） | **放行，原样交回** | `/r146x` ← 已经被丢掉 | `GET /r146x` —— 换了目标，回执 200 |
| `/r146\\tx`（TAB） | 放行 | `/r146x` | `GET /r146x` —— 同上 |
| `/r146\\rx`（CR） | 放行 | `/r146x` | `GET /r146x` —— 同上 |
| `/r146 x`（空格） | 放行 | `/r146 x` | `GET /r146%20x` —— **编码了，目标没换** |
| `/r146\\x7fx`（DEL） | 放行 | `/r146\\x7fx` | `GET /r146%7Fx` —— 编码了，目标没换 |

三件事因此是被量出来的，不是被推出来的：
① 同一类"不该出现在 URL 里的字符"里有**两种待遇**：空格与 DEL 走编码（目标保留），LF/TAB/CR 走删除
（目标被换）；② 门口连"我看见了一颗换行"都不说，而**它自己的解析视图已经和送出去的一致了** ——
也就是校验器在读者面前把那颗字符当作不存在；③ 拆头/拆行**没打通**（服务端始终 14 颗头，与基准同数），
所以危害按实测记：换目标 + 回执一切正常，不按 CVE 记。

依赖面（收紧之前先量）：`tests/` 里 URL 形状的字面串带控制字符的格数 = **0**（字面扫描，
f-string 等动态拼接不在其内）；`security.py` 内唯一的剥控制字符那一处是**搜索词**（query）的清洗，
不是 URL 的路径 —— 这一把不碰那颗值。

判据（本文件钉住）：
  * `validate_url` 对 C0 控制字符（0x00-0x1F）与 0x7F **具名拒绝**，引用的形状走 `repr` 转义
    （回执里原样带一颗换行等于允许输入伪造一行 —— `_unsupported_scheme_face` 早就按这条办）。
  * 句子与门话走既有那两位作者：成因由守卫一处说，出路 = `URL_SHAPE_RECOVERY`，行首申报按门装配。
  * 空格照旧放行（目标以 `%20` 保留）—— 收紧不许顺手变成"不许有空白"。
  * 反面锁：backslash 那句与私网地址那句一字未动（同族里已有判据的位置不挪）。
"""
from __future__ import annotations

import json

import pytest

from dhole_mcp.security import SecurityError, validate_url
from dhole_mcp.server import MasterFetchServer

CLEAN = "http://example.com/r146x"
BAD = {
    "LF": "http://example.com/r146\nx",
    "TAB": "http://example.com/r146\tx",
    "CR": "http://example.com/r146\rx",
    "VT": "http://example.com/r146\x0bx",
    "NUL": "http://example.com/r146\x00x",
    "DEL": "http://example.com/r146\x7fx",
}
SPACE = "http://example.com/r146 x"


class TestTheDoorNamesTheCharacter:
    def test_a_clean_url_still_walks_through_unchanged(self):
        assert validate_url(CLEAN) == CLEAN

    @pytest.mark.parametrize("name", sorted(BAD))
    def test_the_door_refuses_a_control_character(self, name):
        with pytest.raises(SecurityError) as got:
            validate_url(BAD[name])
        msg = str(got.value)
        assert "control character" in msg.lower(), msg
        assert "U+" in msg, f"要说得出是哪一颗字符的码位：{msg}"

    @pytest.mark.parametrize("name", sorted(BAD))
    def test_the_refusal_never_echoes_a_raw_control_character(self, name):
        """引用输入要转义：原样带一颗换行上路，等于让一次调用伪造回执的一行。"""
        with pytest.raises(SecurityError) as got:
            validate_url(BAD[name])
        raw = str(got.value)
        assert "\n" not in raw and "\r" not in raw and "\t" not in raw and "\x00" not in raw, repr(raw)

    def test_a_space_is_still_a_space_not_a_refusal(self):
        """对照锁：量到的待遇是空格编码后目标保留；不许把这一把的收紧扩成"不许有空白"。"""
        assert validate_url(SPACE) == SPACE

    def test_the_quoted_neighbourhood_is_redacted_and_capped(self):
        """引用调用方的输入不等于把他的凭据抄进回执 —— 走既有 redaction（同族的第三张成本）。

        这一格是 §14 教出来的：第一版只报码位与偏移，`test_a_refusal_that_quotes_what_the_caller_
        actually_sent` 那条既有契约判"读者手里没有可改的东西"，于是补了上下文引用 —— 补了引用，
        就必须同批付上转义、帽、redaction 三张成本，缺一张就是换了个新漏法。
        """
        secret = "sk-" + "a" * 24
        with pytest.raises(SecurityError) as got:
            validate_url(f"http://example.com/{secret}\nx")
        msg = str(got.value)
        assert secret not in msg, msg[:200]
        assert "[API_KEY_REDACTED]" in msg, msg[:220]
        assert "\n" not in msg and "\\n" in msg, repr(msg)[:200]


class TestTheReceiptsSpeakFromTheirOwnDoor:
    @pytest.mark.asyncio
    async def test_web_read_refuses_before_any_request_goes_out(self):
        r = await MasterFetchServer()._dispatch("web_read", {"url": BAD["LF"]})
        rows = r[1] if isinstance(r, tuple) else r
        d = rows if isinstance(rows, dict) else json.loads(rows[0].text)
        assert str(d.get("error", "")).startswith("invalid_request:")
        assert (d.get("next_action") or "").startswith("Nothing was fetched")
        assert "control character" in str(d.get("error", "")).lower()
        assert d.get("status") == 0

    @pytest.mark.asyncio
    async def test_the_search_door_says_it_sent_nothing_itself(self):
        """同一颗值在另一扇门上要说另一扇门的动词 —— 不许由读门代答（R02-098/100 那条轴）。"""
        r = await MasterFetchServer().web_discover(url=BAD["LF"], max_pages=1)
        d = r.model_dump()
        assert str(d.get("error", "")).startswith("invalid_request:")
        na = d.get("next_action") or ""
        assert na.startswith("Nothing was fetched") or na.startswith("Nothing was mapped"), na[:160]
        assert "control character" in str(d.get("error", "")).lower(), str(d.get("error"))[:160]


class TestTheGatesDidNotMove:
    def test_the_backslash_rule_is_untouched(self):
        with pytest.raises(SecurityError) as got:
            validate_url("http://example.com\\..\\etc")
        assert "backslash" in str(got.value).lower(), str(got.value)

    def test_the_private_address_rule_is_untouched(self):
        with pytest.raises(SecurityError) as got:
            validate_url("http://127.0.0.1:8399/")
        assert "127.0.0.1 in 127.0.0.0/8" in str(got.value), str(got.value)

    def test_query_sanitization_is_a_different_value_and_stays(self):
        """搜索词那一族的清洗（security 的 strip control chars）不是 URL 的路径，本把不碰。"""
        from dhole_mcp.security import validate_search_query
        assert validate_search_query("hello\x01 world").replace(" ", "") != ""
