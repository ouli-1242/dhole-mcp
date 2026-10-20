"""批次 6e：一次猜出来的编码不许长得像一次测量。

2026-10-05 实测：`doc_parse` 读一个 Shift_JIS 的 `sjis.csv` 与一个 EUC-KR 的
`euckr.html`，两者都被判成 `gb18030`、正文是流式乱码（`名前|都市` → `柤慜|搒巗`、
`한국어 텍스트입니다` → `茄惫绢 咆胶飘涝聪促`），而 `content_ok=true`、`error=""`、
没有一个字的警告。显式传 `encoding='shift_jis'` / `encoding='euc-kr'` 立刻拿到正确的
`名前|都市` / `한국어 텍스트입니다` —— 解药存在，但它要求调用方预先知道答案，而那正是
检测器本该给出的东西。

`charset.py:116-120` 把这件事记成**实测过的已知边界**：GB18030 是 GBK/GB2312 的超集，
而且严格解得开 Shift_JIS 与 EUC-KR 的字节，出来的全是合法汉字 —— damage 0、无
替换符、无 PUA，所以**便宜的判别器不存在**（本机复测三条样本的 damage 全为 0）。

于是能诚实说出口的只有一处，而且必须精确停在那一处：这个名字是**猜的**（没有人声明
编码、没有 BOM 说明、胜出的是严格 gb18030 那一支）。不是"这一次猜错了"——我们没有
说出后半句的权利。`encoding=` 传进去，猜就变成判定。

不翻 `content_ok`：那会把一次**正确的**中文读取报成失败（一份 GBK 文件同样满足"没人
声明过编码"），而调用方从此开始不信这个字段。用一个新的谎换掉一句旧的含糊，是这个
仓库反复拒绝的那种修法。先例是 `_ignored_selector_note`：一条从 metadata 来的、只
进 summary/next_action、不动判词的说明。

判据：

1. `encoding_basis` 的真值表 —— gb18030 + 无人声明 + 无 BOM + 头部无 NUL 才
   为真；声明过、BOM 说过、UTF-8 解开的、头部有 NUL 的都别声称"这是猜的"。
2. `doc_parse` 在 SJIS/EUC-KR 样本上：`metadata.encoding_basis == "guessed"`、
   `next_action` 说出这是猜测并给出 `encoding=` 与四个候选名，而 `content_ok` **不
   被翻假**、`error` 仍为空。
3. 解药真的能走通：同一颗文件带 `encoding=` 重读，正文是原语言，且 `encoding_basis`
   这个键**不出现**（声明即判定，没什么好交代的）。
4. 一次正确的 GBK 读取同样被标为猜测 —— 这条把"我说出口的那半句"钉在"没人声明"
   而非"我怀疑读错了"；措辞里不许出现指责。
5. 不需要说话的时候一个字都不说：UTF-8、带 BOM、以及已经报 `encoding_undecodable`
   的那一支（错误已经点名编码了）。
6. 这条链只有一个作者：判词在 `charset.encoding_basis`，搬运会是
   `parse._decode_body` → `server.doc_parse` → `quality._agent_hints`，中间不许长出
   第二个决定"猜没猜"的地方。
"""

from __future__ import annotations

import asyncio
import pathlib

from dhole_mcp import charset as charset_mod
from dhole_mcp.server import MasterFetchServer

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
