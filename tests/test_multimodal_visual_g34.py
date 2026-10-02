"""G34 → 17.0：元素级截图 + 本地二维码解码，**常开**（无 DHOLE_MULTIMODAL 门）。

大众化一刀切把"调用方声明自己是多模态的"这层许可删了 —— 元素级截图
（screenshot 的 options.selector、web_read 的 {"screenshot"} 动作）和工具描述
里的试读纪律默认全量在线；dhole 不引入视觉模型，识别永远在 agent 侧。这里各守各的：

* **无门控语义**：任何 env 状态下动作与工具都能走通；曾经读 env 的入口
  （actions.multimodal_enabled / server._multimodal_enabled）必须不存在。
* **二维码解码**：确定性工作（opencv 本地解码），不是视觉任务（decode_qr 走
  文本通道）；解码失败永远不连坐截图本身。
* **像素走内容列表、元数据走收据**：base64 进 JSON payload 会摊到每一个
  transcript / cache 副本上，所以 metadata.actions 只留名字级收据（image: true
  + 解码出的 QR 文本），像素单独骑 ImageContent。
"""

from __future__ import annotations

import base64

import pytest

from dhole_mcp.actions import (
    MAX_ACTION_SHOT_BYTES,
    _validate_actions,
    build_page_action,
)
from dhole_mcp.server import (
    MasterFetchServer,
    ResponseModel,
    _report_action_outcomes,
    _wire_result,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 64

URL = "https://example.com/login"


def _qr_png(payload: str) -> bytes:
    """Encode a payload into a PNG QR (needs the opencv extra; callers skip)."""
    import cv2

    encoder = cv2.QRCodeEncoder_create()
    ok, buf = cv2.imencode(".png", encoder.encode(payload))
    assert ok
    return buf.tobytes()


class _FakeLocator:
    def __init__(self, sel: str, data: bytes = PNG_BYTES, fail: bool = False):
        self._sel, self._data, self._fail = sel, data, fail

    @property
    def first(self):
        return self

    async def count(self) -> int:
        # 真的 Locator 有 count()。元素裁切路径现在先查零匹配再截图（缺陷 5：
        # `.first` 对零匹配的 locator 会白等满 timeout，然后把「元素不在 DOM 里」
        # 说成「页面没渲染完」），所以替身也得有。这里的 fail 语义是「元素在、
        # 截图超时」，因此计数为 1。
        return 1

    async def screenshot(self, **kwargs):
        if self._fail:
            raise TimeoutError(f"waiting for locator({self._sel!r})")
        return self._data

    async def fill(self, *a, **kw):
        raise AssertionError("fill should not be part of a capture test")


class _FakePage:
    url = URL

    def __init__(self, data: bytes = PNG_BYTES, fail: bool = False):
        self._data, self._fail = data, fail

    def locator(self, sel: str):
        return _FakeLocator(sel, self._data, self._fail)

    async def screenshot(self, **kwargs):
        if self._fail:
            raise TimeoutError("waiting for the page")
        return self._data

    async def wait_for_timeout(self, ms):
        pass


class _FakeSession:
    def __init__(self, page: _FakePage):
        self.page = page

    async def fetch(self, url, **kwargs):
        action = kwargs.get("page_action")
        if action is not None:
            await action(self.page)


# ─── 无门控：动作在任何 env 状态下都能通过校验，读 env 的入口已删 ──────────


class TestNoActionGate:

    @pytest.mark.parametrize("raw", [None, "0", "1", "flase", "yes-please"])
    def test_the_action_passes_regardless_of_env(self, monkeypatch, raw):
        """17.0 起这个能力不在任何门后面 —— 连旧旋钮的残留值也不改变行为
        （注销的旋钮运行时静默忽略）。"""
        import dhole_mcp.actions as actions_mod
        if raw is None:
            monkeypatch.delenv("DHOLE_MULTIMODAL", raising=False)
        else:
            monkeypatch.setenv("DHOLE_MULTIMODAL", raw)
        assert not hasattr(actions_mod, "multimodal_enabled")
        assert build_page_action([{"screenshot": "img.captcha"}]) is not None

    def test_the_dict_form_normalizes_selector_and_decode_qr(self):
        (a,) = _validate_actions([{"screenshot": {"selector": "img.qr",
                                                  "decode_qr": True}}])
        assert a == {"screenshot": {"selector": "img.qr", "decode_qr": True}}
        (b,) = _validate_actions([{"screenshot": "img.qr"}])
        assert b == {"screenshot": {"selector": "img.qr", "decode_qr": False}}

    @pytest.mark.parametrize("bad", [
        {"screenshot": {"selector": "img.qr", "decode_qr": "yes"}},
        {"screenshot": {"decode_qr": True}},
        {"screenshot": {"selector": ""}},
        {"screenshot": {"selector": None}},
        {"screenshot": 3},
    ])
    def test_bad_shapes_are_refused(self, bad):
        with pytest.raises((ValueError, Exception)) as e:
            build_page_action([bad])
        assert "element_screenshot_disabled" not in str(e.value)


# ─── 执行器：像素进行、收据齐、失败不连坐 ───────────────────────────────────


class TestTheCaptureExecutor:

    @pytest.mark.asyncio
    async def test_a_capture_rides_its_outcome_row(self, monkeypatch):
        pa = build_page_action([{"screenshot": "img.captcha"}])
        await pa(_FakePage())
        (row,) = pa.outcomes
        assert row["ok"] is True
        assert row["image_mime"] == "image/png"
        assert base64.b64decode(row["image_b64"]) == PNG_BYTES

    @pytest.mark.asyncio
    async def test_decode_qr_reports_the_payload_in_the_row(self, monkeypatch):
        pytest.importorskip("cv2")
        payload = "https://example.com/checkin?token=abc123"
        pa = build_page_action([{"screenshot": {"selector": "img.qr",
                                                "decode_qr": True}}])
        await pa(_FakePage(data=_qr_png(payload)))
        (row,) = pa.outcomes
        assert row["ok"] is True
        assert row["qr"] == [payload]

    @pytest.mark.asyncio
    async def test_a_decoder_problem_never_fails_the_capture(self, monkeypatch):
        """图是产品，解码是赠品：缺 opencv / 空白图都不能把动作标成失败。"""

        import dhole_mcp.qr as qr_mod

        def boom(data):
            raise ImportError("decode_qr requires OpenCV")
        monkeypatch.setattr(qr_mod, "decode_qr_bytes", boom)
        pa = build_page_action([{"screenshot": {"selector": "img.qr",
                                                "decode_qr": True}}])
        await pa(_FakePage())
        (row,) = pa.outcomes
        assert row["ok"] is True
        assert row["image_b64"]
        assert row["qr"] == []
        assert "ImportError" in row["qr_error"]

    @pytest.mark.asyncio
    async def test_an_oversized_capture_is_a_failed_action_not_a_dos(self,
                                                                     monkeypatch):
        big = b"\x89PNG\r\n\x1a\n" + b"0" * (MAX_ACTION_SHOT_BYTES + 1)
        pa = build_page_action([{"screenshot": "body"}])
        await pa(_FakePage(data=big))
        (row,) = pa.outcomes
        assert row["ok"] is False
        assert "image_b64" not in row, "超限的像素一个字节都不能出门"
        assert "bytes" in row["error"]

    @pytest.mark.asyncio
    async def test_a_failed_capture_does_not_abort_the_rest(self, monkeypatch):
        pa = build_page_action([{"wait": 1},
                                {"screenshot": "img.missing"},
                                {"wait": 1}])
        await pa(_FakePage(fail=True))
        ok_flags = [r["ok"] for r in pa.outcomes]
        assert ok_flags == [True, False, True]
        assert "locator" in pa.outcomes[1]["error"]


# ─── 收据与 wire：像素只走内容列表 ──────────────────────────────────────────


class TestTheReceiptAndTheWire:

    @staticmethod
    def _result_with_capture() -> ResponseModel:
        pa = build_page_action([{"wait": 1}])
        pa.outcomes.append({"action": "screenshot 'img.qr'", "ok": True,
                            "image_b64": base64.b64encode(PNG_BYTES).decode(),
                            "image_mime": "image/png",
                            "qr": ["https://example.com/t"]})
        result = ResponseModel(url=URL, status=200, content=["body"],
                               fetcher_used="http")
        _report_action_outcomes(result, pa)
        return result

    def test_metadata_keeps_a_marker_and_the_qr_text_not_the_bytes(self):
        result = self._result_with_capture()
        (row,) = result.metadata["actions"]
        assert row["image"] is True
        assert "image_b64" not in row, "base64 进 JSON 会摊到每个 transcript 副本"
        assert row["qr"] == ["https://example.com/t"]

    def test_the_pixels_reach_action_images_for_the_content_list(self):
        result = self._result_with_capture()
        (img,) = result._action_images
        assert img["mime_type"] == "image/png"
        assert base64.b64decode(img["data"]) == PNG_BYTES

    def test_wire_result_prepends_the_image_and_keeps_the_json_clean(self):
        result = self._result_with_capture()
        content, payload = _wire_result(result, images=result._action_images)
        assert content[0].type == "image"
        assert content[0].data == base64.b64encode(PNG_BYTES).decode()
        assert content[0].mime_type == "image/png"
        assert content[-1].type == "text"
        assert "image_b64" not in str(payload)
        assert payload["metadata"]["actions"][0]["image"] is True

    def test_wire_result_without_images_is_the_old_single_text_shape(self):
        result = ResponseModel(url=URL, status=200, content=["body"],
                               fetcher_used="http")
        content, payload = _wire_result(result)
        assert len(content) == 1
        assert content[0].type == "text"


# ─── screenshot 工具：无门，形状冲突仍然当场拒绝 ────────────────────────────


class TestTheToolGate:

    @pytest.mark.asyncio
    async def test_a_selector_crop_starts_no_browser_for_a_shape_error(
            self, monkeypatch):
        async def no_browser(*a, **kw):
            raise AssertionError("a browser was started for a shape refusal")
        monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session", no_browser)

        with pytest.raises(ValueError) as e:
            await MasterFetchServer().screenshot(URL, selector="img.captcha",
                                                 full_page=True)
        assert "full_page" in str(e.value)

    @pytest.mark.asyncio
    async def test_full_page_cannot_combine_with_a_selector(self, monkeypatch):
        with pytest.raises(ValueError) as e:
            await MasterFetchServer().screenshot(URL, selector="img.captcha",
                                                 full_page=True)
        assert "full_page" in str(e.value)

    @pytest.mark.asyncio
    async def test_decode_qr_runs_with_no_env_at_all(self, monkeypatch):
        """decode_qr 从来不是视觉任务：任何 env 状态下都走到浏览器层（假会话证明
        它没在入口就被拒）。"""

        class _Entry:
            session = _FakeSession(_FakePage())

        async def fake_session(*a, **kw):
            return "auto"

        async def fake_get(self, ssid, expected_type=None):
            return _Entry()

        monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session", fake_session)
        monkeypatch.setattr(MasterFetchServer, "_get_session", fake_get)
        monkeypatch.setattr("dhole_mcp.server._browser_deps_available", lambda: True)

        out = await MasterFetchServer().screenshot(URL, decode_qr=True)
        text = [c for c in out if getattr(c, "type", "") == "text"][0].text
        assert "decode_qr" in text


class TestTheToolCapture:

    @staticmethod
    def _patch_session(monkeypatch, page: _FakePage):
        class _Entry:
            session = _FakeSession(page)

        async def fake_session(*a, **kw):
            return "auto"

        async def fake_get(self, ssid, expected_type=None):
            return _Entry()

        monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session", fake_session)
        monkeypatch.setattr(MasterFetchServer, "_get_session", fake_get)
        monkeypatch.setattr("dhole_mcp.server._browser_deps_available", lambda: True)

    @pytest.mark.asyncio
    async def test_a_selector_crop_captures_the_element(self, monkeypatch):
        self._patch_session(monkeypatch, _FakePage())
        out = await MasterFetchServer().screenshot(URL, selector="img.captcha")
        image = [c for c in out if getattr(c, "type", "") == "image"][0]
        text = [c for c in out if getattr(c, "type", "") == "text"][0].text
        assert base64.b64decode(image.data) == PNG_BYTES
        assert image.mime_type == "image/png"
        assert "element crop: img.captcha" in text

    @pytest.mark.asyncio
    async def test_decode_qr_lands_in_the_text_part(self, monkeypatch):
        pytest.importorskip("cv2")
        payload = "https://example.com/checkin?token=abc123"
        self._patch_session(monkeypatch, _FakePage(data=_qr_png(payload)))
        out = await MasterFetchServer().screenshot(URL, decode_qr=True)
        text = [c for c in out if getattr(c, "type", "") == "text"][0].text
        assert payload in text

    @pytest.mark.asyncio
    async def test_no_qr_in_the_capture_is_a_note_not_an_error(self, monkeypatch):
        pytest.importorskip("cv2")
        import cv2
        import numpy as np

        blank = np.full((60, 60, 3), 255, dtype=np.uint8)
        ok, buf = cv2.imencode(".png", blank)
        assert ok
        self._patch_session(monkeypatch, _FakePage(data=buf.tobytes()))
        out = await MasterFetchServer().screenshot(URL, decode_qr=True,
                                                   selector="img.not-a-code")
        text = [c for c in out if getattr(c, "type", "") == "text"][0].text
        assert "no QR code found" in text

    @pytest.mark.asyncio
    async def test_undecodable_bytes_are_a_note_and_the_image_still_flows(
            self, monkeypatch):
        """PNG_BYTES 不是真图片：解码失败要降级成注记，截图本身照常交付。"""
        self._patch_session(monkeypatch, _FakePage())
        out = await MasterFetchServer().screenshot(URL, decode_qr=True)
        image = [c for c in out if getattr(c, "type", "") == "image"][0]
        text = [c for c in out if getattr(c, "type", "") == "text"][0].text
        assert image.data
        assert "decode_qr: unavailable on this capture" in text


# ─── 描述：纪律跟着 flag 上线，类字面量保持原样 ─────────────────────────────


class TestTheDescriptions:

    def test_discipline_is_in_the_literal_itself(self):
        """VISUAL ASSIST 不再是运行时拼接：它在 _TOOL_DEFS 字面量里
        （能力常开，纪律不是模式）。"""
        defs = {td["name"]: td for td in MasterFetchServer._enabled_tool_defs()}
        shot = defs["screenshot"]["description"]
        sf = defs["web_read"]["description"]
        assert "VISUAL ASSIST" in shot
        assert "selector=" in shot
        assert "VISUAL ASSIST" in sf
        assert '{"screenshot"' in sf
        # 纪律（安全红线 6 的 ≤N 次与一律转交）
        for desc in (shot, sf):
            assert "at most 3" in desc
            assert "DataDome" in desc
            assert "2FA" in desc
            assert "hand off" in desc

    def test_repeated_calls_do_not_accumulate(self):
        first = [td["description"] for td in MasterFetchServer._enabled_tool_defs()]
        again = [td["description"] for td in MasterFetchServer._enabled_tool_defs()]
        assert first == again
