"""G10 回归：截图落盘 —— 看不见图片的调用方也要拿得到那张图。

报告的缺口是「screenshot 只回裸图」：非多模态模型收到一句 image unavailable 外加一
堆它读不懂的 base64，等于这个工具对它不存在；即便是多模态客户端，也没有任何下游工具
能接着用这张图（没有路径）。

这里守的四件事：
    路径校验发生在**启动浏览器之前** —— 一个写错的 save_to 不该值 10 秒冷启动。
    不写非图片命名的文件（无论存在与否）—— 打错一个字的截图工具吃掉 notes.txt
    是数据丢失；一个尚不存在的 task.bat 一样不该被创建（G25 评审把后缀校验从
    「已存在的文件」扩到「任何非图片命名」，代码这才与描述对齐）。
    落盘的事实出现在文本段里 —— 读不到图片的那一方，路径就是它唯一能用的输出。
    注解说真话 —— save_to 会写盘、anti_detect 会答挑战，readOnlyHint=True 等于
    把写原语放进宿主的免审批通道。
"""

from __future__ import annotations

import pytest

# A1：save_to 的家从 web_screenshot 搬到了 web_act 的截图动作，helper 随迁。
from dhole_mcp.actions import _resolve_save_to, _write_screenshot, build_page_action
from dhole_mcp.server import MasterFetchServer

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


class TestThePathIsCheckedBeforeTheBrowserStarts:

    def test_parents_are_created(self, tmp_path):
        target = tmp_path / "deep" / "nested" / "shot.png"
        assert not target.parent.exists()
        path = _write_screenshot(PNG, _resolve_save_to(str(target), "png"))
        assert target.read_bytes() == PNG
        assert path == str(target.resolve())

    def test_the_returned_path_is_absolute(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = _write_screenshot(PNG, _resolve_save_to("shot.png", "png"))
        assert path == str(tmp_path / "shot.png")
        assert (tmp_path / "shot.png").exists()

    def test_a_home_shortcut_is_expanded_rather_than_written_literally(self):
        """只解析、不落盘（这里没有 tmp_path 可断言）：真正的等价物是「返回的路径
        不再带 ~，而且落在展开后的家目录下」。"""
        import os
        from pathlib import Path

        target = _resolve_save_to("~/dhole-g10-probe.png", "png")
        assert not str(target).startswith("~")
        assert Path(os.path.expanduser("~/dhole-g10-probe.png")) == target
        assert not target.exists(), "resolving must not create anything"

    @pytest.mark.parametrize("bad", ["", "   ", "./"])
    def test_a_path_that_is_not_a_file_target_is_refused(self, bad, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError):
            _resolve_save_to(bad, "png")

    def test_a_directory_is_refused_rather_than_written_into(self, tmp_path):
        with pytest.raises(ValueError) as e:
            _resolve_save_to(str(tmp_path), "png")
        assert "directory" in str(e.value)


class TestRetakingAShotDoesNotEatADocument:

    def test_an_existing_image_is_overwritten_because_that_is_the_point(self, tmp_path):
        """同一个名字重拍是正常用法（改完样式再来一张）。拦下来只会逼人换个
        越来越长的文件名。"""
        target = tmp_path / "shot.png"
        target.write_bytes(b"old bytes")
        _write_screenshot(PNG, _resolve_save_to(str(target), "png"))
        assert target.read_bytes() == PNG

    def test_a_file_that_is_not_named_like_an_image_is_left_alone(self, tmp_path):
        notes = tmp_path / "notes.txt"
        notes.write_text("the only copy", encoding="utf-8")

        with pytest.raises(ValueError) as e:
            _resolve_save_to(str(notes), "png")

        assert "refusing to overwrite" in str(e.value)
        assert notes.read_text(encoding="utf-8") == "the only copy"
        assert ".png" in str(e.value), "报错要说清改成什么才通过"

    def test_a_new_file_that_is_not_named_like_an_image_is_refused_too(self, tmp_path):
        """后缀守卫不以「文件已存在」为前提（G25 评审）：描述说 a non-image file
        name is refused，那就无论存在与否都拒——否则 save_to 是一扇无门的
        「任意新路径写字节」原语，往 Startup 目录塞 .bat 的路是敞开的。"""
        target = tmp_path / "startup" / "task.bat"

        with pytest.raises(ValueError) as e:
            _resolve_save_to(str(target), "png")

        assert "refusing to create" in str(e.value)
        assert "not named like an image" in str(e.value)
        assert ".png" in str(e.value), "报错要说清改成什么才通过"
        assert not target.parent.exists(), "校验不许顺手建目录"

    def test_a_jpeg_named_target_may_hold_jpeg_bytes(self, tmp_path):
        """image_type=jpeg 写 .jpg 是同一个动作，不该被后缀守卫拦下。"""
        shot = tmp_path / "a.jpg"
        _write_screenshot(b"\xff\xd8\xff", _resolve_save_to(str(shot), "jpeg"))
        assert shot.read_bytes() == b"\xff\xd8\xff"

    def test_an_unwritable_target_is_a_value_error_not_a_traceback(self, tmp_path):
        """父目录建不了要成为可读的错误，而不是 OSError 冒到工具层——这里的形状
        是「一个叫 blocker 的文件挡在了中间目录的位置上」。"""
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")

        with pytest.raises(ValueError) as e:
            _write_screenshot(PNG, _resolve_save_to(str(blocker / "s.png"), "png"))

        assert "could not write" in str(e.value)


class TestTheAnnotationsTellTheTruth:
    """注解是宿主决定「要不要先问用户」的依据：readOnlyHint=True 的工具会被
    自动放行，而 save_to 写盘、anti_detect 答 Cloudflare 挑战都不是只读
    （G25 评审：注解撒谎等于给写原语开免审通道）。"""

    def test_web_act_is_not_read_only_on_the_wire(self):
        """A1：截图能力住进 web_act；"save_to 写盘、anti_detect 答挑战都不是
        只读"的注解诚实现在由 web_act 这扇门继续承担。"""
        defs = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}
        ann = defs["web_act"]["annotations"]
        assert ann["readOnlyHint"] is False
        assert ann["idempotentHint"] is False
        assert ann["openWorldHint"] is True
        assert "web_screenshot" not in defs


class TestScreenshotUsesTheKnobItDocuments:

    def test_a_bad_save_to_refuses_at_action_validation(self, tmp_path):
        """A1：校验在动作归一化层发生 —— 那是浏览器冷启动之前。"""
        with pytest.raises(ValueError) as e:
            build_page_action([{"screenshot": {"save_to": str(tmp_path)}}])
        assert "directory" in str(e.value)
        with pytest.raises(ValueError) as e:
            build_page_action([{"screenshot": {"save_to": str(tmp_path / "a.png")}}])
        assert "selector" in str(e.value), "既不落盘也不裁元素的截图没有意义"

    @pytest.mark.asyncio
    async def test_the_saved_path_lands_in_the_action_receipt(self, tmp_path):
        """文本段是非多模态客户端唯一能读的那一半：A1 之后路径进的是动作回执行
        （metadata.actions → summary/next_action 的原料），字节一律不搭 transcript。"""
        target = tmp_path / "page.png"
        pa = build_page_action([{"screenshot": {"save_to": str(target),
                                                "full_page": True}}])

        class _Page:
            url = "https://example.com/"

            async def screenshot(self, **kwargs):
                assert kwargs.get("full_page") is True
                return PNG

        await pa(_Page())

        (row,) = pa.outcomes
        assert row["ok"] is True
        assert "image_b64" not in row
        assert target.name in row["saved"]
        assert row["saved_bytes"] == len(PNG)
        assert target.read_bytes() == PNG


@pytest.mark.live
@pytest.mark.asyncio
async def test_a_real_page_lands_on_disk_as_a_real_png(tmp_path):
    """真浏览器、真页面：证明的不是「write_bytes 被调了」（上面已经证了），而是
    落下来的东西是一个能被打开的图片文件，且路径出现在文本段里。"""
    from dhole_mcp.browser import check_browser_available

    if not check_browser_available():
        pytest.skip("no browser deps for the live tier")

    try:
        out = await MasterFetchServer().web_screenshot(
            "https://example.com/", save_to=str(tmp_path / "real.png"), timeout=60000)
    except RuntimeError as e:
        # patchright 装了但 chromium 没装（`python -m patchright install chromium`
        # 是 [all] 之外的一步）——那是环境缺件，不是这个功能坏了。
        if "executable" in str(e).lower() or "install" in str(e).lower():
            pytest.skip(f"no browser binary: {str(e)[:120]}")
        raise

    text = [c for c in out if getattr(c, "type", "") == "text"][0].text
    written = tmp_path / "real.png"
    assert written.exists(), text
    assert written.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    assert written.stat().st_size > 1000, "a 1KB screenshot is a blank frame"
    assert str(written.resolve()) in text
