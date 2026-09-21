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
        own = {"bing": "bing.com", "yandex": "yandex."}.get(c.engine)
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
