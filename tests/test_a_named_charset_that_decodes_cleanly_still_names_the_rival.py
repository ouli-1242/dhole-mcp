"""点名的字符集**严格解得开**这串字节，回执也不许把这份读数写成判定 —— 字节还允许另一份。

现场（R02-037 · P4 复验 R02-F3 时撞出来的第二形，与 R02-021 同一条链的另一半）：

    doc_parse(file_path='…/railway-gbk.txt', encoding='big5')      # 这份文件其实是 GBK
      → metadata = {"encoding": "big5", "encoding_asked": "big5"}  ← 没有 encoding_basis
      → content_ok = true、next_action = ""（空）
      → content[0] = 詩寢艦瘧婓盄潰聆ㄩ疏倛艦瘧迵艦瘧醱儅數呾ㄛ獗桶…  ← 明显的错语言

量过的四个读数（本刀的实现前探针，逐字记在 fix-R02-F8.md）：这一发的
`decode_damage` = 0、`misdecode_score` = 0、`charset_is_in_doubt` = False —— 也就是说
仓库里那三把现成的尺子**全都看不见**这一格：big5 严格解得开 GBK 的字节，解出来的每个字
都是 big5 自己表里的合法字符（连那几枚注音符号也是）。所以这不是"检查没跑"，而是
"按吩咐读出来的东西，回执没有任何一句话说明它是按吩咐读的、而不是量出来的"。

同一颗文件不给编码那一发反而是诚实的：`encoding_basis=guessed` + 那句"这一个是 dhole
选的"（R01 起）。差别只在一件事上 —— 点了名，`encoding_basis` 就返回 `declared`，而那
一颗的 docstring 写着「这是判定，没什么好交代的」。**这一句是假的**：调用方的名字与这串
字节相容，不等于这串字节属于那个名字。同一批字节 `gb18030` 也严格解得开，而两份读数逐字
不同（一份是错语言，一份是「钢轨磨耗在线检测」）—— dhole 手上没有任何便宜的判别器说哪份
对（这正是 `guessed` 那一支已经承认过的事实），所以它唯一能诚实交代的是：**另一份读数也存
在，名字是它**。

契约（本文件钉住）：
  * 点名的那颗解得开 → 正文**仍按点名的那颗交**（一个字的替换都不做 —— R02-021 那一族
    最坏的做法就是静默换一颗名字）；`encoding == encoding_asked` 一字不动。
  * 但 `encoding_basis` 在这一格不再缺席：当同一批字节还有另一颗名字也**严格解得开、
    那份读数自身无可疑、而文本与点名的这一份不同**时，`encoding_basis=contested`，
    `next_action` 说出**两个名字**（点名的那颗 + 竞争的那颗），并给出可执行的解药
    （"不是你要的语言就改点名那一颗"）。
  * 反面锁（三种不许报的情况，防止它成为噪声）：
      - 两份读数**逐字相同**（GBK 件点名 gbk：自动那颗 gb18030 给出同一份文本）→ `declared`；
      - 竞争那份自己就可疑（真 big5 件被 gb18030 读成一堆 PUA）→ `declared`；
      - 严格解不开、由检测顶上的那一支照旧是 `fallback`，本文件一个字不碰。
  * `content_ok` 不翻假、`error` 不写：把一次按吩咐的读取报成失败，换来的只是调用方从此
    不信这个字段（R01 起写着的取舍，本刀继续守）。
  * 「这一份是按谁读的」仍然只有**一个作者**（`charset.encoding_basis`），搬运是
    `parse._decode_body` → `server.doc_parse` → `quality._agent_hints`。
  * 那句话里不许出现 "guess"：点名的这一颗不是猜的，它跑的就是吩咐的那一颗；要说的是
    **相容不是测量**。（这一条把 R02-F3 那批反面锁的职责原样保住。）
"""

from __future__ import annotations

import pathlib

import pytest

from dhole_mcp import charset as charset_mod
from dhole_mcp.server import MasterFetchServer

KO_TEXT = "한글 문서를 여는 방법: 인코딩과 변환 순서"
ZH_GBK = "钢轨磨耗在线检测：波形磨耗与磨耗面积计算，见表 4（附录）。"
ZH_BIG5 = "鋼軌磨耗在線檢測：波形磨耗與磨耗面積計算，見表四（附錄）。"


def _write(tmp_path: pathlib.Path, name: str, body: bytes) -> str:
    p = tmp_path / name
    p.write_bytes(body)
    return str(p)


async def _parse(tmp_path, name, body, **kw):
    path = _write(tmp_path, name, body)
    return await MasterFetchServer().doc_parse(file_path=path, **kw)


# ─── 1 · 那位作者：相容不等于测量 ───


class TestTheRuler:

    def test_a_clean_rival_reading_contests_the_named_one(self):
        """现场那一格：GBK 字节点名 big5，两份读数都干净而文本不同。"""
        body = ZH_GBK.encode("gbk")
        body.decode("big5")                      # 前提：点名的这颗确实解得开
        assert charset_mod.encoding_basis(body, "big5", "big5") == "contested"

    def test_identical_readings_stay_a_verdict(self):
        """GBK 件点名 gbk：自动那颗 gb18030 给出**同一份文本** —— 没什么好交代的。"""
        body = ZH_GBK.encode("gbk")
        assert charset_mod.decode_file_bytes(body, "")[1] == "gb18030"
        assert charset_mod.decode_file_bytes(body, "gbk")[0] \
            == charset_mod.decode_file_bytes(body, "")[0]
        assert charset_mod.encoding_basis(body, "gbk", "gbk") == "declared"

    def test_a_rival_that_is_itself_in_doubt_does_not_contest(self):
        """真 big5 件：gb18030 那份读成一堆 PUA（damage 14、在疑），它没有资格竞争。"""
        body = ZH_BIG5.encode("big5")
        rival, rival_used, rival_damage = charset_mod.decode_file_bytes(body, "")
        assert (rival_used, rival_damage) == ("gb18030", 14), (
            f"这一格的前提是竞争者自身可疑：{rival_used}/{rival_damage}")
        assert charset_mod.charset_is_in_doubt(rival)
        assert charset_mod.encoding_basis(body, "big5", "big5") == "declared"

    def test_utf8_and_ascii_names_stay_quiet(self):
        body = ZH_GBK.encode("utf-8")
        assert charset_mod.encoding_basis(body, "utf-8", "utf-8") == "declared"
        plain = b"plain ascii text only"
        assert charset_mod.encoding_basis(plain, "cp1252", "cp1252") == "declared"

    def test_the_fallback_branch_is_untouched(self):
        """点名的解不开、由检测顶上那一支（R02-021）一字不改。"""
        body = KO_TEXT.encode("euc-kr")
        assert charset_mod.encoding_basis(body, "shift_jis", "gb18030") == "fallback"
        assert charset_mod.encoding_basis(body, "", "gb18030") == "guessed"


# ─── 2 · 端到端：两个名字都要在场，正文不许换 ───


class TestTheReceipt:

    @pytest.mark.asyncio
    async def test_the_named_reading_is_still_the_one_that_ships(self, tmp_path):
        """反面锁里最硬的一条：这一刀不搬正文，只说话。"""
        r = await _parse(tmp_path, "gbk-named-big5.txt", ZH_GBK.encode("gbk"),
                         encoding="big5")
        md = r.metadata or {}
        assert md.get("encoding") == md.get("encoding_asked") == "big5", md
        body_text = "\n".join(r.content)
        assert ZH_BIG5[:6] not in body_text          # 没有偷偷改按 gb 读
        assert "詩" in body_text, (
            f"点名的那颗没在跑：{body_text[:60]}")

    @pytest.mark.asyncio
    async def test_the_receipt_names_both_charsets(self, tmp_path):
        r = await _parse(tmp_path, "gbk-named-big5.txt", ZH_GBK.encode("gbk"),
                         encoding="big5")
        md = r.metadata or {}
        assert md.get("encoding_basis") == "contested", (
            f"相容被写成了判定：{md}")
        na = r.next_action or ""
        assert "big5" in na and "gb18030" in na, (
            f"那句话没有点名两颗名字：{na!r}")
        assert "guess" not in na.lower(), (
            f"按吩咐读的那一发被叫成猜：{na!r}")

    @pytest.mark.asyncio
    async def test_this_is_not_reported_as_a_failure(self, tmp_path):
        r = await _parse(tmp_path, "gbk-named-big5.txt", ZH_GBK.encode("gbk"),
                         encoding="big5")
        assert not r.error, f"一次按吩咐的读取被报成失败：{r.error!r}"
        assert r.content_ok is True, "见上一条的取舍：不换一个新谎进来"

    @pytest.mark.asyncio
    async def test_a_correct_name_with_a_clean_rival_says_the_same_kind_of_thing(
            self, tmp_path):
        """韩文件点名 euc-kr（点**对**了）：竞争者 gb18030 也干净，句子照说。

        这一格不是误报而是同一件事：dhole 确实说不出哪份对。它守的是 F3 那批反面锁的
        职责（正文是原语言、不叫"猜"、content_ok 不动），改的只有 basis 那一颗。
        """
        r = await _parse(tmp_path, "korean-right.txt", KO_TEXT.encode("euc-kr"),
                         encoding="euc-kr")
        md = r.metadata or {}
        assert md.get("encoding_basis") == "contested", md
        assert KO_TEXT in "\n".join(r.content), "解药那一发正文得是原语言"
        assert "guess" not in (r.next_action or "").lower(), r.next_action

    @pytest.mark.asyncio
    async def test_a_name_agreeing_with_the_rival_still_says_nothing(self, tmp_path):
        """两份读数同一份文本时，句子必须不出现（不许把这格做成噪声）。"""
        r = await _parse(tmp_path, "gbk-named-gbk.txt", ZH_GBK.encode("gbk"),
                         encoding="gbk")
        md = r.metadata or {}
        assert "encoding_basis" not in md, f"两份读数逐字相同却仍开口：{md}"
        assert "contested" not in (r.next_action or "")


# ─── 3 · 一位作者与手册 ───


def test_the_new_value_has_one_author():
    import inspect

    src = inspect.getsource(charset_mod)
    assert src.count('"contested"') == 1, "判据只有 `encoding_basis` 一位作者"


def test_the_manual_carries_the_encoding_basis_vocabulary():
    """回执要发第四颗值，手册就得把这一族的四颗值一次说清（它今天一个字也没有）。"""
    import inspect
    from pathlib import Path

    from dhole_mcp import quality as quality_mod
    doc = (Path(inspect.getsourcefile(quality_mod)).resolve().parent
           / "skills" / "dhole-web" / "references" / "fields.md"
           ).read_text(encoding="utf-8")
    for value in ("declared", "fallback", "guessed", "contested"):
        assert f"`{value}`" in doc, f"手册里没有 encoding_basis 的这颗值：{value}"
    assert "encoding_basis" in doc, "手册里没有这颗字段"
