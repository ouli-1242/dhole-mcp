"""答案可能早就以 JSON 的形式躺在页面里（提升计划 2.2 · 数据岛）。

现代 SPA 常把完整正文塞在 `script[type='application/json']` / `__NEXT_DATA__` 里
——数据已经在 HTML 里，只是不在可见 DOM 里。dhole 从前只有两条路：升级浏览器
（3–30s）或放弃。现在抽取稀疏时把这些块拼起来当候选，但**过与正文同一套质量闸**：
候选得是一段话、明显多于现有答案；过不了闸就当这页没有岛，空答案照旧让壳闸说话、
阶梯照旧升级。

本文件钉住六件事：

  * 收集：application/json 岛与 __NEXT_DATA__ 里的长字符串块，按文档序、去重；
  * 配置/埋点类的键整棵子树跳过（config / env / buildId / gtag …）；
  * 接管：稀疏抽取 + 岛是一段话 → 岛成为答案，回执说明来路；
  * 反例锁一：岛里只有埋点配置 → 一个字节不变；
  * 反例锁二：抽取不稀疏（≥2,000 字）→ 岛不许掺和；
  * 反例锁三：岛拼起来不是一段话（一串无句末标点的标签）→ 不许接管。
"""

from __future__ import annotations

import json

from dhole_mcp.context import _ARG_NOTES
from dhole_mcp.data_island import MAX_BLOCKS, MIN_BLOCK_CHARS, extract_data_islands
from dhole_mcp.envelope import answer_is_prose
from dhole_mcp.fetcher import Response
from dhole_mcp.server import _translate_response_body

PARA = ("中微子是构成物质世界的基本粒子之一，不带电、质量极轻，几乎不与物质作用，"
        "被称为幽灵粒子。在黑洞喷流与伽马射线暴的现场，只有它能笔直冲出天体核心，"
        "跨越数十亿光年抵达地球，把核心深处的事直接写进人类的探测器里。")


def _para(i: int) -> str:
    """第 i 段：同源而互不相同——收集器按块去重，五块一样会被折成一块。"""
    return f"{PARA * 2}（第{i}段，接续前文。）"


BLOCK = _para(0)          # ≥ MIN_BLOCK_CHARS 的一块正文
ISLAND_PARAS = [_para(i) for i in range(5)]


def _island_script(payload, *, as_next_data: bool = True) -> str:
    attrs = 'id="__NEXT_DATA__" type="application/json"' if as_next_data \
        else 'type="application/json"'
    return (f'<html><body><div id="root">加载中</div>'
            f'<script {attrs}>{json.dumps(payload, ensure_ascii=False)}</script>'
            f"</body></html>")


def _page(html: str) -> Response:
    return Response(url="https://spa.test/post", body=html.encode("utf-8"),
                    status=200, headers={"content-type": "text/html; charset=utf-8"})


# ─── 1 · 收集器本身 ────────────────────────────────────────────────────

def test_long_strings_are_collected_from_nested_json():
    payload = {"props": {"pageProps": {"article": {"paras": ISLAND_PARAS}}}}
    assert extract_data_islands(_island_script(payload)) == ISLAND_PARAS


def test_config_and_tracking_subtrees_are_skipped():
    payload = {
        "buildId": "x1", "theme": {"description": BLOCK},
        "config": {"content": BLOCK},
        "gtag": {"js": [{"data": BLOCK}]},
        "page": {"paras": [BLOCK]},
    }
    blocks = extract_data_islands(_island_script(payload))
    assert blocks == [BLOCK], "配置/埋点子树里的长文案不许进来"


def test_short_strings_and_broken_json_are_not_content():
    payload = {"title": "短标题", "note": "n" * (MIN_BLOCK_CHARS - 1)}
    assert extract_data_islands(_island_script(payload)) == []
    html = '<script type="application/json">{not json}</script>'
    assert extract_data_islands(html) == []


def test_identical_blocks_are_deduped_and_capped():
    payload = {"a": [BLOCK] * 50}
    assert extract_data_islands(_island_script(payload)) == [BLOCK]
    many = {f"k{i}": BLOCK + f"（第{i}段）" for i in range(MAX_BLOCKS + 200)}
    assert len(extract_data_islands(_island_script(many))) == MAX_BLOCKS


def test_ld_json_is_not_an_island_candidate():
    html = f'<script type="application/ld+json">{{"x":"{BLOCK}"}}</script>'
    assert extract_data_islands(html) == []


# ─── 2 · 接管与三把反例锁 ──────────────────────────────────────────────

def _translate(monkeypatch, html: str, current: list[str]):
    """真走 `_translate_response_body`，抽取层全部换成受控的假货。"""
    monkeypatch.setattr("dhole_mcp.trafilatura_extractor.extract_with_trafilatura",
                        lambda *a, **k: current)
    monkeypatch.setattr("dhole_mcp.extractor.recover_a_lost_document",
                        lambda page, content, *a, **k: content)
    monkeypatch.setattr("dhole_mcp.extractor._collapse_boilerplate",
                        lambda content, *a, **k: content)
    monkeypatch.setattr("dhole_mcp.extractor.extract_content",
                        lambda page, **k: current)

    notes: list[str] = []
    token = _ARG_NOTES.set(notes)
    try:
        return _translate_response_body(_page(html), "markdown", None, "http", 1.0), notes
    finally:
        _ARG_NOTES.reset(token)


def test_a_spa_shell_gets_its_prose_from_the_island(monkeypatch):
    payload = {"props": {"pageProps": {"article": {"paras": ISLAND_PARAS}}}}
    result, notes = _translate(monkeypatch, _island_script(payload), ["加载中"])
    assert result.content == ["\n\n".join(ISLAND_PARAS)]
    assert answer_is_prose(result.content[0])
    assert result.metadata.get("recovered_as") == "data_island"
    assert any("data island" in n for n in notes), notes


def test_an_analytics_only_island_changes_nothing(monkeypatch):
    payload = {"buildId": "9f2", "gtag": {"id": "G-XX", "events": [{"label": BLOCK}]},
               "theme": {"colors": {"primary": "#333"}}}
    result, notes = _translate(monkeypatch, _island_script(payload), ["加载中"])
    assert result.content == ["加载中"]
    assert "recovered_as" not in (result.metadata or {})
    assert not any("data island" in n for n in notes), notes


def test_a_page_without_islands_is_untouched(monkeypatch):
    result, _ = _translate(monkeypatch, "<html><body><p>挂载中</p></body></html>",
                           ["挂载中"])
    assert result.content == ["挂载中"]


def test_a_rich_extraction_is_not_traded_for_the_island(monkeypatch):
    payload = {"props": {"pageProps": {"article": {"paras": ISLAND_PARAS}}}}
    rich = [PARA * 12]        # ≥ _ISLAND_SPARSE_CHARS：抽取已经读到了东西
    result, notes = _translate(monkeypatch, _island_script(payload), rich)
    assert result.content == rich, "不稀疏时岛不许掺和"
    assert not any("data island" in n for n in notes), notes


def test_an_island_that_is_not_prose_is_refused(monkeypatch):
    labels = "，".join(f"推荐阅读第{i:03d}条这里是一条很长的推荐标题没有句末标点"
                      for i in range(12))
    payload = {"props": {"pageProps": {"sidebar": [labels * 3]}}}
    result, notes = _translate(monkeypatch, _island_script(payload), ["加载中"])
    assert result.content == ["加载中"], "一串无句末标点的标签不许接管"
    assert not any("data island" in n for n in notes), notes
