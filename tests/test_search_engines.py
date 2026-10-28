"""test_search_engines.py —— 由 3 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import pytest
from bs4 import BeautifulSoup
from dhole_mcp.search_engines import DEFAULT_ENGINES
from dhole_mcp.search_metasearch import _TEXT_ENGINES
import time
from unittest.mock import AsyncMock
from dhole_mcp import search as search_mod
from dhole_mcp import server as server_mod
from dhole_mcp.models import ResponseModel
from dhole_mcp.search_engines import EngineReport, RawResult
from dhole_mcp.server import MasterFetchServer
import os
import subprocess
import sys


# ============================================================================
# 来源：test_engine_parsers.py
# ============================================================================
# 解析器契约测试：把"引擎答了、我们解析出 0 条"钉成一个会红的断言。

FIXTURE_DIR = Path(__file__).parent / "engine_fixtures"


@dataclass(frozen=True)
class Contract:
    engine: str          # _TEXT_ENGINES 里的名字
    fixture: str         # tests/engine_fixtures/<name>
    oracle_css: str      # 独立 parser 下的"结果容器"选择器
    must_not_start: tuple = ()   # 解码后不该残留的站内跳转前缀


CONTRACTS = [
    Contract("bing", "bing_variant_a.html", "li[class*='b_algo']",
             must_not_start=("https://www.bing.com/ck/a",)),
    # 版面 B 是这次真事故的那一版，它单独把 href 回退路径钉住
    Contract("bing", "bing_variant_b.html", "li[class*='b_algo']",
             must_not_start=("https://www.bing.com/ck/a",)),
    Contract("yandex", "yandex.html", "li[class*='serp-item']"),
    # baidu 的结果 href 是 baidu.com/link?url=<token> 包装，真链在容器的 mu 属性里。
    # 这条锁的就是"交出去的 href 不许是自家跳转包装"（浏览器打开会过期/带 token）。
    Contract("baidu", "baidu_variant_a.html", "div.result.c-container",
             must_not_start=("http://www.baidu.com/link", "https://www.baidu.com/link")),
    # B 版面（div.c-result）没有 mu，真链埋在 data-log 的 JSON 里 —— 与 bing 那次
    # "两套标题链接版面"同类，所以同样单独出契约：A 版绿不代表 B 版能读。
    Contract("baidu", "baidu_variant_b.html", "div.c-result",
             must_not_start=("http://www.baidu.com/link", "https://www.baidu.com/link",
                             "http://m.baidu.com", "https://m.baidu.com")),
    # 国内两家。真链都在 data-* 属性里（so.com/link?m= 与 sogou /link?url= 都是
    # 会过期的跳转包装），所以 must_not_start 锁的是"不许把包装当成结果 URL 交出去"。
    Contract("so360", "so360.html", "li.res-list:has([data-mdurl])",
             must_not_start=("https://www.so.com/link", "http://www.so.com/link")),
    # 选择器里的 :has([data-url]) 不是装饰：div.vrwrap 这个类名也被「相关搜索」聚合块
    # 复用，没有 data-url 的容器不是结果（实测 9 个里 2 个是聚合块）。
    Contract("sogou", "sogou_web.html", "div.vrwrap:has([data-url])",
             must_not_start=("/link?", "https://www.sogou.com/link",
                             "http://www.sogou.com/link")),
    # 国际版 bing 与 cn 版同一套容器/解析，但页面上**每条**链接都是 ck/a 跳转包装
    # （cn 版多数是直链）—— 解码器坏掉时 cn 版可能看不出来，这条就是为它出的。
    Contract("bing_global", "bing_global.html", "li[class*='b_algo']",
             must_not_start=("https://www.bing.com/ck/a",)),
    # 独立小网索引：容器是普通卡片，链接是直链；解析器只认卡片里的 h2/a。
    # oracle 数的是「卡片里的 h2 链接」而不是「含 h2 链接的卡片」：soupsieve 3.0 起
    # `:has()` 里不许放后代选择器（严格 CSS），而 2.x 允许 —— 数出来的都是 4，
    # 但 3.x 直接抛 SelectorSyntaxError。夹具里 4 张卡各有 1 颗 h2/a，两种数法同值。
    Contract("marginalia", "marginalia.html", "div.bg-white.p-4 h2 > a"),
    # Yahoo 两套版面各出一份契约。A 版是 div.algo.relsrch 卡片；B 版把首条放在
    # section.richAlgo、后续条目嵌套在 section.Sr 里 —— fixture 已剪掉嵌套子树，
    # 契约只钉“每张卡自己的第一个 h3/compText”。
    Contract("yahoo", "yahoo_variant_a.html", "div.algo.relsrch",
             must_not_start=("https://r.search.yahoo.com",)),
    Contract("yahoo", "yahoo_variant_b.html", "section.Sr, section.richAlgo",
             must_not_start=("https://r.search.yahoo.com",)),
    # 捷克自有索引：整站 class 是构建期哈希（随发版轮换），解析锚点是语义 data
    # 属性。oracle 数的是标题链接本身：一容器一链接，与容器数同值。
    Contract("seznam", "seznam.html", "a[data-e-a='heading']"),
    # 阿里系移动索引：结果卡带全套语义锚（data-sc/data-cmp=ti/qk-source-content），
    # 只收带标题链接的卡（百科/结构卡不算 web 行）。oracle 数标题链接，
    # 与收紧后的 items_xpath 同数（夹具上 8=8 实测）。
    Contract("shenma", "shenma.html", "#results div[data-cmp='ti'] a[href]"),
]

JS_SHELL = ("<html><body><div id='root'></div>"
            "<script>window.__jsl = true;</script>"
            "<div class='cookie-banner'>We use cookies to improve your "
            "experience. Accept</div></body></html>")


def _engine(name: str):
    return _TEXT_ENGINES[name](proxy=None, timeout=5, verify=True)


def _text(c: Contract) -> str:
    return (FIXTURE_DIR / c.fixture).read_text(encoding="utf-8")


def _oracle_count(c: Contract, text: str) -> int:
    """独立管线数容器：html.parser + soupsieve，与 lxml + items_xpath 无共享路径。"""
    return len(BeautifulSoup(text, "html.parser").select(c.oracle_css))


def _usable(rows):
    return [r for r in rows if getattr(r, "href", None) and getattr(r, "title", None)]


def _has(c: Contract) -> bool:
    return (FIXTURE_DIR / c.fixture).exists()


def _meta_of(html_name: str) -> Path:
    """fixture 的元数据路径：统一 stem 约定（x.html -> x.meta.json）。"""
    return (FIXTURE_DIR / html_name).with_suffix(".meta.json")


def _repo_bytes(p: Path) -> bytes:
    """夹具的**仓库字节**：`.gitattributes` 钉的是 `* text=auto eol=lf`，而本机
    `core.autocrlf=true` 会把工作树换成 CRLF。指纹要钉的是那份字节，不是这台机器的
    checkout —— 记录端（`tests/test_engine_fixtures_live.py` 的 `_sha`）折的是同一刀。
    """
    return p.read_bytes().replace(b"\r\n", b"\n")


CONTRACT_IDS = [c.fixture for c in CONTRACTS]


class TestParserContracts:
    """每个 fixture 上的六条断言。"""

    @pytest.mark.parametrize("c", CONTRACTS, ids=CONTRACT_IDS)
    def test_oracle_agrees_with_parser(self, c: Contract):
        if not _has(c):
            pytest.skip(f"{c.fixture} 尚未抓取")
        text = _text(c)
        rows = _engine(c.engine).extract_results(text) or []
        n_oracle = _oracle_count(c, text)
        assert len(rows) == n_oracle, (
            f"{c.engine}: items_xpath={_TEXT_ENGINES[c.engine].items_xpath!r} 匹配到 "
            f"{len(rows)} 个容器，独立 parser 在同一页面上看到 {n_oracle} 个 "
            f"—— xpath 与页面结构错位了，不是测试本身的问题")

    @pytest.mark.parametrize("c", CONTRACTS, ids=CONTRACT_IDS)
    def test_every_item_yields_href_and_title(self, c: Contract):
        """这条才是漂移警报：容器还在、子元素读不出，就是解析器跟不上页面了。

        仅比条数看不到它 —— 上面的 oracle 一致时，条目完全可以全是空的。
        """
        if not _has(c):
            pytest.skip(f"{c.fixture} 尚未抓取")
        rows = _engine(c.engine).extract_results(_text(c)) or []
        assert rows, "契约测试要求 fixture 至少解析出一条"
        bad = [i for i, r in enumerate(rows) if not (getattr(r, "href", "") and getattr(r, "title", ""))]
        assert not bad, (
            f"{c.engine}/{c.fixture}: 第 {bad} 个容器解析不出 href+title —— "
            f"elements_xpath 与页面结构错位（这就是引擎记成 empty 的原因）")

    @pytest.mark.parametrize("c", CONTRACTS, ids=CONTRACT_IDS)
    def test_no_usable_dropped_by_post_filter(self, c: Contract):
        """usable>0 而后处理归零 = 过滤器/解码器坏了，与 xpath 错位是两类故障。"""
        if not _has(c):
            pytest.skip(f"{c.fixture} 尚未抓取")
        eng = _engine(c.engine)
        rows = eng.extract_results(_text(c)) or []
        if not _usable(rows):
            pytest.skip(f"{c.fixture} 本身读不出可用条目 —— 先修上一条")
        kept = eng.post_extract_results(rows) or []
        assert kept, (
            f"{c.engine}: 解析出 {len(_usable(rows))} 条可用，post_extract_results "
            f"之后归零（解码/过滤逻辑坏了）")

    @pytest.mark.parametrize("c", CONTRACTS, ids=CONTRACT_IDS)
    def test_redirect_wrappers_are_decoded_away(self, c: Contract):
        if not _has(c) or not c.must_not_start:
            pytest.skip(f"{c.fixture}: 无需解码的站内跳转")
        eng = _engine(c.engine)
        kept = eng.post_extract_results(eng.extract_results(_text(c)) or []) or []
        leaked = [r.href for r in kept if str(r.href).startswith(c.must_not_start)]
        assert not leaked, f"{c.engine} 的跳转链接没被解码: {leaked[:2]}"

    @pytest.mark.parametrize("c", CONTRACTS, ids=CONTRACT_IDS)
    def test_hrefs_point_offsite(self, c: Contract):
        if not _has(c):
            pytest.skip(f"{c.fixture} 尚未抓取")
        eng = _engine(c.engine)
        kept = eng.post_extract_results(eng.extract_results(_text(c)) or []) or []
        assert kept
        own = {"bing": "bing.com", "bing_global": "bing.com",
               "yandex": "yandex."}.get(c.engine)
        if own:
            bad = [r.href for r in kept if own in str(r.href).split("/")[2:3][0]
                   or own in str(r.href)[:20]]
            assert not bad, f"{c.engine} 返回了引擎自己的页面而不是结果页: {bad[:2]}"

    @pytest.mark.parametrize("c", CONTRACTS, ids=CONTRACT_IDS)
    def test_bodies_within_a_band(self, c: Contract):
        """摘要非空比例用**带**而不是相等：body 的 xpath 是最先烂的那一层，
        而 0 摘要与 0 条目是两种不同的故障。"""
        if not _has(c):
            pytest.skip(f"{c.fixture} 尚未抓取")
        rows = _usable(_engine(c.engine).extract_results(_text(c)) or [])
        assert rows
        with_body = sum(1 for r in rows if (getattr(r, "body", "") or "").strip())
        assert with_body / len(rows) >= 0.5, (
            f"{c.engine}: {len(rows)} 条里只有 {with_body} 条有摘要，"
            f"body xpath 大概率已经错位")

    @pytest.mark.parametrize("c", CONTRACTS, ids=CONTRACT_IDS)
    def test_negative_control_invents_nothing(self, c: Contract):
        if not _has(c):
            pytest.skip(f"{c.fixture} 尚未抓取")
        eng = _engine(c.engine)
        assert eng.extract_results("<html><body><p>hello</p></body></html>") == []
        assert eng.last_extract == (0, 0), "契约的另一半：解析器不能凭空造结果"

    @pytest.mark.parametrize("c", CONTRACTS, ids=CONTRACT_IDS)
    def test_js_shell_reads_as_no_containers_not_as_drift(self, c: Contract):
        """JS 壳/同意墙页应当是"0 容器"，而不是"有容器但读不出"。

        这条把两类失败在 fixture 层分开：前者是上游把我们挡在门外，
        后者是我们的解析器坏了 —— 修法完全不同。
        """
        if not _has(c):
            pytest.skip(f"{c.fixture} 尚未抓取")
        eng = _engine(c.engine)
        eng.extract_results(JS_SHELL)
        assert eng.last_extract == (0, 0), (
            f"{c.engine}: JS 壳页被判出 {eng.last_extract} 个容器 —— "
            f"items_xpath 太宽，会把挑战页算成'解析器坏了'")


class TestFixtureAntiRot:
    """fixture 自己也会烂：仓库里那份 135KB 的 old.reddit 页面就烂过一次（17.0 随
    它的解析器一起删了），所以这里钉的是机制，不是某一个人的记性。"""

    def test_every_fixture_is_content_addressed(self):
        """记录的 sha256 是 **仓库字节** 的指纹：先把行尾折回 `* text=auto eol=lf` 的形状再算。

        2026-09-22 那一版把这条写成"只在工作树与仓库字节一致时成立"，于是 CRLF 残留的
        工作树让它红 —— 而它红的那一句是"fixture 被改动过"，一句假话：守卫本该测
        "这份夹具还是不是当初那份"，结果它测的是"这台机器怎么 checkout 的"。CI 与克隆者
        拿到 LF、本机 `core.autocrlf=true` 拿到 CRLF，同一份字节两种指纹，谁都不算错。
        现在折叠只发生在这条守卫的读数上：仓库里存的字节一字未动，改夹具仍然照红。
        """
        missing = [p.name for p in FIXTURE_DIR.glob("*.html")
                   if not _meta_of(p.name).exists()]
        assert not missing, f"这些 fixture 没有 .meta.json: {missing}"
        for p in FIXTURE_DIR.glob("*.html"):
            meta = _meta_of(p.name)
            if not meta.exists():
                continue
            want = json.loads(meta.read_text(encoding="utf-8")).get("sha256")
            got = hashlib.sha256(_repo_bytes(p)).hexdigest()[:16]
            assert want == got, (
                f"{p.name} 被改动过（{want} → {got}）。改 fixture 必须同时重新"
                f"记录 sha256，否则'契约'就只是看起来存在")

    def test_no_orphan_fixture(self):
        used = {c.fixture for c in CONTRACTS if _has(c)}
        on_disk = {p.name for p in FIXTURE_DIR.glob("*.html")}
        assert on_disk == used, (
            f"未被任何契约引用的 fixture: {sorted(on_disk - used)}；"
            f"契约引用却不存在的: {sorted(used - on_disk)}")

    def test_captured_at_is_a_real_date(self):
        """只校验"是不是个真日期"，绝不校验"新不新鲜"（见模块 docstring）。"""
        for meta in FIXTURE_DIR.glob("*.meta.json"):
            raw = json.loads(meta.read_text(encoding="utf-8")).get("captured_at")
            date.fromisoformat(raw)   # 抛异常即失败

    def test_no_secret_shaped_query_params(self):
        """fixture 会进仓库：不该留着 cookie / token / 签名会话参数。

        分隔符要同时认裸 `&` 与 HTML 转义后的 `&amp;`：搜狗结果链接里的会话 token
        长这样 `...&amp;token=50F4...`，只按裸 `&` 匹配会整片漏过 —— 这条守卫原先
        就是漏的，收 sogou fixture 时才发现。
        """
        import re
        bad = re.compile(r"(?:[?&]|&amp;)(?:Cookie|token|sig|auth|session|apikey|key)="
                         r"(?![Rr][Ee][Dd][Aa][Cc][Tt][Ee][Dd])[^&\"'\s<>]{8,}")
        offenders = []
        for p in FIXTURE_DIR.glob("*.html"):
            for m in bad.findall(p.read_text(encoding="utf-8", errors="replace"))[:2]:
                offenders.append(f"{p.name}: {m}")
        assert not offenders, f"fixture 里残留疑似凭据参数: {offenders}"


class TestChallengePagesAreBlocks:
    """实测形态：被限流时回的是 **200 + 几 KB 的校验页**（百度搜索页 1.4KB、360 的
    「访问异常」页同理），静态正文里没有结果也没有条目。它必须记成 blocked（进
    熔断冷却），不能以 0 条结果的形态混进 engine_empty —— 那会被读成"没有结果"。
    """

    SHELL = ("<html><head><title>360搜索</title></head><body>"
             "<div>访问异常</div><div>安全校验中…</div></body></html>")
    BAIDU_SHELL = ("<html><head><title>百度安全验证</title></head><body>"
                   "<p>请完成安全验证后继续访问</p></body></html>")

    def test_challenge_page_raises_blocked(self, monkeypatch):
        from dhole_mcp.search_metasearch import MetaBlockedException

        eng = _engine("so360")
        monkeypatch.setattr(eng, "request", lambda *a, **k: self.SHELL)
        with pytest.raises(MetaBlockedException) as exc:
            eng.search("人工智能")
        assert "校验" in str(exc.value) and "so360" in str(exc.value)

    def test_large_page_mentioning_security_is_not_a_challenge(self):
        """体积闸门：正常页面内嵌的脚本里也写着"安全校验"，只按关键字匹配会把真页面
        误判成拦截页（那会把好结果丢掉，比漏判更糟）。"""
        from dhole_mcp.search_metasearch import _is_challenge_shell

        page = ("<html><head><script>" + "x" * 30000 + "安全校验"
                + "</script></head><body>real serp</body></html>")
        assert _is_challenge_shell(page) is False
        assert _is_challenge_shell(self.BAIDU_SHELL) is True
        assert _is_challenge_shell(self.SHELL) is True
        # 搜狗实测形态：5.4KB 的 200 页 + "验证"
        assert _is_challenge_shell("<html><body>请输入验证码</body></html>") is True

    def test_baidu_retries_once_then_blocks(self, monkeypatch):
        """搜索页的拦截是偶发的（实测 ~1/16），所以先重试一次；两次都被拦才熔断。"""
        from dhole_mcp.search_metasearch import MetaBlockedException

        eng = _engine("baidu")
        calls = {"n": 0}

        def always_challenged(*a, **k):
            calls["n"] += 1
            return self.BAIDU_SHELL

        monkeypatch.setattr(eng, "request", always_challenged)
        monkeypatch.setattr(eng, "build_payload", lambda **k: {})
        with pytest.raises(MetaBlockedException):
            eng.search("python")
        assert calls["n"] == 2, "应当恰好重试一次"

    def test_baidu_challenge_then_real_page_returns_rows(self, monkeypatch):
        eng = _engine("baidu")
        pages = [self.BAIDU_SHELL,
                 (FIXTURE_DIR / "baidu_variant_a.html").read_text(encoding="utf-8")]
        monkeypatch.setattr(eng, "request", lambda *a, **k: pages.pop(0))
        monkeypatch.setattr(eng, "build_payload", lambda **k: {})
        rows = eng.search("python")
        assert rows, "重试拿到真页面后必须交出结果"
        assert all(r.href.startswith(("http://", "https://")) for r in rows)

class TestResultHrefHygiene:
    """从 data-* 属性里取目标 URL 的卫生规则（360 那个坑是实测出来的）：

    360 的视频聚合卡把多个 URL 拼在同一个 ``data-mdurl`` 里，原样交出去会得到
    ``...&srcg=...https://www.douyin.com/video/…https://www.bilibili.com/video/…``
    这种打不开的链接 —— 与其让 agent 拿到废链，不如丢掉这一条。
    """

    CONCATENATED = ("https://tv.360kan.com/s?q=python教程&src=mohe-short_video_vertical"
                    "https://www.douyin.com/video/7627751835104726291?from=360box"
                    "https://www.bilibili.com/video/BV1fWLv6qERF")

    def test_concatenated_urls_are_dropped(self):
        from dhole_mcp.search_metasearch import _clean_result_href

        assert _clean_result_href(self.CONCATENATED, ("so.com",)) == ""
        assert _clean_result_href("https://a.test/x https://b.test/y", ("so.com",)) == ""

    def test_engine_own_hosts_are_dropped(self):
        from dhole_mcp.search_metasearch import _clean_result_href

        assert _clean_result_href("https://www.so.com/link?m=abc", ("so.com",)) == ""
        assert _clean_result_href("https://so.com/x", ("so.com",)) == ""
        assert _clean_result_href("https://www.sogou.com/link?url=x", ("sogou.com",)) == ""
        # 只是长得像的不算（notso.com 不是 so.com 的子域）
        assert _clean_result_href("https://notso.com/x", ("so.com",)) == "https://notso.com/x"

    def test_normal_urls_survive(self):
        from dhole_mcp.search_metasearch import _clean_result_href

        for url in ("https://www.runoob.com/python3/python-asyncio.html",
                    "http://www.cnblogs.com/Red-Sun/p/16934843.html",
                    "https://zhuanlan.zhihu.com/p/1995095899534284369"):
            assert _clean_result_href(url, ("baidu.com", "so.com", "sogou.com")) == url
        assert _clean_result_href("", ("so.com",)) == ""
        assert _clean_result_href("javascript:void(0)", ("so.com",)) == ""


class TestChallengeStatusesAreBlocks:
    """实测：DuckDuckGo 的 html 端点在限流时回 **202** + 反爬挑战页（"Unfortunately,
    bots use DuckDuckGo too…"），不是 403。只认 200 的 request() 会让它变成 0 条结果
    的 empty —— 那会被读成"这个查询没结果"。所以 202 只在 DDG 这家算被拦；其它引擎
    的 202 语义没有观测，不替它们猜。
    """

    @staticmethod
    def _engine(cls_name: str, status: int):
        from dhole_mcp import search_metasearch as ms

        eng = ms._TEXT_ENGINES[cls_name](proxy=None, timeout=5)

        class _Resp:
            status_code = status
            text = "<html>challenge</html>"

        class _Client:
            def request(self, *a, **k):
                return _Resp()

        eng.http_client = _Client()
        return eng

    def test_ddg_202_is_a_block(self):
        from dhole_mcp.search_metasearch import MetaBlockedException

        eng = self._engine("duckduckgo", 202)
        with pytest.raises(MetaBlockedException):
            eng.request("POST", "https://html.duckduckgo.com/html/", data={})

    def test_other_engines_202_is_not_promoted(self):
        eng = self._engine("bing", 202)
        assert eng.request("GET", "https://cn.bing.com/search") is None

    def test_429_is_a_block_for_every_engine(self):
        """429 不是要"观测"的引擎私货：RFC 6585 定死就是 Too Many Requests。

        实测（2026-09-22，用户换代理后）：brave 在新出口 IP 上回 **429 + 反爬壳**，
        旧行为把它记成 empty —— 引擎明明在限流，报告里却写"这个查询没结果"，
        既不冷却也不再重试，用户看到的是一句假话。
        """
        from dhole_mcp.search_metasearch import MetaBlockedException

        for cls_name in ("brave", "bing", "duckduckgo"):
            eng = self._engine(cls_name, 429)
            with pytest.raises(MetaBlockedException):
                eng.request("GET", "https://example.test/search")


class TestBingGlobalReusesTheCnParser:
    """国际版 bing 是**同一家、另一套索引**（实测标题只有 1/17 重合），但页面结构同源：
    必须复用 cn 版的 ck/a 解码与双版面 href 回退，而不是另起一套（另起一套 = 解码器
    失效时整轮结果全是 bing.com/ck/a 跳转链接）。
    """

    def test_inherits_bing_and_points_at_www(self):
        from dhole_mcp.search_metasearch import Bing

        cls = _TEXT_ENGINES["bing_global"]
        assert issubclass(cls, Bing), "国际版必须继承 Bing（ck/a 解码 + 双版面 href）"
        assert cls.search_url.startswith("https://www.bing.com/search")
        assert cls.provider == "bing", "provider 仍是 bing 家族：与 bing/ddg/yahoo 同源"

    def test_family_merges_with_bing(self):
        """家族标签不许按入口分：bing_global 与 bing 同源，共识分母不能虚报。"""
        from dhole_mcp.search_engines import _INDEX_FAMILY

        assert _INDEX_FAMILY["bing_global"] == _INDEX_FAMILY["bing"] == "bing"


class TestDefaultPoolCoverage:
    """默认池里谁的契约还缺 —— 缺要看得见，不能假装覆盖过了。"""

    def test_uncovered_default_engines_are_reported(self):
        covered = {c.engine for c in CONTRACTS if _has(c)}
        pending = sorted(set(DEFAULT_ENGINES) - covered)
        if not pending:
            return
        pytest.skip(
            f"默认池中这些引擎还没有真页面契约（本机网络不可达，未捏造假 fixture）："
            f"{pending}。补齐方式："
            f"`python -m pytest -m live --engine-fixtures=capture "
            f"tests/test_engine_fixtures_live.py`")


class TestYieldDataReachesTheReport:
    """gate 的判据必须真的从 metasearch 流到 EngineReport。

    这条专门盯接线：`_metasearch` 缓存的是函数而不是模块，曾经让产出数据静默变成
    空字典 —— 于是"解析器坏了就不重复打全量 fan-out"的判据永远拿不到数字，
    而所有纯函数测试照样全绿。
    """

    @pytest.mark.asyncio
    async def test_reports_carry_item_nodes_and_usable(self, monkeypatch):
        from dhole_mcp import search_metasearch as ms

        async def fake_metasearch(q, n, **kw):
            return [{"title": "t", "href": "https://a.test", "body": "b",
                     "backend": "bing", "backends": ["bing"]}], {"bing": "empty"}

        ms._ENGINE_YIELD.clear()
        ms._ENGINE_YIELD["bing"] = {"last_nodes": 5, "last": 0, "verdict": "parser_drift",
                                    "status": "empty", "mean": 0, "drift": 2, "zero": 0,
                                    "ts": __import__("time").time()}
        monkeypatch.setattr("dhole_mcp.search_engines._metasearch", fake_metasearch)
        _, reports = await _run_multi_search()
        assert reports[0].item_nodes == 5
        assert reports[0].usable == 0
        assert reports[0].yield_verdict == "parser_drift"
        ms._ENGINE_YIELD.clear()


async def _run_multi_search():
    from dhole_mcp.search_engines import multi_search
    return await multi_search("q", 6)


class TestYahooRedirectWrappers:
    """Yahoo rotates between r.search. and rd.search.; both are the same wrapper.

    实测（2026-10-20）：同一查询在不同轮次里交回直接链接或 rd.search.yahoo.com
    包装。只认 r.search 会让后一种把带会话 token 的跳转页当成结果 URL 交出去。
    """

    def test_both_redirect_hosts_decode_to_the_target(self):
        from dhole_mcp.search_metasearch import _yahoo_decode_url

        for host in ("r.search.yahoo.com", "rd.search.yahoo.com"):
            url = (f"https://{host}/_ylt=x/RV=2/RE=1/RO=10/"
                   "RU=https%3a%2f%2fexample.com%2fa/RK=2/RS=y")
            assert _yahoo_decode_url(url) == "https://example.com/a", host

    def test_an_undecodable_redirect_is_dropped(self):
        from dhole_mcp.search_metasearch import _yahoo_decode_url

        assert _yahoo_decode_url(
            "https://rd.search.yahoo.com/_ylt=x/RK=2") == ""

    def test_direct_links_pass_through(self):
        from dhole_mcp.search_metasearch import _yahoo_decode_url

        assert _yahoo_decode_url("https://example.com/a") == "https://example.com/a"


class TestMarginaliaRateLimit:
    def test_the_200_challenge_is_blocked_not_empty(self):
        from dhole_mcp.search_metasearch import (
            MetaBlockedException, _TEXT_ENGINES,
        )

        eng = _TEXT_ENGINES["marginalia"](timeout=5)
        with pytest.raises(MetaBlockedException):
            eng._check_challenge(
                "<html><body>The search engine is currently seeing a lot of "
                "fairly aggressive bot activity. Please wait for 1 seconds"
                "</body></html>")

    def test_http_400_is_a_challenge_status(self):
        from dhole_mcp.search_metasearch import _TEXT_ENGINES

        assert 400 in _TEXT_ENGINES["marginalia"]._challenge_statuses


# ============================================================================
# 来源：test_inline_fetch_stops_before_the_host_timeout.py
# ============================================================================
# R2刀1（P2-5）：`fetch_content=true` 的内联扇出没有总量闸，两次复现把整调用撞死在宿主的 30s 超时上。

HOST_MS = 30_000
RESERVE_MS = 2_000


class _Clock:
    """替身时钟：server_mod.now 是从全局 time 借来的名字，换模块属性而不是 patch
    time 模块本身，否则别的模块的 now() 会调到同一根针上。起点用真实 epoch，
    免得谁拿它去减一个真实文件 mtime 得出一个荒谬的年龄。"""

    def __init__(self):
        self.t0 = time.time()
        self.t = self.t0

    def __call__(self) -> float:
        return self.t

    def advance(self, ms: float) -> None:
        self.t += ms / 1000.0

    @property
    def elapsed_ms(self) -> float:
        return (self.t - self.t0) * 1000


def _wire(monkeypatch, n_results: int = 3, search_ms: int = 3_000):
    """装一条可控的搜索链：结果数、搜索本身花掉多少时间，都由测试说。"""
    clock = _Clock()

    async def fake_multi_search(query, max_results, **kwargs):
        clock.advance(search_ms)
        rows = [RawResult(title=f"widgets doc {i}",
                          url=f"https://a{i}.example.test/widgets-{i}",
                          snippet=f"widgets guide number {i} of {n_results}",
                          source="brave", position=i + 1)
                for i in range(n_results)]
        return rows, [EngineReport(name="brave", ok=True)]

    monkeypatch.setattr(search_mod, "multi_search", fake_multi_search)
    # fetch_content 只抓 fetch_relevance=="high" 的前 3 条。merge 打分下第三行会掉到
    # med，扇出因此少一页、预算算术就对不上；这里钉住三行全 high（与共识扇出那份
    # 夹具同一手法），让"几页"由测试说了算。
    monkeypatch.setattr(search_mod, "ensure_reranker", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        search_mod, "neural_rerank",
        lambda q, ranked, **kw: [(r, 0.9 - 0.05 * i) for i, r in enumerate(ranked)])
    monkeypatch.setattr(server_mod, "now", clock)
    return clock


def _ok_page(url: str) -> ResponseModel:
    return ResponseModel(url=url, status=200, content=["real page text"],
                         content_ok=True)


def _server_with_slow_pages(clock: _Clock, spend_ms: list[int],
                            spend_full: bool = False):
    """每页按 spend_ms 消耗预算（0 = 秒回），并把收到的 timeout 留在 call_timeouts 上。

    spend_full=True 让每页花满「配额 + 兜底宽限」——一页实际占用的时间不止它的
    timeout，最坏情况那条不变量要按这个口径量。
    """
    server = MasterFetchServer()
    state = {"call_timeouts": [], "n": 0}

    async def fake_web_read(**kwargs):
        i = state["n"]
        quota = int(kwargs.get("timeout") or 0)
        state["call_timeouts"].append(quota)
        state["n"] += 1
        if spend_full:
            clock.advance(quota + server_mod.INLINE_PAGE_GRACE_MS)
        else:
            clock.advance(spend_ms[i] if i < len(spend_ms) else 0)
        return _ok_page(kwargs.get("url", "u"))

    server.web_read = AsyncMock(side_effect=fake_web_read)
    return server, state


class TestTheInlineBudget:

    @pytest.mark.asyncio
    async def test_worst_case_spending_still_lands_inside_the_host_budget(self, monkeypatch):
        """每页都花满配额 + 兜底宽限（一页实际占用的时间）时，整条内联链仍落在 30s 内。

        这条是不变量测试，不重复实现里的算式：配额可以调，「最坏情况不许超宿主的耐心」
        不许变。修前 3 页 × 15s = 45s，宿主在 30s 就杀。
        """
        clock = _wire(monkeypatch, n_results=3, search_ms=2_000)
        server, state = _server_with_slow_pages(clock, [], spend_full=True)

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        spent_ms = clock.elapsed_ms
        assert spent_ms <= HOST_MS - 1_000, (
            f"内联扇出最坏花了 {spent_ms:.0f}ms，宿主 30s 已到")
        assert len(out.fetched_pages) == 3, "每一页都要有一行"
        assert all(t <= server_mod.INLINE_PAGE_CAP_MS
                   for t in state["call_timeouts"])

    @pytest.mark.asyncio
    async def test_each_page_gets_a_share_not_the_whole_budget(self, monkeypatch):
        """3 页 × 15s = 45s 的结构超支：修后第一页的上限是「剩余预算 ÷ 剩余页数」。

        这里断言的是**配额**而不是配额的加总：配额按实时余额逐页重算，秒回的页不会
        把自己那份花掉，于是后一页的配额可以变大。真正兜住时间的是上面那条最坏情况。
        """
        clock = _wire(monkeypatch, n_results=3, search_ms=3_000)
        server, state = _server_with_slow_pages(clock, [0, 0, 0])

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        assert len(state["call_timeouts"]) == 3
        left = HOST_MS - 3_000 - RESERVE_MS
        assert state["call_timeouts"][0] <= left // 3, \
            "搜索只花了 3s，第一页还拿到整份 15s —— 总量闸没生效"
        assert state["call_timeouts"][0] >= server_mod.INLINE_PAGE_FLOOR_MS
        assert all(p["content_ok"] is True for p in out.fetched_pages), \
            "配额收紧不该让秒回的成功页变成洞"

    @pytest.mark.asyncio
    async def test_a_slow_page_leaves_the_rest_the_leftover(self, monkeypatch):
        """预算按实时余额重算：第一页烧掉 14s，第二页的配额只剩余额的一份。"""
        clock = _wire(monkeypatch, n_results=3, search_ms=1_000)
        server, state = _server_with_slow_pages(clock, [14_000, 0, 0])

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        left_after_page1 = HOST_MS - 1_000 - RESERVE_MS - 14_000
        assert state["call_timeouts"][1] <= left_after_page1 // 2
        assert state["call_timeouts"][1] < 15_000, \
            "第一页烧掉 14s 之后，第二页还拿到整份 15s —— 余额没跟着走"
        assert len(out.fetched_pages) == 3, "三页都该有行，成功的页不许被静默丢掉"

    @pytest.mark.asyncio
    async def test_the_run_stops_when_the_budget_is_out(self, monkeypatch):
        """到点即停：剩下的页不再发包，但每页仍占一行并说明为什么是洞。"""
        clock = _wire(monkeypatch, n_results=3, search_ms=20_000)
        server, state = _server_with_slow_pages(clock, [6_000, 0, 0])

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        assert server.web_read.await_count == 1, "预算已尽还在发第 2 页"
        assert len(out.fetched_pages) == 3
        assert out.fetched_pages[0]["content_ok"] is True
        for row in out.fetched_pages[1:]:
            assert row["content_ok"] is False
            assert row["content"] == ""
            assert row["error"] == "inline_fetch_budget"
            assert row["url"], "被砍的页也要报是哪一条"
        assert "inline_fetch_budget" in out.summary

    @pytest.mark.asyncio
    async def test_a_search_that_ate_the_budget_fetches_nothing(self, monkeypatch):
        """搜索本身超时（28s）时不再追发包：一发就必然被宿主杀，搜索结果也一起丢。"""
        clock = _wire(monkeypatch, n_results=3, search_ms=28_000)
        server, state = _server_with_slow_pages(clock, [0, 0, 0])

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        server.web_read.assert_not_awaited()
        assert [r["error"] for r in out.fetched_pages] == ["inline_fetch_budget"] * 3
        assert out.results, "搜索结果本身必须还在——被杀的是抓取，不是整次调用"

    @pytest.mark.asyncio
    async def test_healthy_pages_are_untouched(self, monkeypatch):
        """快路径行为不变：三页秒回就三页正文，没有洞、没有注记。"""
        clock = _wire(monkeypatch, n_results=3, search_ms=500)
        server, state = _server_with_slow_pages(clock, [0, 0, 0])

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        assert len(out.fetched_pages) == 3
        assert all(p["content_ok"] is True for p in out.fetched_pages)
        assert all("error" not in p for p in out.fetched_pages)
        assert "inline_fetch_budget" not in out.summary


class TestEveryHoleCarriesAReason:
    """失败占位的 error 字段此前只在「web_read 抛异常」时才有；正常返回一条空正文的
    失败页时那一行 content_ok=false + content="" 且**无 error**，与注释的承诺矛盾。"""

    @pytest.mark.asyncio
    async def test_a_page_that_returns_empty_without_error_still_gets_a_reason(
            self, monkeypatch):
        _wire(monkeypatch, n_results=1, search_ms=500)
        server = MasterFetchServer()
        server.web_read = AsyncMock(return_value=ResponseModel(
            url="https://a0.example.test/widgets", status=200, content=[],
            content_ok=False))

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        row = out.fetched_pages[0]
        assert row["content_ok"] is False
        assert row["content"] == ""
        assert row.get("error"), "洞开着却没有 reason"

    @pytest.mark.asyncio
    async def test_the_reason_names_what_the_page_reported(self, monkeypatch):
        """页侧已经说出来的原因要原样带上来，不要在组装处再编一句空话。"""
        _wire(monkeypatch, n_results=1, search_ms=500)
        server = MasterFetchServer()
        server.web_read = AsyncMock(return_value=ResponseModel(
            url="https://a0.example.test/widgets", status=200, content=[],
            content_ok=False, page_type="js_shell",
            diagnosis={"code": "js_shell", "retryable": True, "recover_with": ""}))

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        assert "js_shell" in out.fetched_pages[0]["error"]

    @pytest.mark.asyncio
    async def test_a_page_error_wins_over_the_fallback(self, monkeypatch):
        _wire(monkeypatch, n_results=1, search_ms=500)
        server = MasterFetchServer()
        server.web_read = AsyncMock(return_value=ResponseModel(
            url="https://a0.example.test/widgets", status=503, content=[],
            content_ok=False, error="http 503: upstream gave up"))

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        assert out.fetched_pages[0]["error"] == "http 503: upstream gave up"

    @pytest.mark.asyncio
    async def test_a_long_reason_keeps_its_cure(self, monkeypatch):
        """重启后实测：塌陷页那一行的 error 停在 "…Do NOT re-fetch for rendering; "。

        页侧那句 200 字之后还有出路（`take extraction_type='html' (or 'text')`），而组装处
        的 `[:200]` 把它剪掉了 —— 洞是解释了，能照做的那半句没了。这一行是响应里唯一带这句
        话的地方（没有别的字段兜着），所以剪它等于剪掉答案。
        """
        reason = ("extraction_collapsed: this page's own markup holds 1895 characters of visible "
                  "text while the extract returned 467 - the words ARE in the HTML, dhole's reader "
                  "lost them. Do NOT re-fetch for rendering; take extraction_type='html' (or 'text') "
                  "for this URL instead.")
        assert len(reason) > 200, "夹具要长过旧帽，否则测不到这一刀"
        _wire(monkeypatch, n_results=1, search_ms=500)
        server = MasterFetchServer()
        server.web_read = AsyncMock(return_value=ResponseModel(
            url="https://a0.example.test/widgets", status=200, content=[],
            content_ok=False, error=reason))

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        assert out.fetched_pages[0]["error"] == reason

    @pytest.mark.asyncio
    async def test_an_oversized_reason_is_cut_at_a_word_and_says_so(self, monkeypatch):
        """外来长文本仍要有帽，但帽落在词界上，并且明说被剪过 —— 不留半截词、不装作完整。"""
        _wire(monkeypatch, n_results=1, search_ms=500)
        server = MasterFetchServer()
        server.web_read = AsyncMock(return_value=ResponseModel(
            url="https://a0.example.test/widgets", status=200, content=[],
            content_ok=False, error="parser dump token " * 200))

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        row = out.fetched_pages[0]["error"]
        assert len(row) <= server_mod.HOLE_REASON_CAP + 40
        assert row.endswith(" [reason clipped]")
        assert "dump token" in row and not row[:-len(" [reason clipped]")].endswith(" tok")

    @pytest.mark.asyncio
    async def test_a_clean_page_still_carries_no_error_key(self, monkeypatch):
        """反向不许漂：给成功行硬塞一个空 error 会让调用方把"没问题"读成"有问题"。"""
        _wire(monkeypatch, n_results=1, search_ms=500)
        server = MasterFetchServer()
        server.web_read = AsyncMock(return_value=_ok_page("u"))

        out = await server.web_search("widgets doc", fetch_content=True, cache_ttl=0)

        assert "error" not in out.fetched_pages[0]


# ============================================================================
# 来源：test_cli.py
# ============================================================================
# CLI tests: the entry point's structure, the doctor/engines subcommand surface.

_SRC = str(Path(__file__).resolve().parent.parent / "src")

_BLOCK_HEAVY_DEPS = """
import sys, importlib.abc

BLOCK = {"mcp", "primp", "patchright", "playwright", "pydantic",
         "trafilatura", "aiosqlite", "httpx", "lxml", "onnxruntime"}

class _Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in BLOCK:
            raise ImportError(f"blocked: {fullname}")
        return None

sys.meta_path.insert(0, _Blocker())
import dhole_mcp.cli
assert callable(dhole_mcp.cli.main)
print("CLI_IMPORT_OK")
"""


# ─── CLI self-heal behaviour ───────────────────────────────────────

class TestCLIStructure:

    def test_cli_imports_without_heavy_deps(self):
        """cli.py 必须在重依赖全部不可导入时仍可导入（自愈前提）。

        行为测试：子进程中用 import hook 把 mcp/primp/patchright/...
        全部变成 ImportError，再导入 dhole_mcp.cli。若有人给 cli.py
        加了模块级重依赖导入，坏安装下 dhole 命令会彻底报废——这个
        测试会在那时真实失败。（旧的 hasattr 存在性检查做不到这一点，
        已删。）"""
        result = subprocess.run(
            [sys.executable, "-c", _BLOCK_HEAVY_DEPS],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "PYTHONPATH": _SRC},
        )
        assert result.returncode == 0, (
            f"cli import failed with heavy deps blocked:\n"
            f"{result.stdout}\n{result.stderr}"
        )
        assert "CLI_IMPORT_OK" in result.stdout


class TestSearchPoolRow:
    """`dhole -v` 的 search pool 行曾经是写死的字面量，15.0 换引擎池时漏改，面板里
    报的还是旧池（bing,duckduckgo,…,sogou_weixin）。现在从 DEFAULT_ENGINES 推导，
    这条钉住它不许再漂 —— 与 instructions 那条 `searches N engines` 同一类守卫。
    """

    @staticmethod
    def _pool_row(monkeypatch, env_value: str | None) -> str:
        from dhole_mcp import updater

        if env_value is None:
            monkeypatch.delenv("DHOLE_DEFAULT_ENGINES", raising=False)
        else:
            monkeypatch.setenv("DHOLE_DEFAULT_ENGINES", env_value)
        rows = {label: state for label, state, _ok in updater.capabilities()}
        return rows["search pool"]

    def test_row_lists_the_real_pool_and_nothing_else(self, monkeypatch):
        """池子换成什么样都行，但印出来的必须**恰好**是它 —— 不靠手写退役名单反查。

        旧写法是 `for gone in (sogou_weixin, wikipedia, ...)`：那种名单每次删引擎都要
        跟着改，改漏一次守卫就静默失效。现在正向比对：主体段 = 池子的逗号串，
        附注里点名的 VPN 引擎必须是池内名字。
        """
        from dhole_mcp.search_engines import DEFAULT_ENGINES

        row = self._pool_row(monkeypatch, None)
        assert row.split(" (")[0] == ",".join(DEFAULT_ENGINES), row
        if " (default; " in row:
            named = row.split(" (default; ")[1].split(" need VPN")[0]
            extra = set(named.split(",")) - set(DEFAULT_ENGINES)
            assert not extra, f"VPN 注记点名了池外引擎: {sorted(extra)}"

    def test_env_override_still_wins(self, monkeypatch):
        row = self._pool_row(monkeypatch, "bing,yandex")
        assert row == "bing,yandex"


# ─── W5 · CLI syntax unification ────────────────────────────────────
class TestDoctorSubcommandSyntax:
    """`dhole doctor` == `dhole --doctor`.

    Before W5 the four management subcommands (model / proxy / engines /
    skill) dispatched manually *before* argparse, but `doctor` was an
    argparse flag. Result: the intuitive spelling `dhole doctor` fell
    through to argparse and errored with `unrecognized arguments: doctor`
    without ever mentioning `--doctor`. These tests pin the fix.
    """

    @pytest.fixture
    def env_isolated(self, tmp_path, monkeypatch):
        from dhole_mcp import env_registry as _reg
        for e in _reg.ALL:
            monkeypatch.delenv(e.name, raising=False)
        monkeypatch.setenv("DHOLE_HOME", str(tmp_path / "home"))

    @pytest.mark.parametrize("argv_tail", [("doctor",), ("--doctor",)])
    def test_both_spellings_produce_identical_stdout_and_exit(self,
                                                              env_isolated,
                                                              monkeypatch,
                                                              capsys,
                                                              argv_tail):
        """Same env, same tmp home, both invocations must yield byte-identical
        stdout AND the same exit code (0/1 depending on install state)."""
        from dhole_mcp import ops as srv

        monkeypatch.setattr(sys, "argv", ["dhole", *argv_tail])
        code_a = None
        out_a = ""
        try:
            srv.main()
        except SystemExit as exc:
            code_a = exc.code if exc.code is not None else 0
        out_a = capsys.readouterr().out

        monkeypatch.setattr(sys, "argv", ["dhole", *(("--doctor",) if argv_tail == ("doctor",) else ("doctor",))])
        try:
            srv.main()
        except SystemExit as exc:
            code_b = exc.code if exc.code is not None else 0
        out_b = capsys.readouterr().out

        assert code_a == code_b
        assert out_a == out_b
        assert "all healthy" in out_a or "issue(s) found" in out_a

    def test_unknown_subcommand_exits_2_and_suggests_the_closest(
            self, env_isolated, monkeypatch, capsys):
        """`dhole doctoooor` was a "usage" dump with no hint. Now it says
        exactly what the plan wanted: `did you mean: dhole doctor?`."""
        from dhole_mcp import ops as srv

        monkeypatch.setattr(sys, "argv", ["dhole", "doctoooor"])
        try:
            srv.main()
        except SystemExit as exc:
            code = exc.code
        else:
            raise AssertionError("main() should exit for an unknown subcommand")
        err = capsys.readouterr().err
        assert code == 2
        assert "unknown subcommand: doctoooor" in err
        assert "did you mean: dhole doctor" in err

    def test_flag_first_args_still_reach_argparse(self, env_isolated,
                                                  monkeypatch, capsys):
        """The unknown-subcommand guard fires only for a bare positional that
        is not a flag. `-v`, `--http`, `-u` etc. must still parse normally."""
        from dhole_mcp import ops as srv

        monkeypatch.setattr(sys, "argv", ["dhole", "--help"])
        with pytest.raises(SystemExit) as excinfo:
            srv.main()
        # argparse uses exit 0 for --help.
        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert "dhole doctor" in out
        assert "dhole --doctor" in out


class TestEnginesProbe:
    """`dhole engines probe` —— 逐引擎实测的那个子命令。

    它存在的理由是"这家今天到底能不能用"在真实搜索里问不出来（早退配额会把慢的
    引擎取消掉），所以它必须**一家一次**地打、并且把 metasearch 的原始状态如实
    印出来。下面几条钉的都是它的输出契约，不碰网络。
    """

    @staticmethod
    def _run(monkeypatch, capsys, args, reports=None, cooldowns=None):
        from dhole_mcp import search_engines as se
        from dhole_mcp import ops as srv
        from dhole_mcp.search_engines import EngineReport

        given = reports or {}
        asked: list[str] = []

        async def fake_multi(query, max_results=10, *, engines=None, **kw):
            name = list(engines or [])[0]
            asked.append(name)
            rep = given.get(name) or EngineReport(name=name, ok=True, status="ok",
                                                  item_nodes=5, usable=5)
            return ([object()] * 3 if rep.ok else []), [rep]

        monkeypatch.setattr(se, "multi_search", fake_multi)
        monkeypatch.setattr(se, "_cooldowns", lambda: dict(cooldowns or {}))
        code = srv._engines_probe(args)
        captured = capsys.readouterr()
        # 用法错误按 CLI 的约定走 stderr；人类摘要仍在 stdout。两条合起来读。
        return code, captured.out + captured.err, asked

    def test_probes_every_default_engine_one_at_a_time(self, monkeypatch, capsys):
        from dhole_mcp.search_engines import DEFAULT_ENGINES

        code, out, asked = self._run(monkeypatch, capsys, [])
        assert asked == list(DEFAULT_ENGINES), "一家一次，顺序就是池子顺序"
        assert f"{len(DEFAULT_ENGINES)} of {len(DEFAULT_ENGINES)}" in out
        assert code == 0

    def test_names_the_status_code_instead_of_saying_no_results(self, monkeypatch, capsys):
        """302 没跟进 = 没看到结果页，输出里必须说得出是哪个状态码。"""
        from dhole_mcp.search_engines import EngineReport

        _, out, _ = self._run(
            monkeypatch, capsys, ["bing_global"],
            reports={"bing_global": EngineReport(
                name="bing_global", status="empty",
                error="no parsable results (HTTP 302, redirect not followed)")})
        assert "HTTP 302" in out and "redirect not followed" in out

    def test_does_not_knock_on_a_cooling_engine(self, monkeypatch, capsys):
        """点名单引擎时它在冷却里的话，metasearch 会抛"No engines could start"，
        输出就变成一个看不懂的 error —— 所以冷却要自己先判、干脆不发请求。"""
        code, out, asked = self._run(monkeypatch, capsys, ["so360"],
                                     cooldowns={"so360": 55.0})
        assert asked == [], "冷却中的引擎不许被探针敲"
        assert "cooling" in out and "55s left" in out
        assert code == 1, "没有任何引擎答话时退出码非零（脚本里能用）"

    def test_alias_resolves_to_the_backend_the_cooldown_is_keyed_by(self, monkeypatch, capsys):
        _, out, asked = self._run(monkeypatch, capsys, ["360"],
                                  cooldowns={"so360": 40.0})
        assert asked == [] and "cooling" in out, "别名 360 要认得 so360 的冷却表"

    def test_unknown_engine_name_is_rejected_with_a_way_to_list_them(self, monkeypatch, capsys):
        code, out, asked = self._run(monkeypatch, capsys, ["goole"])
        assert code == 2 and asked == []
        assert "goole" in out and "--all" in out

    def test_all_flag_covers_every_registered_keyless_engine(self, monkeypatch, capsys):
        from dhole_mcp.search_metasearch import _TEXT_ENGINES

        _, _, asked = self._run(monkeypatch, capsys, ["--all"])
        assert len(asked) == len(_TEXT_ENGINES), \
            f"--all 应当问遍 {len(_TEXT_ENGINES)} 个免密引擎，实际 {len(asked)}"


class TestPublicReExports:
    """`dhole_mcp.<name>` 这份公开面在拆文件的每一刀都必须还成立。

    B6 把 server.py 一块块搬出去，而 `__init__` 的惰性再导出是按名字硬编码的：
    名字搬走了、导出的那行没跟着改，用户那边就是一个 ImportError —— 全量测试里
    没有任何一条会读到 `dhole_mcp.main`，所以这类断裂只能靠守卫钉住。
    """

    def test_every_declared_export_resolves(self):
        import dhole_mcp

        missing = []
        for name in dhole_mcp.__all__:
            try:
                getattr(dhole_mcp, name)
            except Exception as exc:  # noqa: BLE001 - 守卫要报出任何失败
                missing.append(f"{name}: {type(exc).__name__} {exc}")
        assert not missing, f"__all__ 里有名字导不出来：{missing}"

    def test_main_points_at_the_entry_that_was_moved(self):
        import dhole_mcp
        from dhole_mcp import ops

        assert dhole_mcp.main is ops.main, "再导出的必须是同一个函数对象"

    def test_an_unknown_name_is_still_a_plain_attribute_error(self):
        import dhole_mcp

        try:
            dhole_mcp.nope_not_a_dhole
        except AttributeError as exc:
            assert "nope_not_a_dhole" in str(exc)
        else:
            raise AssertionError("未知名字必须是 AttributeError，不是静默 None")
