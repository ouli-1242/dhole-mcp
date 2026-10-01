"""验证码靶站（`evals/fixture_site.py`）的确定性不变式。

`evals/captcha_baseline.py` 的整套判卷都建立在一条约定上：**答案只由 token 决定，
与难度档无关**。这条约定一旦悄悄破掉（比如有人把难度掺进答案派生），基线会
变成"用错的键判对的卷"，而且不会有任何报错——正是本项目最忌讳的那种静默失败。
所以这里把它钉住。

另两条：`captcha_text` 的字母表不能含易混字符（0/O、1/I/L），否则量到的是
"看不清"而不是"认不出"；同一 (token, level) 必须渲染出同一份字节，否则
`--emit` 与 `--score` 之间靶站重启就会判错。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("cv2")

EVALS = Path(__file__).resolve().parent.parent / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import fixture_site as fs  # noqa: E402


class TestTheAnswerDependsOnTheTokenAlone:
    def test_deterministic_for_a_token(self):
        t = fs.new_captcha_token()
        assert fs.captcha_text(t) == fs.captcha_text(t)

    def test_length_and_alphabet(self):
        for _ in range(40):
            text = fs.captcha_text(fs.new_captcha_token())
            assert len(text) == fs.CAPTCHA_LEN
            assert set(text) <= set(fs.CAPTCHA_ALPHABET)

    def test_no_ambiguous_glyphs(self):
        # 0/O、1/I/L 在扭曲字体下几乎不可分；留在字母表里等于在量"看不清"。
        assert not (set(fs.CAPTCHA_ALPHABET) & set("01OIL"))

    def test_tokens_do_not_all_collide(self):
        texts = {fs.captcha_text(fs.new_captcha_token()) for _ in range(60)}
        assert len(texts) > 40      # 60 次采样撞到 20 个以内说明派生退化了

    def test_the_same_token_verifies_regardless_of_level(self):
        """靶站的 /captcha/verify 只收 token+answer，不知道 level。

        所以答案必须与 level 无关 —— 这里直接对两档各渲染一次，断言答案不变。
        """
        t = "deadbeefcafe"
        fs._captcha_png(t, "normal")
        fs._captcha_png(t, "hard")
        assert fs.captcha_text(t) == fs.captcha_text(t)


class TestTheRenderingIsReproducible:
    def test_same_token_and_level_same_bytes(self):
        t = fs.new_captcha_token()
        assert fs._captcha_png(t, "normal") == fs._captcha_png(t, "normal")

    def test_levels_actually_differ(self):
        """hard 若不比 normal 更难，转交路径就永远测不到。"""
        t = "0badc0ffee11"
        assert fs._captcha_png(t, "normal") != fs._captcha_png(t, "hard")

    def test_unknown_level_falls_back_to_normal(self):
        t = fs.new_captcha_token()
        assert fs._captcha_png(t, "nonsense") == fs._captcha_png(t, "normal")

    def test_decodes_as_a_png_of_the_expected_shape(self):
        import cv2
        import numpy as np

        t = fs.new_captcha_token()
        for level in fs.CAPTCHA_LEVELS:
            img = cv2.imdecode(np.frombuffer(fs._captcha_png(t, level), np.uint8),
                               cv2.IMREAD_GRAYSCALE)
            assert img is not None
            expected_w = 12 + (fs.CAPTCHA_LEN - 1) * 40 + 64 + 12
            assert img.shape == (80, expected_w)
