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
# 垂直/非英文索引得用自己的查询词：拿英文技术词去问公众号搜索，抓到的是"没有结果的
# 页面"，而那种页面不能当契约基线（capture 模式会拒绝写盘，等于永远覆盖不了它）。
QUERY_BY_ENGINE = {"sogou_weixin": "SQLite WAL 模式"}
MAX_ITEMS = 4

pytestmark = pytest.mark.live


def _mode(request):
    return request.config.getoption("--engine-fixtures", default=None)


def _query_for(engine: str) -> str:
    return QUERY_BY_ENGINE.get(engine, QUERY)


def _trim_to_items(eng, html_text):
    """只留前 N 个结果容器 + 它们自身需要的子树 + **它们所在的祖先链**。

    elements_xpath 全是相对路径（以 . 开头），所以容器子树自带全部依赖；整体保存
    一份 SERP 会有 100KB+，而 tests/old_reddit_real.html 就是"没人引用的大 fixture
    烂在仓库里"的现成先例。

    祖先链必须留：items_xpath 里可以带祖先条件（sogou 的
    `//ul[contains(@class,"news-list")]//li[...]` 就是），只把 li 抠出来存盘，解析器
    在 fixture 上一条都匹配不到 —— 收 sogou fixture 时实测踩到（last_extract=(0,0)）。
    所以这里把祖先的开合标签按原样重建（只保留标签名+属性，不带兄弟节点）。

    返回的第二个值是**裁剪后**的容器数（= 写进 meta 的 item_nodes），不是原页面的
    容器数 —— 实测原页 bing 5~10 / yandex 14 / sogou 10 个容器，裁到 4 个。
    """
    import html as _html
    from lxml import html as lhtml
    tree = lhtml.fromstring(eng.pre_process_html(html_text))
    nodes = tree.xpath(eng.items_xpath)[:MAX_ITEMS]
    parts = [lhtml.tostring(n, encoding="unicode") for n in nodes]
    if not nodes:
        return "<html><body></body></html>", 0
    chain = [a for a in nodes[0].iterancestors() if a.tag not in ("html", "body")]
    def _open(a):
        attrs = "".join(f' {k}="{_html.escape(str(v), quote=True)}"' for k, v in a.attrib.items())
        return f"<{a.tag}{attrs}>"
    envelope = ("".join(_open(a) for a in reversed(chain))
                + "\n".join(parts)
                + "".join(f"</{a.tag}>" for a in chain))
    return "<html><body>" + envelope + "</body></html>", len(nodes)


def _scrub(text: str) -> str:
    """剥掉抓取痕迹：cookie / token / sig / 会话参数不该进仓库。

    分隔符既要认 `?a=`/`&a=`，也要认 HTML 转义后的 `&amp;a=` —— 搜狗的结果链接里
    带 `&amp;token=...` 的会话 token，只按裸 `&` 匹配会整片漏掉（那个 token 会过期，
    但它在仓库里活多久由提交决定，不由搜狗决定）。
    """
    import re
    return re.sub(
        r"((?:[?&]|&amp;)(?:q|Cookie|token|sig|key|sid|session|auth|_c)\w*=)[^&\"'\s<>]+",
        r"\1REDACTED", text)


def _fetch(name: str):
    """向一个引擎发一次真请求，返回 (trimmed_html, item_nodes, usable)。"""
    from dhole_mcp.search_metasearch import _TEXT_ENGINES

    eng = _TEXT_ENGINES[name](proxy=None, timeout=15, verify=True)
    query = _query_for(name)
    payload = eng.build_payload(query=query, region="us-en", safesearch="moderate",
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


@pytest.mark.parametrize("engine", ["bing", "duckduckgo", "brave", "yahoo", "yandex",
                                    "sogou_weixin"])
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
        from dhole_mcp.search_metasearch import _TEXT_ENGINES
        eng = _TEXT_ENGINES[engine](proxy=None, timeout=15, verify=True)
        in_fixture = sum(1 for r in (eng.extract_results(trimmed) or [])
                         if getattr(r, "href", None) and getattr(r, "title", None))
        meta.write_text(json.dumps({
            "engine": engine, "fixture": fixture.name,
            "captured_at": date.today().isoformat(),
            "captured_from_url": _source_of(engine), "query": _query_for(engine),
            "sha256": _sha(fixture),
            # item_nodes 是**裁剪后**的容器数（MAX_ITEMS 上限），不是在活页上看到的
            # 数量 —— 两个数混在一起会读出"usable/item_nodes = 2.5"这种假比值。
            "item_nodes": nodes, "usable_in_fixture": in_fixture,
            "usable_on_live_page": usable,
            "note": "真页面裁剪件：只留前若干结果容器，已剥 cookie/token/会话参数",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        pytest.skip(f"{engine}: fixture 已重新抓取（{nodes} 容器 / 活页 {usable} 可用）")

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
