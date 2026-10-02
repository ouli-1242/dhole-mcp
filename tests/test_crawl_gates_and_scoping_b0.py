"""B0 补测：爬取面的四个主题 —— 预算三闸门、同域归一、子树前缀语义、robots 预剪。

大众化一刀切（17.0）动工前，爬取面只有 24 个用例（g20 的 4 + g27 的 11 + g28 的 9），
而 B5 要在这一面上动刀（删总预算 / 删 delay / 删 crawl_urls / 收紧上限）。刀落在哪
先把尺子放在哪：本文件只给**留下的形状**写，不为将删的东西写 —— 总预算、`delay`
参数、`crawl_urls` 三者一律不在此处出现。三刀都已落下，`crawl_urls` 连同它的四个
裁剪计数一起走了（g28 那个文件也跟着），这一段留着是为了说明为什么这里没有它们的测试。

四个主题与 B0 计划一一对应：

1. **预算三闸门与各自如实报告**：max_pages / deadline / 每页 max_chars 各自到点时，
   布尔位要立起来、summary 要说出口。一个"停了但不说为什么"的爬取，调用方只能把
   「站就这么大」和「你被我掐了」混成一件事 —— 那正是 g28 当年为一份调用方清单单独
   记账的原因，闸门这边不能是漏网的同类。
2. **同域归一与跨域丢弃**：`normalize_url`（283 行去重判定的地基）与
   `extract_same_domain_links` 的 netloc 判定。去重准确率评测（集 A 的免费副产品）
   量的是这条链，先把单点行为钉住。
3. **子树前缀语义**：模式命名的契约 —— `/docs` 是子树不是字符串前缀，唯一通配是
   尾部 `/*`，路径中段的通配符拒绝而不是悄悄匹配空集。g28/bug_report2 已钉了
   `/docs-old` 方向；这里补拼法等价与拒绝面。
4. **robots 预剪**：发现阶段就把已知的 disallowed 丢出队列（robots_skipped 计数），
   seed 被禁时一次请求都不发，robots 读不到时放行（"not checked ≠ complied" 的
   爬取面版本）。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp import robots
from dhole_mcp import crawl as crawl_mod
from dhole_mcp.crawl import (
    extract_same_domain_links,
    normalize_path_pattern,
    normalize_url,
    path_allowed,
)
from dhole_mcp.server import ResponseModel

SEED = "https://gates.example.com/"
ROOT = "gates.example.com"


class _FakeSite:
    """每一页秒回，只记录被请求到的 URL；页面内容按 URL 查表。"""

    def __init__(self, pages: dict[str, str] | None = None, sleep: float = 0.0):
        self.fetched: list[str] = []
        self.sleep = sleep
        self.pages = pages or {}

    async def web_read(self, url, **kwargs):
        self.fetched.append(url)
        if self.sleep:
            await asyncio.sleep(self.sleep)
        body = self.pages.get(url, "<p>leaf page</p>")
        return ResponseModel(url=url, status=200, content=[body],
                             content_type="text/html", fetcher_used="http",
                             content_ok=True)


def _page_with_links(links: list[str]) -> str:
    return ("<html><body>"
            + "".join(f'<a href="{h}">link</a>' for h in links)
            + "</body></html>")


# ─── 主题 1：预算三闸门与各自如实报告 ─────────────────────────────────────────


class TestBudgetGatesReportThemselves:

    @pytest.mark.asyncio
    async def test_max_pages_gate_trips_and_says_so(self):
        links = [f"/p{i}" for i in range(4)]
        site = _FakeSite({SEED: _page_with_links(links)})

        out = await crawl_mod.web_crawl(site, SEED, max_pages=2, max_depth=1,
                                          cache_ttl=0)

        assert out.pages_crawled == 2
        assert out.truncated_by_max_pages is True
        assert "stopped: max_pages reached" in out.summary
        assert "crawl stopped early (max_pages)" in out.next_action

    @pytest.mark.asyncio
    async def test_time_deadline_gate_trips_and_says_so(self):
        """闸门是批内逐结果检查的：seed 那一抓付了钱（sleep 超过 deadline），但
        处理它的那一拍已经到点 —— 页面被闸掉、truncated_by_time 如实立起来。
        断言的就是这个语义：请求发了、页没报上来、原因说了出口。"""
        site = _FakeSite({SEED: _page_with_links(["/a", "/b"])}, sleep=0.05)

        out = await crawl_mod.web_crawl(site, SEED, max_pages=5, max_depth=1,
                                          deadline_ms=30, cache_ttl=0)

        assert site.fetched == [SEED]          # 请求发了
        assert out.pages_crawled == 0          # 但页面没赶上记账
        assert out.truncated_by_time is True
        assert out.truncated_by_max_pages is False
        assert "stopped: time deadline reached" in out.summary
        assert "time deadline" in out.next_action

    @pytest.mark.asyncio
    async def test_a_crawl_that_hit_nothing_reports_no_gate(self):
        site = _FakeSite({SEED: _page_with_links(["/a", "/b"])})

        out = await crawl_mod.web_crawl(site, SEED, max_pages=5, max_depth=1,
                                          cache_ttl=0)

        assert (out.truncated_by_max_pages, out.truncated_by_time) == (False, False)
        assert "stopped:" not in out.summary

    @pytest.mark.asyncio
    async def test_per_page_cap_truncates_and_hands_the_offset(self):
        """每页预算到点：内容被切到上限，is_truncated/next_offset 替调用方记住
        「这页还有」，让他知道去 web_read(offset=) 续读，而不是当整页就这么长。"""
        big = "<p>" + ("word " * 20000) + "</p>"          # ~100k chars
        site = _FakeSite({SEED: big})

        out = await crawl_mod.web_crawl(site, SEED, max_pages=1,
                                          max_content_chars_per=500, cache_ttl=0)

        assert out.pages_crawled == 1
        page = out.pages[0]
        assert page.content_chars == 500
        assert page.is_truncated is True
        assert page.next_offset == 500

    @pytest.mark.asyncio
    async def test_a_page_under_the_cap_is_not_marked_truncated(self):
        site = _FakeSite({SEED: "<p>short page</p>"})

        out = await crawl_mod.web_crawl(site, SEED, max_pages=1,
                                          max_content_chars_per=5000, cache_ttl=0)

        page = out.pages[0]
        assert page.is_truncated is False
        assert page.next_offset == 0

    @pytest.mark.asyncio
    async def test_asking_below_the_per_page_floor_is_clamped_and_said(self):
        """下限 500：往 crawled 结果里塞 10 个字符的页是没人要的。夹了要说。"""
        site = _FakeSite({SEED: "<p>" + ("word " * 4000) + "</p>"})

        out = await crawl_mod.web_crawl(site, SEED, max_pages=1,
                                          max_content_chars_per=10, cache_ttl=0)

        assert "clamped 10->500" in out.summary
        assert out.pages[0].content_chars == 500


# ─── 主题 2：同域归一与跨域丢弃 ───────────────────────────────────────────────


class TestSameDomainNormalization:

    @pytest.mark.parametrize("raw,expected", [
        # 大小写：主机归一，路径保留原样（/Page 与 /page 是两页）。
        ("HTTPS://Example.COM/Page", "https://example.com/Page"),
        # 默认端口不构成身份。
        ("https://example.com:443/a", "https://example.com/a"),
        ("http://example.com:80/a", "http://example.com/a"),
        # 片段不参与去重。
        ("https://example.com/a#section", "https://example.com/a"),
        # 跟踪参数丢弃，真实参数保留（?page=2 是分页不是跟踪）。
        ("https://example.com/list?utm_source=x&utm_medium=y&page=2",
         "https://example.com/list?page=2"),
        # 非根路径的尾斜杠折叠；根路径的 "/" 就是 "/"。
        ("https://example.com/docs/", "https://example.com/docs"),
        ("https://example.com/", "https://example.com/"),
    ])
    def test_normalize_url_cases(self, raw, expected):
        assert normalize_url(raw) == expected

    def test_scheme_is_part_of_identity(self):
        assert normalize_url("http://example.com/a") != normalize_url("https://example.com/a")

    def test_a_subdomain_is_off_domain(self):
        """netloc 判定：www. 也算另一台主机。这不是疏忽而是契约 —— 同域过滤的
        单位是 netloc，宽到 eTLD+1 是另一个（更危险的）决定。"""
        links = extract_same_domain_links(
            _page_with_links(["https://www.gates.example.com/a",
                              "https://sub.gates.example.com/b",
                              "/same"]),
            base_url=SEED, start_url=SEED)
        assert [u for u, _ in links] == ["https://gates.example.com/same"]

    @pytest.mark.asyncio
    async def test_bfs_never_fetches_off_domain_links(self):
        site = _FakeSite({SEED: _page_with_links(
            ["/kept", "https://other.example.com/x", "https://sub.gates.example.com/y"])})

        out = await crawl_mod.web_crawl(site, SEED, max_pages=5, max_depth=1,
                                          cache_ttl=0)

        assert all(urlparse_netloc_is_root(u) for u in site.fetched)
        assert out.pages_crawled == 2          # seed + /kept

    def test_assets_and_non_http_schemes_are_dropped(self):
        links = extract_same_domain_links(
            _page_with_links(["/style.css", "/app.js", "mailto:a@b.c",
                              "javascript:void(0)", "/real"]),
            base_url=SEED, start_url=SEED)
        assert [u for u, _ in links] == [SEED.rstrip("/") + "/real"]

    def test_relative_links_resolve_against_the_page(self):
        links = extract_same_domain_links(
            _page_with_links(["docs/x"]),
            base_url="https://gates.example.com/guide/", start_url=SEED)
        assert [u for u, _ in links] == ["https://gates.example.com/guide/docs/x"]

    def test_two_spellings_of_one_page_collapse_to_one_entry(self):
        """发现层的去重也在规范化之后：/a 与 /a/ 只进一次队列。"""
        links = extract_same_domain_links(
            _page_with_links(["/a", "/a/", "/a#x"]),
            base_url=SEED, start_url=SEED)
        assert len(links) == 1


def urlparse_netloc_is_root(u: str) -> bool:
    from urllib.parse import urlparse
    return urlparse(u).netloc == ROOT


# ─── 主题 3：子树前缀语义 ─────────────────────────────────────────────────────


class TestSubtreePatternContract:

    @pytest.mark.parametrize("spelling", ["/docs", "/docs/", "/docs/*", "docs", "docs/"])
    def test_five_spellings_of_one_subtree(self, spelling):
        assert normalize_path_pattern(spelling) == "/docs"
        assert path_allowed("/docs/x", [spelling]) is True
        assert path_allowed("/docs", [spelling]) is True

    @pytest.mark.parametrize("spelling", ["*", "/*"])
    def test_star_means_everything(self, spelling):
        assert normalize_path_pattern(spelling) == "/"
        assert path_allowed("/any/thing/at/all", [spelling]) is True

    @pytest.mark.parametrize("bad", ["/api/*/v1", "/do?cs", "/a[b]", "/docs/*/x"])
    def test_a_wildcard_anywhere_but_the_tail_is_rejected(self, bad):
        """中段通配符拒绝而不是悄悄匹配空集：一个安静返回 0 页的爬取，读起来
        就像「这站没内容」。拒绝要在任何请求之前（web_crawl 已在入口校验）。"""
        with pytest.raises(ValueError, match="not supported"):
            normalize_path_pattern(bad)

    @pytest.mark.parametrize("bad", ["", "   ", 123, None])
    def test_empty_and_non_string_patterns_are_rejected(self, bad):
        with pytest.raises(ValueError):
            normalize_path_pattern(bad)

    def test_exclude_beats_include_where_they_overlap(self):
        assert path_allowed("/a/c", ["/a"], ["/a/b"]) is True
        assert path_allowed("/a/b", ["/a"], ["/a/b"]) is False
        assert path_allowed("/a/b/deep", ["/a"], ["/a/b"]) is False

    def test_exclude_alone_keeps_its_subtree_out(self):
        assert path_allowed("/keep/this", None, ["/skip"]) is True
        assert path_allowed("/skip/this", None, ["/skip"]) is False


# ─── 主题 4：robots 预剪 ──────────────────────────────────────────────────────


class TestRobotsPrePrune:
    """g27 钉了节奏（Crawl-delay），这里钉「别把预算花在注定的拒绝上」。

    conftest 对全套件设 ``DHOLE_IGNORE_ROBOTS=1``；本组测的是 robots 在场的
    行为，所以和 g27 一样把环境变量摘掉、给缓存播种，两端都复位。
    """

    @pytest.fixture(autouse=True)
    def _live_robots_rules(self, monkeypatch):
        monkeypatch.delenv("DHOLE_IGNORE_ROBOTS", raising=False)
        robots.reset_robots_cache()
        get = robots.get_robots_cache()
        get.prime(SEED, "User-agent: *\nDisallow: /private\n")
        yield
        robots.reset_robots_cache()

    @pytest.mark.asyncio
    async def test_a_disallowed_seed_refuses_before_any_request(self):
        robots.get_robots_cache().prime(SEED, "User-agent: *\nDisallow: /\n")
        site = _FakeSite()

        out = await crawl_mod.web_crawl(site, SEED, max_pages=3, cache_ttl=0)

        assert site.fetched == []
        assert out.pages == []
        assert out.error.startswith("robots_disallowed")
        assert "ignore_robots=true" in (out.next_action or "")

    @pytest.mark.asyncio
    async def test_known_disallowed_links_are_pruned_without_a_request(self):
        """发现阶段预剪：/private/* 拿不到队列槽位，robots_skipped 记账。"""
        site = _FakeSite({SEED: _page_with_links(["/private/a", "/public/b"])})

        out = await crawl_mod.web_crawl(site, SEED, max_pages=5, max_depth=1,
                                          cache_ttl=0)

        assert "/private/a" not in site.fetched
        assert any(u.endswith("/public/b") for u in site.fetched)
        assert out.robots_skipped == 1
        assert "1 URL(s) skipped by robots.txt" in out.summary

    @pytest.mark.asyncio
    async def test_ignore_robots_fetches_what_pruning_would_drop(self):
        site = _FakeSite({SEED: _page_with_links(["/private/a"])})

        out = await crawl_mod.web_crawl(site, SEED, max_pages=3, max_depth=1,
                                          ignore_robots=True, cache_ttl=0)

        assert any(u.endswith("/private/a") for u in site.fetched)
        assert out.robots_skipped == 0

    @pytest.mark.asyncio
    async def test_unreadable_robots_fails_open_and_skips_nothing(self, monkeypatch):
        """robots 读不到 = 没查过 ≠ 查过且允许：页面照常进队列，计数不撒谎。
        （seed 检查的 try/except 就是这条路：verdict 为 None，爬取继续。）"""
        async def explode(url):
            raise OSError("robots.txt unreadable")
        monkeypatch.setattr(crawl_mod, "check_robots", explode)
        site = _FakeSite({SEED: _page_with_links(["/a"])})

        out = await crawl_mod.web_crawl(site, SEED, max_pages=3, max_depth=1,
                                          cache_ttl=0)

        assert len(site.fetched) == 2
        assert out.robots_skipped == 0
        assert out.error == ""
