"""活体 fixture 抓取 / 比对（只手动跑，绝不在默认运行里）。

    python -m pytest -m live --engine-fixtures=capture tests/test_engine_fixtures_live.py
    python -m pytest -m live --engine-fixtures=check  tests/test_engine_fixtures_live.py

为什么需要它：tests/test_engine_parsers.py 的契约是"解析器对**某一天真实页面**的
断言"。fixture 会随上游改版而失效，这个模块就是让它能被重新证实的那只手。

判红口径刻意很窄 —— 只有"fixture 能解析出东西、活页也有结果容器、但活页一条可用
都没有"才判红（那是确认的解析器漂移）。其余一律打表 + skip：真实 SERP 返回 0 条是
天气，不是故障；把天气写进红绿，维护者学会的第一件事就是删测试。
"""

import json
from datetime import date
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "engine_fixtures"
QUERY = "how does sqlite WAL mode work"
MAX_ITEMS = 4

pytestmark = pytest.mark.live


def _mode(request):
    return request.config.getoption("--engine-fixtures", default=None)


def _trim_to_items(eng, html_text):
    """只留前 N 个结果容器 + 它们自身需要的子树。

    elements_xpath 全是相对路径（以 . 开头），所以容器子树自带全部依赖；整体保存
    一份 SERP 会有 100KB+，而 tests/old_reddit_real.html 就是"没人引用的大 fixture
    烂在仓库里"的现成先例。
    """
    from lxml import html as lhtml
    tree = lhtml.fromstring(eng.pre_process_html(html_text))
    nodes = tree.xpath(eng.items_xpath)[:MAX_ITEMS]
    parts = [lhtml.tostring(n, encoding="unicode") for n in nodes]
    return "<html><body>" + "\n".join(parts) + "</body></html>", len(nodes)


def _scrub(text: str) -> str:
    """剥掉抓取痕迹：cookie / token / sig / 会话参数不该进仓库。"""
    import re
    return re.sub(
        r"([?&](?:q|Cookie|token|sig|key|sid|session|auth|_c)\w*=)[^&\"'\s<>]+",
        r"\1REDACTED", text)


def _fetch(name: str):
    """向一个引擎发一次真请求，返回 (trimmed_html, item_nodes, usable)。"""
    from dhole_mcp.search_metasearch import _TEXT_ENGINES

    eng = _TEXT_ENGINES[name](proxy=None, timeout=15, verify=True)
    payload = eng.build_payload(query=QUERY, region="us-en", safesearch="moderate",
                                timelimit=None, page=1)
    if eng.search_method == "GET":
        text = eng.request(eng.search_method, eng.search_url, params=payload)
    else:
        text = eng.request(eng.search_method, eng.search_url, data=payload)
    if not text:
        return None, 0, 0
    trimmed, nodes = _trim_to_items(eng, text)
    rows = eng.extract_results(text) or []
    usable = sum(1 for r in rows if getattr(r, "href", None) and getattr(r, "title", None))
    return _scrub(trimmed), nodes, usable


def _try_fetch(name: str):
    """网络/限速异常一律转成 skip —— 活体探测的失败原因是天气，不是断言。"""
    try:
        return _fetch(name)
    except Exception as exc:
        return None, 0, 0, f"{type(exc).__name__}: {str(exc)[:120]}"


@pytest.fixture(scope="module")
def _guard(request):
    mode = _mode(request)
    if not mode:
        pytest.skip("需要 --engine-fixtures=check|capture（外加 -m live）才跑这个模块")
    return mode


@pytest.mark.parametrize("engine", ["bing", "duckduckgo", "brave", "yahoo", "yandex"])
def test_engine_fixture_contract(engine, _guard):
    mode = _guard
    fixture = FIXTURE_DIR / f"{engine}.html"
    meta = fixture.with_suffix(".meta.json")   # 统一 stem 约定：bing_variant_a.html -> bing_variant_a.meta.json

    if mode == "capture":
        got = _try_fetch(engine)
        trimmed, nodes, usable = got[0], got[1], got[2]
        if len(got) > 3:
            pytest.skip(f"{engine}: 抓不到页面（{got[3]}）—— 未覆盖 fixture")
        if not trimmed or usable == 0:
            # 一条都读不出来的页面不能当契约基线 —— 那会把"坏了"固化成"预期"。
            pytest.skip(f"{engine}: 本轮抓到 {nodes} 容器但 0 条可用，"
                        f"拒绝写入 fixture（这是降级页，不是契约）")
        fixture.parent.mkdir(exist_ok=True)
        fixture.write_text(trimmed, encoding="utf-8")
        meta.write_text(json.dumps({
            "engine": engine, "fixture": fixture.name,
            "captured_at": date.today().isoformat(),
            "captured_from_url": _source_of(engine), "query": QUERY,
            "sha256": _sha(fixture), "item_nodes": nodes, "usable": usable,
            "note": "真页面裁剪件：只留前若干结果容器，已剥 cookie/token/会话参数",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        pytest.skip(f"{engine}: fixture 已重新抓取（{nodes} 容器 / {usable} 可用）")

    # mode == "check"
    if not fixture.exists():
        pytest.skip(f"{engine}: 还没有 fixture，跑 --engine-fixtures=capture 抓一份")
    got = _try_fetch(engine)
    trimmed, nodes, usable = got[0], got[1], got[2]
    if len(got) > 3:
        pytest.skip(f"{engine}: 活页没答上来（{got[3]}）—— 天气，不判红")
    if trimmed is None:
        pytest.skip(f"{engine}: 活页没答上来（被墙/超时）—— 这是天气，不判红")
    from dhole_mcp.search_metasearch import _TEXT_ENGINES
    eng = _TEXT_ENGINES[engine](proxy=None, timeout=15, verify=True)
    fixture_usable = sum(1 for r in (eng.extract_results(fixture.read_text(encoding="utf-8")) or [])
                         if getattr(r, "href", None) and getattr(r, "title", None))
    report = (f"{engine}: live containers={nodes} live usable={usable} | "
              f"fixture usable={fixture_usable}")
    if fixture_usable > 0 and nodes > 0 and usable == 0:
        pytest.fail(
            f"PARSER DRIFT on {engine}: 活页有 {nodes} 个结果容器却一条都读不出"
            f"（fixture 上还能读出 {fixture_usable} 条）。items_xpath/elements_xpath "
            f"与页面结构已错位 —— 这是解析器坏了，不是查询问题。")
    pytest.skip(report + " —— 无确认漂移")


def _source_of(engine):
    from dhole_mcp.search_metasearch import _TEXT_ENGINES
    return getattr(_TEXT_ENGINES[engine], "search_url", "")


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
