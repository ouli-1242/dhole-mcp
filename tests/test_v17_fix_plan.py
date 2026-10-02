"""v17.0 实测缺陷修复计划的回归守卫。

来源：`dhole-mcp-fix-plan.md`（v17.0 实测，6 条缺陷，根因已定位到源码行）。
每条缺陷的守卫都写在下面，按计划建议的修复顺序编号。

这些用例的共同形状是「**看起来正常、实际答错**」——本仓库最忌讳的那一类：
- 批量路径抛的是原始 AttributeError（不是 envelope），客户端看到的是崩溃而不是诊断；
- OCR 抢救成功的 PDF 被标 `content_ok=false`（内容对、判定反）；
- 零匹配选择器等满 30s 后把「元素不存在」说成「页面没渲染完」，把人引向两个无效补救；
- 「请安装 [all]」在已装 [all] 的机器上是事实错误的指引。
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from dhole_mcp import crawl as crawl_mod
from dhole_mcp import sitemap as sm
from dhole_mcp.server import (
    BulkResponseModel,
    MasterFetchServer,
    ResponseModel,
    _detect_content_issue,
    _is_js_shell,
    _locator_or_raise,
    _screenshot_error_message,
    _wire_json,
)


def _dispatch(args: dict):
    """跑一次真实 dispatcher，返回 (content, structured envelope)。"""
    return asyncio.run(MasterFetchServer(cache_ttl=0)._dispatch("web_read", args))


# ── 缺陷 1（P0）· 批量路径 100% 抛 AttributeError ────────────────────────────

class TestBulkFetchDoesNotReachForASingleResultAttribute:
    """`_action_images` 只定义在 `ResponseModel` 上；`urls=[...]` 返回的是
    `BulkResponseModel`，那里这个属性是**不存在**而不是空。无守卫的读取抛
    AttributeError，冒泡成客户端可见的原始异常，整个批量调用失败。

    实测覆盖四种形式：带 option、单元素、不带 option、走 options 口袋——
    它们最终都落到同一行，所以这里测那**一行**，而不是四种排列。
    """

    def test_a_bulk_result_goes_through_the_dispatcher(self, monkeypatch):
        async def fake_smart_fetch(self, url="", urls=None, **kw):
            picked = urls or [url]
            return BulkResponseModel(
                results=[ResponseModel(url=u, status=200, content=["x"]) for u in picked],
                total=len(picked), successful=len(picked))

        monkeypatch.setattr(MasterFetchServer, "web_read", fake_smart_fetch)
        _content, payload = _dispatch({"urls": ["http://a.test/", "http://b.test/"]})
        assert payload["total"] == 2
        assert [r["url"] for r in payload["results"]] == ["http://a.test/", "http://b.test/"]

    def test_a_single_result_still_carries_its_images(self, monkeypatch):
        """守卫式读取不许把单条路径的元素截图功能改坏 —— 这是回归，不是新功能。"""
        async def fake_smart_fetch(self, url="", urls=None, **kw):
            row = ResponseModel(url=url, status=200, content=["x"])
            row._action_images = [{"data": "aGk=", "mime_type": "image/png"}]
            return row

        monkeypatch.setattr(MasterFetchServer, "web_read", fake_smart_fetch)
        content, _payload = _dispatch({"url": "http://a.test/"})
        kinds = [getattr(c, "type", None) for c in content]
        assert "image" in kinds, "单条路径的图片必须仍然作为 image content 返回"

    def test_bulk_mode_refuses_actions_so_no_images_can_be_silently_dropped(self):
        """守卫式读取是**完整**修复，不是权宜之计。

        批量模式明确拒绝 `actions`，而 `_action_images` 的唯一写入点
        （`_report_action_outcomes`）只在单条路径上跑 —— 所以「聚合各结果的
        图片」是死代码。这条把那个前提钉住：哪天批量开始支持 actions，这里会红，
        那时才真的需要聚合。
        """
        result = asyncio.run(MasterFetchServer(cache_ttl=0).web_read(
            url="", urls=["http://a.test/"], actions=[{"click": "a"}]))
        assert isinstance(result, BulkResponseModel)
        assert result.successful == 0
        for row in result.results:
            assert "bulk mode" in (row.error or ""), (
                "批量模式必须显式拒绝 actions，否则上面的守卫就不够了")


# ── 缺陷 3（P1）· OCR 成功的 PDF 被误判 js_shell ─────────────────────────────

OCR_TEXT = ("# scanned\n> OCR-extracted from scanned PDF · 1 pages\n\n--- Page 1 ---\n\n"
            "DHOLE OCR TEST 12345\nscanned page marker ABCDE")


def _pdf(**kw) -> ResponseModel:
    base = dict(url="http://h/scanned.pdf", status=200, content=[OCR_TEXT],
                content_type="application/pdf", fetcher_used="http",
                total_size_bytes=9662)
    return ResponseModel(**{**base, **kw})


class TestAnOcrRescuedPdfIsNotCalledAJavaScriptShell:
    """「大 body + 少文本」描述的是 HTML 空壳，而扫描件经 OCR 抢救回来**按构造**
    就是字节多、文本少：单页扫描件 ≈116 字符对 ≈9.6KB 文件，正好落在阈值
    （body > 3000、text < 200）里。

    `_detect_content_issue` 只守住了零文本那条分支，有文本的 PDF 会 fall
    through 到启发式，于是**内容对、判定反**：一个刚被 OCR 救回来的结果被标成
    `content_ok=false`，agent 依此丢弃它或按 next_action 去重取／换源白跑。
    """

    def test_an_ocr_rescued_scan_is_clean(self):
        r = _pdf()
        assert _is_js_shell(r) is False
        assert _detect_content_issue(r) == ""

    def test_a_short_text_layer_pdf_is_clean_too(self):
        """不是夹具特例：证书、封面页、稀疏文本层的 PDF 都是同一个形状。"""
        r = _pdf(url="http://h/cert.pdf", content=["Certificate of completion, 2026."],
                 total_size_bytes=9000)
        assert _is_js_shell(r) is False
        assert _detect_content_issue(r) == ""

    def test_a_scan_with_no_ocr_result_keeps_its_own_error(self):
        """零文本仍走 `pdf_no_text`，不许被这次的修复顺手并掉。"""
        r = _pdf(content=[])
        assert "pdf_no_text" in _detect_content_issue(r)

    def test_the_status_checks_after_it_still_run(self):
        """守卫放在 `_is_js_shell` 里、不是从 `_detect_content_issue` 提前
        `return ""` —— 那样会顺带吃掉后面的 http_error_4xx。"""
        assert "http_error_404" in _detect_content_issue(_pdf(status=404))

    def test_a_real_html_js_shell_is_still_caught(self):
        """回归：守卫只针对 PDF，HTML 空壳的判定一个字节都不许动。"""
        r = ResponseModel(url="https://quotes.toscrape.com/js/", status=200,
                          content=["<html><body></body></html>"],
                          content_type="text/html", fetcher_used="http",
                          total_size_bytes=12000)
        assert _is_js_shell(r) is True
        assert "js_shell_detected" in _detect_content_issue(r)

    def test_the_guard_is_in_the_predicate_not_only_the_diagnosis(self):
        """`_is_js_shell` 还被**升级决策**调用（三处）。对 PDF 升级浏览器毫无
        意义——浏览器不渲染数据文档，它只是给数据文档套个框。守卫放在函数入口
        才能一次性覆盖诊断与全部升级调用点。"""
        assert _is_js_shell(_pdf()) is False, (
            "守卫必须在 _is_js_shell 内部，否则升级决策仍会把 PDF 当空壳")


# ── 缺陷 2（P1）· 本地扫描件不做 OCR 恢复 ───────────────────────────────────

OCR_PAYLOAD = "# scanned\n> OCR-extracted from scanned PDF · 1 pages\n\n--- Page 1 ---\n\nDHOLE OCR TEST 12345"


def _stub_scan(monkeypatch, *, ocr_text=OCR_PAYLOAD):
    """让两条路径都看到「一份整篇扫描的 PDF」，并把 OCR 换成确定性的假实现。

    钉的是**接线**（本地路径有没有跑那一步），不是 OCR 引擎本身——引擎由下面
    那条真跑的用例覆盖。
    """
    from dhole_mcp import ocr as ocr_mod
    from dhole_mcp import pdf_extractor as pe
    from dhole_mcp.pdf_extractor import PdfResult

    def fake_extract(body, **kw):
        return PdfResult(content=["[Scanned/image-only PDF - no extractable text.]"],
                         title="scanned", scanned=True, content_ok=False,
                         error="scanned_pdf: this PDF is image-only")

    def fake_ocr(body, **kw):
        return PdfResult(content=[ocr_text], title="scanned", scanned=True,
                         error="", content_ok=True)

    monkeypatch.setattr(pe, "extract_pdf", fake_extract)
    monkeypatch.setattr(ocr_mod, "ocr_pdf", fake_ocr)
    monkeypatch.setattr(ocr_mod, "ocr_available", lambda: True)


class TestALocalScanGetsTheSameOcrRecoveryAsTheUrlPath:
    """整篇扫描件的 OCR 恢复此前只写在 server.py 的 URL 路径里。`parse` 只调了
    提取器就返回，于是**同一份字节**走 HTTP 被 OCR 成功、走磁盘却回「请安装
    dhole-mcp[all]」——而 `_parse_pdf` 的 docstring 恰好承诺两条路径一致。

    恢复逻辑已抽到 `pdf_extractor.recover_scanned_pdf`，两条路径共用。
    """

    def test_the_local_path_runs_the_recovery(self, monkeypatch, tmp_path):
        _stub_scan(monkeypatch)
        from dhole_mcp.parse import parse_file_detailed

        p = tmp_path / "scan.pdf"
        p.write_bytes(b"%PDF-1.4 fake")
        content, error, extras = parse_file_detailed(str(p))
        assert error == "", "本地路径必须跑 OCR 恢复，而不是回死路"
        assert "DHOLE OCR TEST 12345" in content
        assert extras["content_ok"] is True

    def test_both_paths_return_the_same_text_for_the_same_bytes(
            self, monkeypatch, tmp_path):
        """这条才是缺陷的正面表述：同一份字节，两条路径必须给同一个答案。"""
        _stub_scan(monkeypatch)
        from dhole_mcp.parse import parse_file_detailed
        from dhole_mcp.server import _extract_pdf_response

        body = b"%PDF-1.4 fake"
        p = tmp_path / "scan.pdf"
        p.write_bytes(body)
        local, _err, _extras = parse_file_detailed(str(p))
        remote = _extract_pdf_response(body, "application/pdf", len(body),
                                       "http://h/scan.pdf", "markdown", "http", 1.0)
        assert local.startswith(remote.content[0]), (
            "本地路径只允许在远程内容后追加说明，正文必须逐字一致")

    def test_the_recovery_is_a_no_op_when_ocr_is_not_installed(self, monkeypatch):
        """没装 OCR 时保持原有的诚实死路，不许把错误吞掉或改成静默空内容。"""
        from dhole_mcp import ocr as ocr_mod
        from dhole_mcp.pdf_extractor import PdfResult, recover_scanned_pdf

        monkeypatch.setattr(ocr_mod, "ocr_available", lambda: False)
        dead_end = PdfResult(content=["[Scanned/image-only PDF]"], scanned=True,
                             error="scanned_pdf: this PDF is image-only")
        out = recover_scanned_pdf(dead_end, b"%PDF")
        assert out is dead_end
        assert out.error.startswith("scanned_pdf")

    def test_it_does_not_touch_a_pdf_with_a_text_layer(self, monkeypatch):
        """有文本层的 PDF 一个字节都不许走 OCR。

        让 `ocr_pdf` 直接炸：只要它被调到，这条就红。断言「结果没变」是弱证据
        （本来就可能没变），断言「引擎根本没被调」才是这条要守的东西。
        """
        from dhole_mcp import ocr as ocr_mod
        from dhole_mcp.pdf_extractor import PdfResult, recover_scanned_pdf

        def boom(*a, **kw):
            raise AssertionError("有文本层的 PDF 不该走 OCR")

        monkeypatch.setattr(ocr_mod, "ocr_pdf", boom)
        healthy = PdfResult(content=["# Report\n\n> PDF · 1 pages"], content_ok=True,
                            quality_score=1.0)
        out = recover_scanned_pdf(healthy, b"%PDF")
        assert out is healthy
        assert out.ocr_fallback_used is False

    def test_an_encrypted_pdf_is_left_alone_too(self, monkeypatch):
        """加密件同样不走 —— OCR 打不开它，`ocr_pdf` 会被无谓地调一次并失败。"""
        from dhole_mcp import ocr as ocr_mod
        from dhole_mcp.pdf_extractor import PdfResult, recover_scanned_pdf

        monkeypatch.setattr(ocr_mod, "ocr_pdf",
                            lambda *a, **kw: pytest.fail("加密件不该走 OCR"))
        locked = PdfResult(content=["[encrypted]"], scanned=True, encrypted=True,
                           error="pdf_encrypted: password required")
        assert recover_scanned_pdf(locked, b"%PDF") is locked


# ── 缺陷 2 的伴随缺陷 · 本地路径把标题印两遍 ────────────────────────────────

class TestTheLocalPathDoesNotDuplicateTheTitle:
    """`extract_pdf` 的正文头本来就是 `# <title>`（`_format_metadata` 写的，
    `ocr_pdf` 也一样），而 `_parse_pdf` 又自己拼了一次 —— 于是**每一个带标题的
    PDF 从磁盘读都会把标题印两遍**，同一份字节走 HTTP 只印一遍。

    这是实现缺陷 2 时发现的：两条路径对同一份文档给出不同输出，正是这个函数
    的 docstring 承诺不会发生的事。
    """

    def test_the_title_appears_once(self, tmp_path):
        pytest.importorskip("pdfplumber")
        pytest.importorskip("pypdf")
        from pypdf import PdfReader, PdfWriter

        from dhole_mcp.parse import parse_file_detailed

        src = Path(__file__).resolve().parent / "background_checks.pdf"
        reader, writer = PdfReader(str(src)), PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.add_metadata({"/Title": "Quarterly Report 2026"})
        out = tmp_path / "titled.pdf"
        with open(out, "wb") as fh:
            writer.write(fh)

        content, error, extras = parse_file_detailed(str(out))
        assert error == ""
        assert content.count("# Quarterly Report 2026") == 1, (
            "标题必须只出现一次 —— 提取器的正文头里已经有一个")
        assert extras["metadata"]["title"] == "Quarterly Report 2026", (
            "标题仍然要作为字段可取，去掉的只是重复的正文行")


# ── 缺陷 2 的端到端（真 OCR，无夹具依赖则跳过）─────────────────────────────

class TestARealScannedPdfIsReadFromDisk:
    def test_end_to_end_with_the_real_ocr_stack(self, tmp_path):
        pytest.importorskip("PIL")
        from dhole_mcp import ocr as ocr_mod
        if not ocr_mod.ocr_available():
            pytest.skip("OCR 栈未安装")

        from PIL import Image, ImageDraw

        from dhole_mcp.parse import parse_file_detailed

        img = Image.new("RGB", (1000, 400), "white")
        ImageDraw.Draw(img).text((60, 120), "DHOLE OCR TEST 12345", fill="black")
        p = tmp_path / "scanned.pdf"
        img.save(p, "PDF", resolution=150.0)

        content, error, extras = parse_file_detailed(str(p))
        assert error == ""
        assert extras["content_ok"] is True
        assert "12345" in content, "OCR 必须真的读出那行字"


# ── 缺陷 4（P1）· OCR 提示硬编码，误导已装 [all] 的用户 ──────────────────────

class TestOcrHintsAreCapabilityAware:
    """"Install dhole-mcp[all]" 是一句关于**这台机器**的事实断言。在已经装了
    `[all]` 的机器上它是假的：它把「这条路径没产出 OCR 文本」报成「你没装
    OCR」。实测代价是开发者据此写下「本机未装 [all]」并据此排查，直到人工核对
    解释器才纠正 —— 真实用户会去重装一个已经装好的包。

    提示统一收进 `pdf_extractor.ocr_advice()`，四处（整篇扫描死路、两个逐页
    标记、web_read 的 next_action）同源，不可能对同一台机器给出两种说法。
    """

    def test_it_does_not_tell_you_to_install_when_ocr_is_present(self, monkeypatch):
        from dhole_mcp import ocr as ocr_mod
        from dhole_mcp.pdf_extractor import ocr_advice

        monkeypatch.setattr(ocr_mod, "ocr_available", lambda: True)
        advice = ocr_advice()
        assert "pip install" not in advice, "已装 OCR 却让人去装 —— 事实错误的指引"
        assert "installed" in advice, "要说清 OCR 在，只是这次没救回来"

    def test_it_does_say_install_when_ocr_is_missing(self, monkeypatch):
        from dhole_mcp import ocr as ocr_mod
        from dhole_mcp.pdf_extractor import ocr_advice

        monkeypatch.setattr(ocr_mod, "ocr_available", lambda: False)
        assert "pip install dhole-mcp[all]" in ocr_advice()

    def test_a_probe_that_raises_is_treated_as_missing(self, monkeypatch):
        """探测本身炸了不能连带炸掉响应构造 —— 当成没装，退回安装提示。"""
        from dhole_mcp import ocr as ocr_mod
        from dhole_mcp.pdf_extractor import ocr_advice

        def boom():
            raise RuntimeError("import machinery broke")

        monkeypatch.setattr(ocr_mod, "ocr_available", boom)
        assert "pip install dhole-mcp[all]" in ocr_advice()

    def test_the_scanned_dead_end_uses_the_helper(self, monkeypatch, tmp_path):
        """整篇扫描死路的 error / content 里必须是辅助函数的话，不是硬编码。

        直接调 `extract_pdf`：OCR 恢复是**调用方**的下一步，所以这里拿到的
        正是死路本身，不需要伪造 PDF 对象。
        """
        pytest.importorskip("pdfplumber")
        pytest.importorskip("PIL")
        from PIL import Image

        from dhole_mcp import ocr as ocr_mod
        from dhole_mcp.pdf_extractor import extract_pdf, ocr_advice

        monkeypatch.setattr(ocr_mod, "ocr_available", lambda: True)
        p = tmp_path / "scan.pdf"
        Image.new("RGB", (600, 300), "white").save(p, "PDF", resolution=150.0)

        out = extract_pdf(p.read_bytes(), extraction_type="markdown")
        assert out.scanned is True
        advice = ocr_advice()
        assert advice in out.error, "死路提示必须来自辅助函数"
        assert advice in out.content[0]
        assert "pip install" not in out.error, (
            "OCR 已装，这条死路不该让人去装")

    def test_the_next_action_for_a_scanned_pdf_is_also_capability_aware(
            self, monkeypatch):
        from dhole_mcp import ocr as ocr_mod
        from dhole_mcp.server import _agent_hints

        monkeypatch.setattr(ocr_mod, "ocr_available", lambda: True)
        r = ResponseModel(url="http://h/scan.pdf", status=200, content=[],
                          content_type="application/pdf", fetcher_used="http",
                          error="scanned_pdf: this PDF is image-only",
                          content_ok=False)
        _summary, next_action, _ok = _agent_hints(r)
        assert "pip install" not in next_action, (
            "next_action 也在让已装 OCR 的人去装 —— 与错误文本自相矛盾")


# ── 缺陷 5（P2）· 零匹配选择器白等 30s + 误诊 ────────────────────────────────

class _FakeLocator:
    def __init__(self, count: int):
        self._count = count
        self.screenshot_reached = False

    async def count(self) -> int:
        return self._count

    @property
    def first(self):
        return self

    async def screenshot(self, **kw):
        self.screenshot_reached = True
        return b"\x89PNG"


class _FakePage:
    def __init__(self, count: int):
        self.loc = _FakeLocator(count)

    def locator(self, selector):
        return self.loc


class TestAZeroMatchSelectorFailsFastAndHonestly:
    """`.first` 对零匹配的 locator 会等满 timeout（默认 30s），而错误映射把
    timeout 一律读成「页面没渲染完」——把人引向调 `timeout` 和加
    `wait_selector`，对「元素不在 DOM 里」这两个补救都无效。

    实测（本地靶站，浏览器已热）：修复前该情形与真超时无法区分；修复后零匹配
    3.6s 返回，与一次**成功**的裁切（2.9s）同量级——等待本身消失了，剩下的只是
    页面加载时间。
    """

    def test_a_zero_match_never_reaches_the_screenshot_call(self):
        page = _FakePage(count=0)
        with pytest.raises(LookupError) as got:
            asyncio.run(_locator_or_raise(page, "#nope"))
        assert "matched no element" in str(got.value)
        assert page.loc.screenshot_reached is False, (
            "预检没拦住 —— 这一路就是白等 30s 的那条")

    def test_a_real_match_still_returns_the_locator(self):
        page = _FakePage(count=3)
        loc = asyncio.run(_locator_or_raise(page, "h1"))
        assert loc is page.loc

    def test_the_headline_says_matched_nothing_not_rendering_timeout(self):
        msg = _screenshot_error_message(
            LookupError("selector matched no element on the loaded page: 'div'"))
        assert "matched nothing" in msg
        assert "raise timeout" not in msg, (
            "零匹配不是渲染超时 —— 让人去调 timeout 是无效方向")
        assert "wait_selector" in msg, "只在元素稍后出现时 wait_selector 才有用"

    def test_a_genuine_timeout_keeps_its_own_hint(self):
        msg = _screenshot_error_message(
            TimeoutError("Locator.screenshot: Timeout 30000ms exceeded."))
        assert "never finished rendering" in msg
        assert "matched nothing" not in msg

    def test_the_narrow_needle_is_matched_before_the_wide_one(self):
        """`_BROWSER_ERROR_HINTS` 按 headline 子串匹配、命中即停。新条目排在宽的
        `timeout` 之后就会被吞掉 —— 这条钉的是顺序，不是措辞。"""
        both = LookupError("selector matched no element on the loaded page: 'div' "
                           "(waited for timeout 30000ms)")
        assert "matched nothing" in _screenshot_error_message(both), (
            "同时提到 timeout 的 headline 必须仍走零匹配那条提示")


# ── 缺陷 6（P2）· discover_only + sitemap：地图短于站点时没人说 ──────────────
#
# 计划把这一条写成「无截断上限」，实测**不成立**：上限一直都在
# （`cap = min(5000, max(SITEMAP_MAP_FLOOR, max_pages * 10))`，floor 1000），
# 349 条不被截断只是因为 349 < 1000。真正的缺口是两处**没人说的截断**：
#
#   1. 走 <sitemapindex> 时的文件数 / 层数上限（`_MAX_SITEMAPS` /
#      `_MAX_INDEX_DEPTH`）独立于 URL 上限。撞上它时 `capped` 是 False，
#      计数也看不出来——站点把 1,200 页拆进 30 个小 sitemap，这里映射出
#      1,000 条，剩下 200 条没有任何痕迹。
#   2. `capped` 为 False 的那条分支**一个字都没有**，于是「mapped 349 URL(s)」
#      读起来就是「这就是整站」。而 sitemap 是站点**选择发布**的索引。
#
# 另外 `path_include` 过去在**截断之后**才过滤，于是 `next_action` 里那句
# 「用 path_include 看全子树」在长 sitemap 上是假的：过滤的是前 1000 条。
# 这条一起修，见 `discover_sitemap` 的 `accept`。


def _stub_sitemap(monkeypatch, *, n=3, capped=False, index_truncated=False,
                  urls=None, sitemaps=None, filtered_out=0):
    """把 `discover_sitemap` 换成一份现成结果：缺陷 6 考的是**报告**，不是抓取。

    替身**不**施加 `accept`——过滤谓词由被测的 `discover_sitemap` 自己负责
    （下面那个类单独测它）。这里要能精确指定 `listed` 与 `ok` 的差。
    `sitemaps=[]` 表示「一个 sitemap 都没抓到」，与「抓到了但没产出」是两回事。
    """
    res = sm.SitemapResult()
    res.urls = [sm.SitemapURL(url=u) for u in
                (urls if urls is not None
                 else [f"https://site.test/p{i}" for i in range(n)])]
    res.sitemaps_used = (["https://site.test/sitemap.xml"] if sitemaps is None
                         else list(sitemaps))
    res.capped = capped
    res.index_truncated = index_truncated
    res.filtered_out = filtered_out
    res.via = "conventional"
    monkeypatch.setattr(sm, "discover_sitemap", lambda start_url, **kw: res)
    return res


def _map(monkeypatch, **kw):
    _stub_sitemap(monkeypatch, **kw)
    return asyncio.run(crawl_mod._sitemap_map(
        "https://site.test/", None, None, max_pages=10, deadline_t=time.time() + 30))


class TestASitemapMapSaysWhenItIsShortOfTheSite:
    """`sitemap_truncated` 是给 agent 提前判断响应规模用的（计划第 7 节验收 3）。

    修复前这条地图的截断状态只活在 `summary` 的散文里：三个 `truncated_by_*`
    在一条**确实被截断**的地图上全是 False，照结构化字段判断的调用方会读反——
    又是「看起来正常、实际答错」。
    """

    def test_a_capped_map_flags_itself(self, monkeypatch):
        r = _map(monkeypatch, n=1000, capped=True)
        assert r.sitemap_truncated is True
        assert r.pages_discovered == 1000
        assert "map capped at 1000 URLs" in r.summary

    def test_the_flag_is_a_field_not_only_a_sentence(self, monkeypatch):
        """线上形态：标志必须真的出现在序列化结果里，而不是只在散文里。"""
        r = _map(monkeypatch, n=1000, capped=True)
        _text, payload = _wire_json(r)
        assert payload["sitemap_truncated"] is True
        assert payload["pages_discovered"] == 1000

    def test_the_small_map_wire_shape_does_not_change(self, monkeypatch):
        """计划验收：小 sitemap 不受影响、字段不变。默认值不上线，所以这条
        地图的 payload 与修复前逐键相同。"""
        r = _map(monkeypatch, n=3)
        _text, payload = _wire_json(r)
        assert "sitemap_truncated" not in payload

    def test_an_uncapped_map_does_not_read_as_the_whole_site(self, monkeypatch):
        r = _map(monkeypatch, n=349)
        # 先断措辞：这条用例要钉的是「没触顶那条分支一个字都不说」，
        # 把新字段的断言放前面会让修复前的失败停在一个无关的 AttributeError 上。
        assert "349 URL(s)" in r.summary
        assert "capped" not in r.summary
        assert "a sitemap is an index" in r.summary, (
            "没触顶的那条分支过去一个字都不说 —— 「mapped 349」于是成了「整站」")
        assert r.sitemap_truncated is False

    def test_the_index_limit_is_its_own_case_and_names_no_dead_knob(self, monkeypatch):
        r = _map(monkeypatch, n=900, index_truncated=True)
        assert r.sitemap_truncated is True
        assert "prefix of the site" in r.summary
        assert "max_pages" not in r.next_action, (
            "max_pages 动不了 sitemap 文件的读取上限 —— 点了没反应的旋钮比不点更糟")

    def test_entries_that_did_not_become_rows_are_counted(self, monkeypatch):
        """`ok` 与 sitemap 实际列出的条数可以不等（归一化后重复、或撞上
        deadline），而 summary 过去只报 `ok`，差值无声消失。"""
        r = _map(monkeypatch, urls=["https://site.test/a", "https://site.test/a/"])
        assert r.pages_discovered == 1
        assert "1 of the sitemap's 2" in r.summary


class TestAnEmptyMapIsNotEmptySite:
    """`_sitemap_map` 过去用同一个 `None` 表示两件事：「没抓到 sitemap」和
    「抓到了、解析了，但一条都没产出」。调用方按后者报了**前者**的话：

        no sitemap.xml found at ... (robots.txt had no Sitemap directive and
        /sitemap.xml returned nothing)

    ——而 sitemap 就在那儿，dhole 刚读完它。这是本仓库最忌讳的形状：调用看起来
    成功了，诊断却自信地答错，还把 agent 指向 BFS 去重新发现一个已经读过的站点。
    实测（真 HTTP 靶站）：`path_include=["/nothing/"]` 在修复前得到上面那句，
    修复后得到「sitemap.xml was read ... none of its 40 URL(s) are
    path_include=['/nothing']」。
    """

    def test_a_found_but_empty_map_is_not_reported_as_missing(self, monkeypatch):
        _stub_sitemap(monkeypatch, urls=[], filtered_out=40)
        r = asyncio.run(crawl_mod._sitemap_map(
            "https://site.test/", ["/nothing/"], None,
            max_pages=10, deadline_t=time.time() + 30))
        assert r is not None, "抓到了 sitemap 却说「没找到」—— 这就是那句假话的来源"
        assert r.sitemap_used is True
        assert r.sitemaps == ["https://site.test/sitemap.xml"]
        assert "was read" in r.summary and "the map is empty" in r.summary
        assert "40" in r.summary, "过滤掉多少条要说出来，否则无从判断是站点还是过滤器"
        assert "path_include" in r.summary
        assert "no sitemap.xml found" not in r.summary
        assert "path_include" in r.next_action or "scope" in r.next_action

    def test_no_sitemap_at_all_is_still_not_found(self, monkeypatch):
        """真的没抓到时必须仍然返回 None —— 调用方的「没找到」那句要留着。"""
        _stub_sitemap(monkeypatch, urls=[], sitemaps=[])
        assert asyncio.run(crawl_mod._sitemap_map(
            "https://site.test/", None, None,
            max_pages=10, deadline_t=time.time() + 30)) is None

    def _empty_map(self):
        return crawl_mod.CrawlResponseModel(
            start_url="https://site.test/", pages=[], discover_only=True,
            sitemap_used=True, summary="sitemap.xml was read ... but the map is empty")

    def _run(self, monkeypatch, sitemap):
        empty = self._empty_map()

        async def fake_map(*a, **kw):
            return empty

        calls: list[str] = []

        async def fake_fetch(self, url="", **kw):
            calls.append(url)
            return ResponseModel(url=url, status=200, content=["# body"],
                                 content_ok=True)

        monkeypatch.setattr(crawl_mod, "_sitemap_map", fake_map)
        monkeypatch.setattr(MasterFetchServer, "web_read", fake_fetch)
        out = asyncio.run(crawl_mod.web_crawl(
            MasterFetchServer(cache_ttl=0), "https://site.test/",
            sitemap=sitemap, discover_only=True, max_pages=1, ignore_robots=True))
        return out, empty, calls

    def test_an_explicit_sitemap_true_returns_the_empty_map(self, monkeypatch):
        out, empty, calls = self._run(monkeypatch, True)
        assert out is empty
        assert not calls, "sitemap=true 是「只用 sitemap」，不该偷偷去 BFS"

    def test_auto_still_falls_through_to_bfs_on_an_empty_map(self, monkeypatch):
        """`auto` = 「有 sitemap 就用，没有就 BFS」。空地图不等于「没有」，
        但 BFS 能到 sitemap 没列的页面，所以这里必须保持退回 BFS 的老行为 ——
        修「找不到」那句假话时不能顺手把它改掉。"""
        out, empty, calls = self._run(monkeypatch, "auto")
        assert calls, "auto + 空地图仍然要走 BFS"
        assert out is not empty and out.sitemap_used is False


class TestThePathFilterIsSpentBeforeTheMapCap:
    """`path_include` 过去在 `_sitemap_map` 里、`discover_sitemap` **返回之后**
    才生效，而返回时列表已经截到 cap。站点把 docs 排在 2000 条之后时，
    `path_include=["/docs/"]` 过滤的是前 1000 条（全是 blog）——结果为空，
    `_sitemap_map` 于是返回 None，整条调用**静默**退回 BFS。

    没有报错，只是答了另一个问题：这正是本仓库最忌讳的形状，也是
    `next_action` 里「scope with path_include/path_exclude to see a subtree in
    full」在长 sitemap 上不成立的原因。
    """

    PATHS = [f"/blog/{i}" for i in range(10)] + [f"/docs/{i}" for i in range(5)]

    def _http_get(self, paths=None):
        xml = ("<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
               + "".join(f"<url><loc>https://site.test{p}</loc></url>"
                         for p in (self.PATHS if paths is None else paths))
               + "</urlset>").encode()

        def get(u):
            return (200, xml) if u.endswith("/sitemap.xml") else None
        return get

    @pytest.fixture(autouse=True)
    def _no_ssrf_dns(self, monkeypatch):
        """每个子 sitemap 抓取前都跑 SSRF 校验，其中含 DNS 复查；本机 DNS 是
        fake-ip 代理，`site.test` 解析不到（见 evals/README）。这里只测「走到
        哪、报了什么」，网络判定不是本用例的对象。"""
        monkeypatch.setattr("dhole_mcp.security.validate_url", lambda u, **kw: u)

    def test_the_cap_is_spent_on_the_subtree_that_was_asked_for(self):
        res = sm.discover_sitemap("https://site.test/", http_get=self._http_get(),
                                  max_urls=3, accept=lambda u: "/docs/" in u)
        assert len(res.urls) == 3
        assert all("/docs/" in u.url for u in res.urls), (
            "cap 又被花在 blog 上了 —— 调用方要的子树一条都没进来")

    def test_without_a_predicate_the_cap_still_counts_raw_entries(self):
        """对照：没有谓词时行为与修复前一致（就是前 N 条），cap 的语义没被改。"""
        res = sm.discover_sitemap("https://site.test/", http_get=self._http_get(),
                                  max_urls=3)
        assert [u.url for u in res.urls] == [
            "https://site.test/blog/0", "https://site.test/blog/1",
            "https://site.test/blog/2"]

    def test_a_predicate_that_raises_drops_only_its_own_url(self):
        """本模块的契约是「永不抛」。一条坏 URL 不能让整张地图消失——那会静默
        退回 BFS，而调用方连地图都没见过。"""
        def accept(u):
            if "/blog/3" in u:
                raise ValueError("junk entry")
            return True
        res = sm.discover_sitemap("https://site.test/", http_get=self._http_get(),
                                  max_urls=100, accept=accept)
        assert len(res.urls) == len(self.PATHS) - 1
        assert all("/blog/3" not in u.url for u in res.urls)

    def test_the_index_file_limit_is_reported_only_when_a_file_was_left(self):
        """`index_truncated` 是**准确**的，不是 `capped` 那种「至少」：刚好读满
        `_MAX_SITEMAPS` 个文件时没有第 N+1 个可读，标志必须保持 False。"""
        def get_for(children):
            idx = ("<sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
                   + "".join(f"<sitemap><loc>https://site.test/sm{i}.xml</loc></sitemap>"
                             for i in range(children))
                   + "</sitemapindex>").encode()
            leaf = ("<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
                    "<url><loc>https://site.test/leaf</loc></url></urlset>").encode()

            def get(u):
                if u.endswith("/sitemap.xml"):
                    return (200, idx)
                if "/sm" in u:
                    return (200, leaf)
                return None
            return get

        # 索引自身占掉一个名额，所以「刚好读满」是 _MAX_SITEMAPS - 1 个子文件。
        exact = sm.discover_sitemap("https://site.test/",
                                    http_get=get_for(sm._MAX_SITEMAPS - 1))
        assert exact.index_truncated is False, "刚好读满不是截断 —— 没有下一个文件可读"
        over = sm.discover_sitemap("https://site.test/",
                                   http_get=get_for(sm._MAX_SITEMAPS))
        assert over.index_truncated is True




