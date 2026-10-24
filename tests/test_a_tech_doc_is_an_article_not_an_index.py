"""一篇技术文档是一篇文章，不是一个索引。

现场（2026-10-24，真实站点实测）：MDN 的 Fetch_API 页 `content_ok=true`、正文
4,965 字，却判 `page_type=list`，`next_action` 建议"去 web_crawl"。量出来的形状：

    <article> 不存在（只有 <main>）   同域内容链接 35   share 0.179
    longest_run 196（判据要求 ≤400 过、文章闸的 600 够不到）

技术文档的正文被代码块与定义列表切成短段，`longest_run` 这把尺对它结构性失真；
而真判别它的信号是**代码密度**——四个真页的读数（当日字节存 tests/page_fixtures）：

    MDN Fetch_API   <main>✔  main 内 <p>=20   <pre>+<code>=38
    HN 首页         <main>✔  <p>=0            code=0
    Python genindex <main>✔  <p>=3            code=0
    gov.cn 门户     <main>✔  <p>=12  h2+h3=23 code=0   ← 段落数和标题数都拦不住门户

所以信号组是三条缺一不可：`<main>` + main 内 ≥8 段 + 全文 ≥5 颗代码标记。
本文件钉住：MDN 判 article；三个反例不翻；合成件锁三条线各自的边界。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dhole_mcp.envelope import detect_page_type

FIXTURES = Path(__file__).parent / "page_fixtures"

MDN = {"html": "mdn_fetch_api.html", "url": "https://developer.mozilla.org/en-US/docs/Web/API/Fetch_API",
       "links": 35, "share": 0.179, "run": 196}
HN = {"html": "hn_home.html", "url": "https://news.ycombinator.com/",
      "links": 2, "share": 0.001, "run": 82}
GENINDEX = {"html": "python_genindex.html", "url": "https://docs.python.org/3/genindex.html",
            "links": 2, "share": 0.041, "run": 115}
QUOTES = {"html": "quotes_index.html", "url": "https://quotes.toscrape.com/",
          "links": 52, "share": 0.264, "run": 148}


def _page(spec: dict) -> str:
    path = FIXTURES / spec["html"]
    meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    raw = path.read_bytes()
    # sidecar 必须一直描述盘上这份字节：sha 对不上就是量具与靶子分了家。
    assert meta["sha256"] == hashlib.sha256(raw).hexdigest()[:16], spec["html"]
    assert meta["content_links"] == spec["links"]
    assert meta["label_share"] == pytest.approx(spec["share"], abs=0.001)
    assert meta["longest_nonlabel_run_chars"] == pytest.approx(spec["run"], abs=5)
    return raw.decode("utf-8", errors="replace")


# ─── 1 · 验收目标：MDN 判 article ──────────────────────────────────────

def test_mdn_reference_doc_is_an_article():
    assert detect_page_type(_page(MDN), MDN["url"], "text/html", 4965) == "article"


def test_the_verdict_survives_an_extraction_collapse():
    """判决不许跟着抽取走：抽取只剩 200 字时，同一份字节仍判 article。"""
    assert detect_page_type(_page(MDN), MDN["url"], "text/html", 200) == "article"


# ─── 2 · 反例锁：索引与门户不翻 ────────────────────────────────────────

@pytest.mark.parametrize("spec", [HN, GENINDEX], ids=["hn", "genindex"])
def test_index_shaped_pages_are_not_promoted_to_article(spec):
    """HN 与 Python genindex 的当日判决都不是 article，技术文档信号不许把它们抬上去。"""
    verdict = detect_page_type(_page(spec), spec["url"], "text/html", 0)
    assert verdict != "article", (
        f"{spec['html']} 被判成 article —— 索引页的 p/code 读数是 "
        f"{HN['html'] if spec is HN else GENINDEX['html']} 那一档，信号组放水了")
    assert verdict in ("list", "unknown"), verdict


def test_a_real_link_index_stays_a_list():
    """quotes 索引（share 0.264 / run 148）：没有 <main> 里的段落，照旧 list。"""
    assert detect_page_type(_page(QUOTES), QUOTES["url"], "text/html", 2179) == "list"


# ─── 3 · 合成件：三条线各自的边界 ─────────────────────────────────────

def _indexish(n_paras: int, n_codes: int, *, with_main: bool = True) -> str:
    """一个落在 list 闸里的页面骨架：30 条同域链接的导航 + n 段短文 + n 颗代码标记。"""
    nav = "".join(f'<a href="https://tech.test/p/{i}">条目{i}</a>' for i in range(30))
    paras = "".join(
        f"<p>第{i}段：<a href='https://tech.test/n/{i}'>参见{i}</a>，一句短话。</p>"
        for i in range(n_paras))
    codes = "".join("<code>await x()</code>" for _ in range(n_codes))
    inner = f"{nav}{paras}{codes}"
    body = f"<main>{inner}</main>" if with_main else f"<div role='main'>{inner}</div>"
    return f"<html><body>{body}</body></html>"


def test_a_portal_shape_with_paragraphs_but_no_code_stays_a_list():
    """gov.cn 的教训：main + 12 段 + h2/h3 一大把，而 code=0 —— 照旧 list。"""
    assert detect_page_type(_indexish(12, 0), "https://tech.test/a", "text/html", 0) == "list"


def test_eight_paras_and_five_code_marks_make_an_article():
    assert detect_page_type(_indexish(8, 5), "https://tech.test/a", "text/html", 0) == "article"


def test_seven_paras_is_not_enough():
    assert detect_page_type(_indexish(7, 5), "https://tech.test/a", "text/html", 0) == "list"


def test_four_code_marks_is_not_enough():
    assert detect_page_type(_indexish(8, 4), "https://tech.test/a", "text/html", 0) == "list"


def test_without_a_main_element_the_gate_does_not_open():
    """role=main 的 div 不算：genindex 就长这样，信号组必须对它关着。"""
    assert detect_page_type(
        _indexish(8, 5, with_main=False), "https://tech.test/a", "text/html", 0) == "list"
