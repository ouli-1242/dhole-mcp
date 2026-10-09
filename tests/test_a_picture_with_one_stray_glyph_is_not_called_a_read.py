# -*- coding: utf-8 -*-
"""一张没有文本层的图片，不许因为 OCR 蒙出一个字符就自称读到了（D-38）

现场（2026-10-09，连着的那台服务器，`web_read("https://httpbin.org/image/png")`，两轮
——http 与 stealthy 逐字节同值）：

    content            : "o"          <-- 一个字符
    error              : ""
    diagnosis          : (缺席)
    summary            : "200 OK · 1B text · stealthy"
    content_ok         : true

`image_ocr_empty` 那一格只在 OCR **一个字都没认出**时才亮，所以它管不到这一发：引擎认出了
一个字符，而 `if text:` 把"认出东西"当成"读到了正文"。结果是调用方拿到一份自称成功的读取，
并被教过"照 content 引用这页的话"—— 引用的是噪声。

样本轮（`.scratch/liveverify/d38_ocr_samples.py`，同一台机器、同一个 RapidOCR）：

    纯噪声 101KB        -> ''                  (0 字符)
    渐变+几何 1.9KB      -> ''                  (0)
    纯白 389B           -> ''                  (0)
    真照片 JPEG 35.6KB  -> ''                  (0)
    同一张照片的 PNG 8KB -> 'o'                 (1)  <-- 就是线上那一发
    一个孤零零的数字 "7" -> ''                  (0)
    64px 一行大字        -> 'DHOLE SAMPLE LINE' (15 个字母数字)
    14px 一行小字        -> 'DHOLE SAMPLE LINE 2' (16)

所以空档在 1~2 与 15 之间，而"一个字符"不是这条线的唯一形状。门槛取 **少于 4 个字母数字
（含中日韩字）才算薄读**：STOP / OK 那一类整词仍然算读到（那是图里真有的词），一个孤零零的
`o` 不算。数字落在空档里，不是随手取的。

**一个字符都不删**：那几个字符仍然交在 `content` 里。这一刀改的是说法 —— 回执必须说"这图
没有值得读的文本层"，而不是安静地交出噪声。删数据是另一种说谎，而且拿不回来。
"""
from __future__ import annotations

import pytest

from dhole_mcp.fetcher import Response
from dhole_mcp.quality import classify_error_text
from dhole_mcp.server import _ocr_thin_read, _translate_response_body

PNG_BODY = b"\x89PNG\r\n\x1a\n" + b"\x00" * 60


def _page(body: bytes = PNG_BODY, content_type: str = "image/png") -> Response:
    return Response(url="https://img.test/pic", body=body, status=200,
                    headers={"content-type": content_type}, encoding="utf-8")


@pytest.fixture
def fake_ocr(monkeypatch):
    """把引擎交回什么变成剧本 —— 判据要量的是**回执怎么说**，不是引擎的眼力。"""
    import dhole_mcp.ocr as ocr

    def _set(text: str):
        monkeypatch.setattr(ocr, "ocr_available", lambda: True)
        monkeypatch.setattr(ocr, "ocr_image_bytes", lambda *a, **k: text)
        return text
    return _set


class TestTheThinReadIsNamed:

    def test_a_stray_glyph_is_delivered_but_called_what_it_is(self, fake_ocr):
        fake_ocr("o")
        result = _apply(_translate_response_body(_page(), "markdown", None,
                                                 fetcher_used="stealthy"))
        assert result.content == ["o"], "一个字符都不许多删掉"
        assert result.error.startswith("image_ocr_thin:"), result.error
        assert "1 readable character" in result.error
        assert result.content_ok is False
        assert result.extracted_type == "text"

    def test_the_machine_readable_twin_exists_for_this_shape(self, fake_ocr):
        fake_ocr("o")
        result = _translate_response_body(_page(), "markdown", None)
        _apply(result)
        diag = result.diagnosis or {}
        assert diag.get("code") == "image_ocr_thin", diag
        assert diag.get("retryable") is False, (
            "原样重发同一调用不会把那个字符变成别的 —— 不许说 retryable")
        assert "vision" in (diag.get("recover_with") or ""), diag

    def test_the_advice_says_noise_not_no_text(self, fake_ocr):
        fake_ocr("o")
        result = _translate_response_body(_page(), "markdown", None)
        _apply(result)
        # 与"一个字都没认出"那一句不许互换：那一句在这发上是假话（content 里明明有东西）。
        assert "OCR noise, not a quote" in result.next_action, result.next_action
        assert "no text could be read" not in result.next_action

    def test_a_whole_word_is_still_a_read(self, fake_ocr):
        """反向锁：门槛不许把"图里真有一个词"那一类打成噪声。"""
        fake_ocr("STOP")
        result = _apply(_translate_response_body(_page(), "markdown", None))
        assert result.error == "", result.error
        assert result.content == ["STOP"]
        assert result.content_ok is True

    def test_four_cjk_characters_are_a_read_but_two_are_not(self, fake_ocr):
        fake_ocr("你好世界")
        assert _translate_response_body(_page(), "markdown", None).error == ""
        fake_ocr("你好")
        thin = _translate_response_body(_page(), "markdown", None)
        assert thin.error.startswith("image_ocr_thin:"), thin.error
        assert "2 readable character" in thin.error

    def test_nothing_recognised_keeps_its_own_sentence(self, fake_ocr):
        """空手那一支一字未动 —— 这一刀是补话，不是把两支并成一支。"""
        fake_ocr("")
        result = _apply(_translate_response_body(_page(), "markdown", None))
        assert result.error.startswith("image_ocr_empty:"), result.error
        assert result.content == []
        assert result.next_action.startswith("no text could be read"), result.next_action

    def test_the_absent_engine_branch_is_untouched(self, monkeypatch):
        import dhole_mcp.ocr as ocr
        monkeypatch.setattr(ocr, "ocr_available", lambda: False)
        result = _translate_response_body(_page(), "markdown", None)
        assert result.error.startswith("image_ocr_unavailable:"), result.error
        assert result.content == []


class TestTheThresholdIsTheMeasuredGap:
    """门槛住在实测的空档里：0/1/2 算薄，4 及以上算读到东西。"""

    @pytest.mark.parametrize("text,thin", [
        ("", 0),                      # 空手另有一支说话
        ("o", 1), ("OK", 2), ("7 ", 1), ("你好", 2),
        ("STOP", 0), ("你好世界", 0), ("DHOLE SAMPLE LINE", 0), ("id 42", 0),
    ], ids=["empty", "stray-o", "OK", "digit", "two-cjk", "word", "four-cjk",
            "line", "four-alnum"])
    def test_the_band(self, text, thin):
        assert _ocr_thin_read(text) == thin, repr(text)

    def test_the_classify_layer_names_it(self):
        code, retryable, recover = classify_error_text(
            "image_ocr_thin: OCR found only 1 readable character(s) in this image")
        assert code == "image_ocr_thin"
        assert retryable is False
        assert "vision" in recover


class TestTheRealEngineKeepsItsPromise:
    """真引擎一发（不 monkeypatch）：没字的图上不许出现"有正文却什么都没说"。"""

    def test_a_generated_noise_picture_is_never_a_clean_read(self):
        from dhole_mcp.ocr import ocr_available
        if not ocr_available():
            pytest.skip("OCR extras not installed on this machine")
        blob = _noise_png()
        result = _translate_response_body(_page(blob, "image/png"), "markdown", None)
        delivered = "".join(result.content)
        if delivered and not result.error:
            # 只有真读出这么多字才许闭嘴；D-38 之前那一发是 1 字符 + error 空。
            assert _ocr_thin_read(delivered) == 0, (repr(delivered), result.error)
        else:
            assert result.error, "读出东西却没说话，正是这一刀要修的形状"


def _noise_png() -> bytes:
    import io
    import random

    from PIL import Image
    rnd = random.Random(7)
    img = Image.new("RGB", (240, 140))
    img.putdata([(rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255))
                 for _ in range(240 * 140)])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _apply(result):
    """把信封与给 agent 的那三句补全 —— 生产里这一步是 `_with_agent_hints`（每一个返回都过它），
    `_translate_response_body` 自己只写产地已知的那些格。"""
    from dhole_mcp.quality import _with_agent_hints
    return _with_agent_hints(result)
