"""点名了一颗 `encoding=`，跑的却是另一颗 —— 回执必须说清「你要的」与「用的」是两个。

现场（2026-10-14 真实使用模拟 · P02-06 高杉（高校讲师 L1）· S9，四发对照）：一份真的
EUC-KR 韩文文件，`doc_parse(file_path='…/korean-euckr.txt', encoding='shift_jis')` 交回：

    status=200  content_ok=true
    content[0] = 霉掳 矫埃 救郴: 绊钙遏苞 …        <-- gb18030 解出来的错中文
    metadata   = {"encoding": "gb18030"}           <-- 没有 encoding_basis
    next_action = （空）

同一颗文件不给编码那一发（S7A）反而**有** `encoding_basis: guessed` 与那句警告；点名
`encoding='euc-kr'`（S7B）正文就是正确的韩文。也就是说：用户最信任的那一发（他点了名）
唯一关掉了「这个名字是猜出来的」那句申报。P3 补测又给出第二例：GBK 件点名 `big5`
（`sessions/P02-R3-b-big5.json`）—— 同样回落 gb18030、同样无 basis、无警告。

机制（`charset.py` 两处读同一件事）：`decode_file_bytes` 在点名的那颗解不开时
（`except (LookupError, UnicodeError)`）**落到自动检测**；而旁边那颗回答「这是猜的吗」
的 `encoding_was_guessed` 只看「有没有人声明过」（`if declared: return False`），不看
声明的那一颗**有没有真的在跑**。docstring 写的是「A declared name that decoded
successfully is NOT a guess」，判据却把「decoding 成功」那一半漏掉了 —— 于是回落被标成
了声明，随它一起消失的还有 `quality._agent_hints` 里那句唯一说真话的 hedge。

契约（本文件钉住）：
  * 点了名而那份字节**严格解得开** → 跑的就是你点的那一颗：`encoding_asked` 与 `encoding`
    同名，正文是那颗名字读出来的文本，`content_ok` 不动，而且这一发**永远不许被叫成"猜"**。
    （这一族反面锁一字不改。）改的是另一件事：`encoding_basis` 在"这批字节还有第二种
    干净的读法、两份文本不同"时写成 `contested` 并说出的另一颗名字 —— 「相容」从来不是
    「测量」（R02-037 量的是那一格；见
    tests/test_a_named_charset_that_decodes_cleanly_still_names_the_rival.py）。
  * 点了名而它解不开、由检测顶上 → `metadata.encoding_asked` 是你点的那颗、
    `metadata.encoding` 是真跑的那颗，两个名字都在场，且 `encoding_basis=fallback` 带着
    一句**点名两颗名字**的话进 `next_action`。
  * `content_ok` 不翻假、`error` 不写：这份正文正是调用方要看的东西，而这一发没有
    「失败」可言 —— 要说的是替换发生了。（同一取舍的先例是 charset 那一族：一个新的谎
    不换一句旧的含糊。）
  * 没人点名时照旧：gb18030 干净解开 → `encoding_basis=guessed` 与原句 hedge。
  * 「猜没猜 / 谁说的」这个判断只有**一个作者**（`charset.encoding_basis`），
    搬运是 `parse._decode_body` → `server.doc_parse` → `quality._agent_hints`。
"""

from __future__ import annotations

import pathlib

import pytest

from dhole_mcp import charset as charset_mod
from dhole_mcp.server import MasterFetchServer

KO_TEXT = "한글 문서를 여는 방법: 인코딩과 변환 순서"
ZH_TEXT = "第 3 段讲的是同一个专题的进展，句式与用词都逐段不同。"


def _write(tmp_path: pathlib.Path, name: str, body: bytes) -> str:
    p = tmp_path / name
    p.write_bytes(body)
    return str(p)


async def _parse(tmp_path, name, body, **kw):
    path = _write(tmp_path, name, body)
    return await MasterFetchServer().doc_parse(file_path=path, **kw)


class TestTheNamedCharsetAndTheOneThatRan:

    @pytest.mark.asyncio
    async def test_a_name_that_could_not_decode_is_reported_as_a_fallback(
            self, tmp_path):
        r = await _parse(tmp_path, "korean-euckr.txt", KO_TEXT.encode("euc-kr"),
                         encoding="shift_jis")
        md = r.metadata or {}
        assert md.get("encoding_asked") == "shift_jis", (
            f"回执里没有「你点的那一颗」：{md}")
        assert md.get("encoding") != "shift_jis", (
            f"点名的那颗其实没在跑，却仍按它交差：{md}")
        assert md.get("encoding_basis") == "fallback", (
            f"回落被当成了声明：{md}")
        assert "shift_jis" in (r.next_action or "") and (md.get("encoding") or "") in (
            r.next_action or ""), (
            f"那句话没有点名两颗名字：{r.next_action!r}")

    @pytest.mark.asyncio
    async def test_a_second_shape_with_the_same_fallback_big5_on_gbk(self, tmp_path):
        """P3 的第二例：GBK 件点名 big5，而那串字节**解不开** big5。

        （写这一格时先撞了一次反例：`ZH_TEXT.encode('gbk')` 那串字节是解得开 big5 的，
        于是它是另一件事 —— 见下面那一发 `names_a_charset_that_did_decode`。同名而解得开
        = 调用方领走这次读数；点名而解不开 = 检测顶上，必须申报。）
        """
        body = ("这台设备的电气参数见表 4（附录 B），另有三处标注为「待确认」。"
                .encode("gbk"))
        with pytest.raises(UnicodeDecodeError):
            body.decode("big5")   # 前提：这颗名字在这串字节上真的解不开
        r = await _parse(tmp_path, "gbk-big5.txt", body, encoding="big5")
        md = r.metadata or {}
        assert md.get("encoding_asked") == "big5"
        assert md.get("encoding_basis") == "fallback", f"这一发同样静默回落：{md}"
        assert "big5" in (r.next_action or ""), r.next_action

    @pytest.mark.asyncio
    async def test_names_a_charset_that_did_decode_is_the_callers_own_reading(self,
                                                                              tmp_path):
        """反面锁：点了名而且它解得开 —— 正文就是那颗读出来的，一个字都不许换。

        R02-F8 之后这一格多了一句"这批字节还允许第二种干净的读法"（`encoding_basis=
        contested`），但**不**换正文、**不**翻判词：把一次按吩咐的读取报成失败，换来的
        只是调用方从此不信这些字段（R01 起写着的取舍，本刀继续守）。这一格守的就是这条。
        """
        body = ZH_TEXT.encode("gbk")
        body.decode("big5")   # 前提：这颗名字解得开（与上一发只有这一点不同）
        r = await _parse(tmp_path, "gbk-named-big5.txt", body, encoding="big5")
        md = r.metadata or {}
        assert md.get("encoding") == md.get("encoding_asked") == "big5", md
        assert body.decode("big5")[:6] in "\n".join(r.content), (
            f"点名的那颗被换掉了：{r.content}")
        assert ZH_TEXT not in "\n".join(r.content), (
            "没点名的那颗反而跑了 —— 那是静默替换，不是交代")
        assert md.get("encoding_basis") == "contested", (
            f"相容被写成了判定，一句交代都没有：{md}")

    @pytest.mark.asyncio
    async def test_a_name_that_decoded_is_a_verdict_and_says_no_more(self, tmp_path):
        """反面锁：点中了就不许多一句「这是猜的」（R01 那批契约的原样）。"""
        r = await _parse(tmp_path, "korean-right.txt", KO_TEXT.encode("euc-kr"),
                         encoding="euc-kr")
        md = r.metadata or {}
        assert md.get("encoding") == "euc-kr", md
        assert md.get("encoding_asked") == "euc-kr", md
        assert md.get("encoding_basis") != "guessed", (
            f"跑的就是吩咐的那一颗，却被叫成猜：{md}")
        assert "guess" not in (r.next_action or "").lower(), r.next_action
        assert KO_TEXT in "\n".join(r.content), "解药那一发正文得是原语言"

    @pytest.mark.asyncio
    async def test_no_declaration_keeps_the_old_guess_wording(self, tmp_path):
        """没人点名时那一支一字不动：gb18030 干净解开 → basis=guessed + 原句 hedge。"""
        r = await _parse(tmp_path, "sjis-nobody-named.txt", KO_TEXT.encode("euc-kr"))
        md = r.metadata or {}
        assert md.get("encoding_basis") == "guessed", md
        assert "encoding_asked" not in md, "没人点名，就不要凭空写一颗被点名的"

    @pytest.mark.asyncio
    async def test_the_fallback_never_doubles_as_a_failure(self, tmp_path):
        """这一发没有「失败」可言：正文照样交回，error 不写，content_ok 不翻假。"""
        r = await _parse(tmp_path, "fallback-not-an-error.txt", KO_TEXT.encode("euc-kr"),
                         encoding="shift_jis")
        assert not r.error, f"回落被报成了错误：{r.error!r}"
        assert r.content_ok is True, (
            "把一次替换读数报成失败，换来的只是调用方从此不信 content_ok")
        assert "\n".join(r.content), "正文还是要给的 —— 那正是他要拿去看的字"

    def test_the_ruler_has_one_author_and_a_total_truth_table(self):
        """`encoding_basis` 是这一族问题唯一的作者（返回值：declared/contested/fallback/guessed/''）。

        第五颗值是 R02-037 加的那一格：点名的解得开、而这批字节另有第二种干净读法。
        `declared` 仍然有它的例子 —— 两种读法给出**同一份文本**时（GBK 件点名 gbk，自动
        那颗走 gb18030 而文本逐字相同），没什么好交代的。
        """
        body = KO_TEXT.encode("euc-kr")
        assert charset_mod.encoding_basis(body, "euc-kr", "euc-kr") == "contested"
        assert charset_mod.encoding_basis(body, "shift_jis",
                                          "gb18030") == "fallback"
        assert charset_mod.encoding_basis(body, "", "gb18030") == "guessed"
        assert charset_mod.encoding_basis(
            KO_TEXT.encode("utf-8"), "", "utf-8") == ""
        assert charset_mod.encoding_basis(
            KO_TEXT.encode("utf-16"), "", "utf-16") == ""
        gbk_body = ZH_TEXT.encode("gbk")
        assert charset_mod.encoding_basis(gbk_body, "gbk", "gbk") == "declared"
        assert charset_mod.encoding_basis(
            gbk_body, "big5", "big5") == "contested"

    def test_the_two_names_are_the_same_name_when_they_are_the_same_charset(self):
        """名字要归一化再比：`GBK` 与 `gbk`、`utf-8` 与 `utf_8` 不是两颗字符集。"""
        body = ZH_TEXT.encode("gbk")
        for asked in ("gbk", "GBK", " gbk "):
            assert charset_mod.encoding_basis(body, asked, "gbk") == "declared", asked
