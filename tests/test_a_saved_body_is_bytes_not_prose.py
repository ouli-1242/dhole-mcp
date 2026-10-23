"""`save_to` 交出去的是**字节**，不是正文 —— 而且它不是抽取路径上的一个开关。

这条路的形状由三件事决定，每一件都单独钉一遍：

  1. **在抽取之前分叉**。`49MB 的 plugins.json` 这条真实请求卡在 50MB 的抽取红线上，
     换回的是一句 "too large"。红线是**抽取**的上限（那个字符串要进上下文、要过一遍
     主内容检测），而落盘根本不抽取 —— 所以 `_body_ceiling()` 在 `save_to` 打开时
     改口说落盘上限（默认 256MB，天花板 1GB）。拿抽取的红线拦一次下载，等于"因为
     文本装不下所以不给你文件"。
  2. **回执不是一页**。它交回 `saved 62,914,560 bytes to D:/...`，于是"大字少"的每
     一条页面启发式都会命中它 —— 实测 60MB 的 .bin 被读成 `js_shell_detected`，
     紧接着 HTTP 档把它当失败升级到浏览器，而浏览器只能回 "Page.goto: Download is
     starting"。一个成功的下载被说成"这页需要 JS 渲染"，原因是判词不是给它写的。
     `_annotate_quality` 因此按**回执自己点的名**（metadata.saved_to）让路。
  3. **不吃也不写缓存**。缓存里存的是抽取出来的正文，从来没有原始 body；拿一份正文
     去覆盖"这次要写文件"的调用，交出的是一份空文件加一句 saved。反过来，把回执存
     进去，那个 URL 在 TTL 内的每一次普通读都会拿到这句话当正文。

真机端到端（真起服务、真写盘、真核 sha256、真拒绝覆盖）在
`evals/r4_live_download_check.py`：那个脚本要一个进程外的 HTTP 服务，不适合塞进
这条按毫秒算的套件，但它是这套判据的来源。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from dhole_mcp import quality
from dhole_mcp import server as S
from dhole_mcp.context import _SAVE_TO, _SAVE_MAX_BYTES
from dhole_mcp.fetcher import Response


BODY = b"x" * 4096


def _page(body: bytes = BODY, *, ctype: str = "application/octet-stream",
          url: str = "https://example.com/file.bin") -> Response:
    return Response(url=url, body=body, status=200,
                    headers={"content-type": ctype, "content-length": str(len(body))})


def _under(save_to: str = "", max_save_bytes: int = 0):
    """把一个 ContextVar 作用域包成上下文管理器（与装饰器里那份同形）。"""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        t1 = _SAVE_TO.set(save_to)
        t2 = _SAVE_MAX_BYTES.set(max_save_bytes)
        try:
            yield
        finally:
            _SAVE_MAX_BYTES.reset(t2)
            _SAVE_TO.reset(t1)

    return _ctx()


class TestTheCeilingChangesWithTheJob:
    """`_body_ceiling()` 是这一族的唯一作者：抽取 50MB、落盘它自己的上限。"""

    def test_without_save_to_the_extraction_cap_is_the_answer(self):
        with _under():
            assert S._body_ceiling() == S.MAX_RESPONSE_BYTES

    def test_a_save_to_call_is_measured_against_the_save_cap(self):
        with _under("/tmp/whatever.bin"):
            assert S._body_ceiling() == S.DEFAULT_MAX_SAVE_BYTES

    def test_an_explicit_cap_wins_over_the_default(self):
        with _under("/tmp/whatever.bin", 8 * 1024 * 1024):
            assert S._body_ceiling() == 8 * 1024 * 1024

    def test_the_ceiling_never_climbs_over_its_own_hard_cap(self):
        """`max_save_bytes` 再大也不会把红线抬过 1GB —— 那是文档写着的天花板。"""
        with _under("/tmp/whatever.bin", 64 * 1024 * 1024 * 1024):
            assert S._body_ceiling() == S.MAX_SAVE_BYTES_CAP

    def test_a_61mb_body_is_refused_by_extraction_but_not_by_a_save_to(self, tmp_path):
        """同一份 61MB 的字节，两条路两个答案 —— 这正是 R4 要修的那件事。"""
        big = b"y" * (61 * 1024 * 1024)
        with _under():
            with pytest.raises(ValueError, match="too large"):
                S._check_response_size(_page(big))
        with _under(str(tmp_path / "big.bin")):
            S._check_response_size(_page(big))  # 不抛：落盘的红线是 256MB


class TestTheReceiptIsNotAPage:
    """回执的字段与判词：它是"写完了"这句话，不是"这页没有正文"。"""

    def test_a_saved_body_returns_a_receipt_and_a_digest(self, tmp_path):
        target = tmp_path / "file.bin"
        r = S._save_response_body(_page(), target)
        assert r.error == ""
        assert r.content == [f"saved 4,096 bytes to {target}"]
        assert (r.metadata or {})["saved_to"] == str(target)
        assert (r.metadata or {})["bytes"] == 4096
        assert (r.metadata or {})["sha256"] == hashlib.sha256(BODY).hexdigest()
        assert target.read_bytes() == BODY

    def test_the_digest_is_of_the_bytes_on_disk(self, tmp_path):
        target = tmp_path / "file.bin"
        r = S._save_response_body(_page(), target)
        assert (r.metadata or {})["sha256"] == hashlib.sha256(
            target.read_bytes()).hexdigest()

    def test_over_the_limit_nothing_is_written_and_the_size_is_said(self, tmp_path):
        target = tmp_path / "file.bin"
        with _under(str(target), 1024):
            r = S._save_response_body(_page(), target)
        assert "Response body too large" in (r.error or "")
        assert "the save_to limit of 1,024" in (r.error or "")
        assert r.total_size_bytes == 4096
        assert not target.exists()

    def test_a_receipt_is_not_judged_as_a_page(self, tmp_path):
        """`_annotate_quality` 认 `metadata.saved_to` 这一颗，而不是 `_SAVE_TO`。

        差别是真的：爬行与批读都跑在同一个作用域里，它们读到的每一页都该照常受判。
        """
        receipt = S._save_response_body(_page(b"z" * 500_000), tmp_path / "z.bin")
        assert receipt.error == ""
        with _under("/somewhere.bin"):
            judged = quality._annotate_quality(receipt)
        assert judged.error == ""
        assert "content_issue" not in (judged.metadata or {})

    def test_a_page_read_in_the_same_scope_is_still_judged(self):
        """反方向：作用域里开着 `save_to`，一份真是页面的响应照样要受判。

        比的不是"有没有判词"，是**同一份输入在两种作用域下的判词逐字相同** ——
        否则这条让路就成了"顺手关掉一整类判读"。
        """
        def _page_result() -> S.ResponseModel:
            return S.ResponseModel(
                url="https://example.com/login", status=200,
                content=["<form><label>Password</label><input type=\"password\" "
                         "name=\"pw\"></form>"], extracted_type="markdown")

        outside = quality._detect_content_issue(_page_result())
        assert outside, "这份输入本来就该有判词，否则这条用例什么都没证明"
        assert quality._detect_content_issue(_page_result()) == outside
        with _under("/somewhere.bin"):
            assert quality._detect_content_issue(_page_result()) == outside


class TestThePathIsCheckedBeforeTheDownload:
    """"先验后发"：一个打错的路径不该先付一次下载再来报错。"""

    def test_a_missing_parent_is_refused_with_the_reason(self, tmp_path):
        with pytest.raises(ValueError, match="parent directory"):
            S._resolve_download_target(str(tmp_path / "nope" / "x.bin"))

    def test_a_directory_is_not_a_file(self, tmp_path):
        with pytest.raises(ValueError, match="is a directory"):
            S._resolve_download_target(str(tmp_path))

    def test_an_existing_file_is_never_overwritten(self, tmp_path):
        target = tmp_path / "keep.bin"
        target.write_bytes(b"mine")
        with pytest.raises(ValueError, match="already exists"):
            S._resolve_download_target(str(target))
        assert target.read_bytes() == b"mine"

    def test_an_empty_path_is_refused_by_name(self):
        with pytest.raises(ValueError, match="needs a file path"):
            S._resolve_download_target("   ")

    def test_a_restricted_directory_is_refused(self, tmp_path, monkeypatch):
        monkeypatch.setattr(S, "_blocked_path_prefix", lambda p: "a system directory")
        with pytest.raises(ValueError, match="restricted system"):
            S._resolve_download_target(str(tmp_path / "x.bin"))


class TestTheWriteIsAtomic:
    """一半的 .part 不是半份文件；`.part` 也不会留在旁边当垃圾。"""

    def test_no_part_file_survives_a_successful_write(self, tmp_path):
        target = tmp_path / "x.bin"
        S._write_download(target, BODY)
        assert target.exists()
        assert not Path(str(target) + ".part").exists()

    def test_a_failed_write_leaves_no_part_file(self, tmp_path):
        """父目录不存在时 open() 就炸 —— 半个文件与一个 `.part` 都不许留下。"""
        target = tmp_path / "nodir" / "x.bin"
        with pytest.raises(OSError):
            S._write_download(target, BODY)
        assert not Path(str(target) + ".part").exists()
        assert not target.exists()

    def test_a_file_that_appears_mid_download_is_not_overwritten(self, tmp_path):
        """出发前那一问与 rename 之间隔着**整段下载**，期间用户可能建了同名文件。

        实测的窗口不是微秒：60MB 要几十秒。只在出发前问一次，"拒绝覆盖"就只是句
        关于出发的话 —— rename 前那一问把它缩到两次系统调用。这里造的就是下载期间
        那个文件出现了：写进 `.part` 的字节一个都不许落到 `target` 上。
        """
        target = tmp_path / "x.bin"
        mine = b"the user's own file, created while the download ran"
        target.write_bytes(mine)

        with pytest.raises(OSError) as excinfo:
            S._write_download(target, BODY)
        assert "refusing to overwrite" in str(excinfo.value)
        assert target.read_bytes() == mine
        assert not Path(str(target) + ".part").exists()

    def test_the_same_bytes_are_never_written_twice_by_accident(self, tmp_path):
        """反方向：第一次写成功之后，第二次**必须**被拒 —— 否则上面那条是空谈。"""
        target = tmp_path / "x.bin"
        S._write_download(target, BODY)
        with pytest.raises(OSError) as excinfo:
            S._write_download(target, b"other bytes")
        assert "refusing to overwrite" in str(excinfo.value)
        assert target.read_bytes() == BODY


class TestTheCacheStaysOutOfIt:
    """两条口都不许拿"抽取出来的正文"冒充"这一份字节"。"""

    def test_a_save_to_response_is_never_stored(self, tmp_path):
        r = S._save_response_body(_page(), tmp_path / "x.bin")
        with _under(str(tmp_path / "x.bin")):
            assert S._store_ttl(r, 3600) == 0

    def test_the_same_receipt_is_storable_once_the_scope_closes(self, tmp_path):
        """反方向：`_store_ttl` 的 0 来自作用域，不是来自回执的形状本身。"""
        r = S._save_response_body(_page(), tmp_path / "x.bin")
        with _under():
            assert S._store_ttl(r, 3600) == 3600


class TestTheDoorSaysWhyBeforeFetching:
    """门口的三句话都在发请求之前说完，而且各自点名自己那一刻的事实。"""

    def setup_method(self):
        self.server = S.MasterFetchServer()

    @pytest.mark.asyncio
    async def test_a_batch_has_no_single_body_to_save(self, tmp_path):
        r = await self.server.web_read(url=None, urls=["https://a.example/", "https://b.example/"],
                                       save_to=str(tmp_path / "x.bin"))
        assert "batch (2 URLs)" in (r.error or "")
        assert not (tmp_path / "x.bin").exists()

    @pytest.mark.asyncio
    async def test_a_cap_without_a_destination_is_refused(self):
        r = await self.server.web_read(url="https://example.com/", max_save_bytes=1024)
        assert "without save_to" in (r.error or "")

    @pytest.mark.asyncio
    async def test_a_rendered_page_has_no_bytes_to_save(self, tmp_path):
        """三颗"先渲染再说"的参数与落盘互斥：浏览器交回的是 DOM，不是 body。"""
        for kwargs in ({"actions": [{"wait": 100}]},
                       {"force_fetcher": "stealthy"},
                       {"cdp_url": "http://127.0.0.1:9222"}):
            r = await self.server.web_read(url="https://example.com/",
                                           save_to=str(tmp_path / "x.bin"), **kwargs)
            assert "asks for a RENDERED page" in (r.error or ""), kwargs

    @pytest.mark.asyncio
    async def test_the_cap_range_is_named_not_clamped(self, tmp_path):
        r = await self.server.web_read(url="https://example.com/",
                                       save_to=str(tmp_path / "x.bin"),
                                       max_save_bytes=2 * 1024 * 1024 * 1024)
        assert "max_save_bytes takes 1-" in (r.error or "")

    @pytest.mark.asyncio
    async def test_a_bad_path_costs_no_request(self, tmp_path):
        r = await self.server.web_read(url="https://example.com/",
                                       save_to=str(tmp_path / "nope" / "x.bin"))
        assert "parent directory" in (r.error or "")
        assert "Nothing was fetched" in (r.next_action or "")
