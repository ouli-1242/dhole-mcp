"""解析器契约测试：把"引擎答了、我们解析出 0 条"钉成一个会红的断言。

为什么需要（实测，不是假想）：2026-09-21 抓 bing 时发现它同时在跑两种标题链接版面，
其中一种 `<a class="tilk">…<h2>文本</h2></a>` 让 href xpath `.//h2/a/@href` 一条都
匹配不到 —— 5 个结果容器全部变成不可用，整轮 bing 结果为空。而在响应里这既不进
engines_used 也不进 engine_blocked，是真正的"结果变少但不报错"。

设计要点：
- 期望条数由**独立 oracle**（bs4 + html.parser，与被测的 lxml+xpath 不同 parser、
  不同查询语言）算出，而不是"我的解析器同意我的 fixture" —— 后者只会把 bug 固化。
- fixture 是真页面裁剪件（见 .meta.json），不是手写玩具。
- 绝不写"fixture 少于 N 天就失败"这类年龄断言：那会让套件在某个随机星期二无代码
  变更地变红，而维护者学会的第一件事就是删测试。
- 活体比对在 tests/test_engine_fixtures_live.py（需 -m live + --engine-fixtures）。
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
    # sogou_weixin 的结果 href **就是**搜狗的 /link?url=... 跳转包装（那个 token 要在
    # 浏览器里换真链，离线解不开），所以这里锁的是"不许留下相对路径"——绝对化是它在
    # extract_results 里唯一的后处理，漏了它浏览器打不开结果。
    Contract("sogou_weixin", "sogou_weixin.html",
             "ul[class*='news-list'] li:has(div.txt-box)",
             must_not_start=("/link?",)),
    # baidu 的结果 href 是 baidu.com/link?url=<token> 包装，真链在容器的 mu 属性里。
    # 这条锁的就是"交出去的 href 不许是自家跳转包装"（浏览器打开会过期/带 token）。
    Contract("baidu", "baidu_variant_a.html", "div.result.c-container",
             must_not_start=("http://www.baidu.com/link", "https://www.baidu.com/link")),
    # B 版面（div.c-result）没有 mu，真链埋在 data-log 的 JSON 里 —— 与 bing 那次
    # "两套标题链接版面"同类，所以同样单独出契约：A 版绿不代表 B 版能读。
    Contract("baidu", "baidu_variant_b.html", "div.c-result",
             must_not_start=("http://www.baidu.com/link", "https://www.baidu.com/link",
                             "http://m.baidu.com", "https://m.baidu.com")),
    # 百科是**单条目**引擎：容器是首段摘要（lemmaSummary），标题取页面 h1、URL 取
    # 页面自己的 canonical —— 两者都不在摘要容器里，这条同时把那个取法钉住。
    Contract("baidu_baike", "baidu_baike.html", "div[class*='lemmaSummary']"),
    # 国内 opt-in 两家。真链都在 data-* 属性里（so.com/link?m= 与 sogou /link?url= 都是
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
    """fixture 自己也会烂：tests/old_reddit_real.html 就是已经烂过一次的那一个。"""

    def test_every_fixture_is_content_addressed(self):
        """记录的 sha256 按 **git 里的字节** 算，行尾以仓库为准（`* text=auto eol=lf`）。

        所以它只在工作树与仓库字节一致时成立 —— 一份 CRLF 残留的工作树会红，
        而那并不代表"fixture 被改过"。这条 2026-09-22 修过一次同类烂法：记录值
        按 CRLF 算、blob 是 LF，于是任何全新克隆一 checkout 就失败。
        """
        missing = [p.name for p in FIXTURE_DIR.glob("*.html")
                   if not _meta_of(p.name).exists()]
        assert not missing, f"这些 fixture 没有 .meta.json: {missing}"
        for p in FIXTURE_DIR.glob("*.html"):
            meta = _meta_of(p.name)
            if not meta.exists():
                continue
            want = json.loads(meta.read_text(encoding="utf-8")).get("sha256")
            got = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
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


class TestBaiduChallengePagesAreBlocks:
    """实测形态：被百度限流时回的是 **200 + 几 KB 的校验页**（搜索页 1.4KB、条目页
    4.4KB「安全校验中…」），静态正文里没有结果也没有条目。它必须记成 blocked（进
    熔断冷却），不能以 0 条结果的形态混进 engine_empty —— 那会被读成"没有结果"。
    """

    SHELL = ("<html><head><title>百度百科</title></head><body>"
             "<div>百度百科</div><div>安全校验中…</div></body></html>")
    BAIDU_SHELL = ("<html><head><title>百度安全验证</title></head><body>"
                   "<p>请完成安全验证后继续访问</p></body></html>")

    def test_challenge_page_raises_blocked(self, monkeypatch):
        from dhole_mcp.search_metasearch import MetaBlockedException

        eng = _engine("baidu_baike")
        monkeypatch.setattr(eng, "request", lambda *a, **k: self.SHELL)
        with pytest.raises(MetaBlockedException) as exc:
            eng.search("人工智能")
        assert "校验" in str(exc.value) and "baidu_baike" in str(exc.value)

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

    def test_real_entry_page_yields_the_entry(self, monkeypatch):
        """真页面裁剪件上的正路：1 条结果，标题=条目名，URL=页面 canonical。"""
        eng = _engine("baidu_baike")
        monkeypatch.setattr(eng, "request", lambda *a, **k:
                            (FIXTURE_DIR / "baidu_baike.html").read_text(encoding="utf-8"))
        rows = eng.search("人工智能")
        assert len(rows) == 1
        assert rows[0].title == "人工智能"
        assert rows[0].href.startswith("https://baike.baidu.com/item/")
        assert rows[0].body

    def test_second_page_is_empty_not_a_refetch(self):
        """单条目引擎没有第二页：page>1 直接空，不打网络。"""
        assert _engine("baidu_baike").search("人工智能", page=2) == []


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


class TestMwmblJsonApi:
    """MWMBL 是 JSON API（没有 HTML 页面），所以不吃 CONTRACTS 那套 html fixture 契约；
    但它有一个真踩过的坑要用真形状钉住：title 与 extract **都是** ``[{value,is_bold}]``
    分词数组（高亮词把标题/摘要切成多段）。只 join 字符串元素的话摘要会静默全空，
    而 usable 只看 title+href —— 整条结果照样"可用"，坏得无声无息。
    """

    PAYLOAD = [
        {"url": "https://github.com/python/asyncio/stargazers", "source": "mwmbl",
         "title": [{"value": "Stargazers · ", "is_bold": False},
                   {"value": "python", "is_bold": True},
                   {"value": "/", "is_bold": False},
                   {"value": "asyncio", "is_bold": True},
                   {"value": " · GitHub", "is_bold": False}],
         "extract": [{"value": " Saved searches Use saved searches ", "is_bold": False},
                     {"value": "asyncio", "is_bold": True},
                     {"value": " to filter results", "is_bold": False}]},
        {"url": "http://leanpub.com/asyncio", "source": "mwmbl",
         "title": [{"value": "asyncio", "is_bold": True},
                   {"value": " from ground up", "is_bold": False}],
         "extract": []},
    ]

    def test_segments_are_joined_not_dropped(self):
        eng = _engine("mwmbl")
        rows = eng.extract_results(json.dumps(self.PAYLOAD))
        assert [r.href for r in rows] == [i["url"] for i in self.PAYLOAD]
        assert rows[0].title == "Stargazers · python/asyncio · GitHub"
        assert rows[0].body == "Saved searches Use saved searches asyncio to filter results"
        assert eng.last_extract == (2, 2), (
            "JSON 引擎也要自己记产出 —— 否则'解析出 0 条'在产出面板里永远是没观测")

    def test_plain_string_fields_still_work(self):
        """保险带：API 若把 title/extract 变回字符串，别把整条结果读成空。"""
        eng = _engine("mwmbl")
        rows = eng.extract_results(json.dumps(
            [{"url": "https://a.test/x", "title": "Plain Title", "extract": "plain body"}]))
        assert (rows[0].title, rows[0].body) == ("Plain Title", "plain body")

    def test_second_page_is_empty_not_a_refetch(self, monkeypatch):
        eng = _engine("mwmbl")

        def boom(*a, **k):
            raise AssertionError("API 没有翻页：page>1 不该打网络")

        monkeypatch.setattr(eng, "request", boom)
        assert eng.search("python", page=2) == []


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
