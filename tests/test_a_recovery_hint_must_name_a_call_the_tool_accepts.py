"""`neural_focus_model_missing` 的出路必须指向一发**这本工具真的会接受**的调用。

现场（2026-10-14 真实使用模拟 · P02-05 邱洛（券商行业研究员 L3）· S6 冷启动（临时 HOME 的独立进程 9215）· R02-017 · P1）：

    web_read(url='https://example.com/', focus_engine='neural', focus='reliability')
      → error=neural_focus_model_missing、No request was made
      → next_action=「The reranker model is not downloaded yet. It downloads automatically
        on the first **neural web_search** (one-time, resumable). For this call only, pass
        focus_engine='bm25'…」

照那句话做（这是它给出的**唯一一条前进路**）：

    web_search(query='…', focus_engine='neural')
      → invalid_request: Unsupported argument(s) for web_search: ['focus_engine']

一句话把调用方支到他刚撞过的拒绝上。量的时候才发现含糊在一个词上：
「a neural web_search」听起来就是"给 web_search 点名 neural 那颗引擎"，而 web_search 的
**参数表里没有 `focus_engine`**（`_TOP_LEVEL_ARGS['web_search']` 当场查得到）。真相是
web_search 自己默认就用这颗模型排序（`mode` 默认 `auto`，而 search.py 里
`mode in ("neural","auto","find_similar")` 那一行会起 `ensure_reranker()`）—— 也就是说
**一发什么参数都不点的普通 web_search 就会把模型取回来**，根本不需要造出一发"neural
web_search"。所以那句话不只是指向一个不存在的形状，它还**多说**了一个不需要的动作。

另一件同族的事：这句话在 `server.py` 里有**两个抄本**（web_read 那一处写 "For this call
only"、web_crawl 那一处写 "For this crawl only"），手册里讲这把拒绝的**五行**（SKILL.md /
references/tools.md / references/examples.md / references/configuration.md /
references/troubleshooting.md）又各写了一遍。同一件事七个作者，
正是组 C 立的那条纪律要治的：**建议的作者必须与错误的作者是同一个人**。

契约（本文件钉住）：
  * 出路点名 `mode`（web_search 真有这颗）与 `focus_engine='bm25'`（本行工具真有这颗），
    并且**明确说出 web_search 没有 `focus_engine`** —— 调用方手里刚点过的名字，要当场拦住。
    旧话暗示的那一发不可达这件事，由 CLI 实际撞的那道闸（`_reject_unknown_args`）当场作证，
    不是由 Python 方法签名作证（签名在闸门之前就 TypeError，量到的不是产品的话）。
  * 一般守卫：那句话里凡是写成 `xxx=` 的名字，都必须真的存在于它被归属的那颗工具的参数表里。
    这一条不是为 R02-017 写的特例，它量的是整族的形状（"建议指向一门不存在的门"）。
  * 一句出路**只有一个作者**：`server.py` 里那两处抄本合成一位（主语随工具变），
    手册讲这把拒绝的五行必须逐行与它同一个说法。
  * 「No request was made」这一半不许丢：被拒绝的是**这一发**，模型下载是另一件事。
  * 拒绝的判决位一位不动：`error` 仍以 `neural_focus_model_missing` 开头、`status=0`、
    `content_ok=false` —— 本刀修的是出路，不是判决。
  * **不许**为了让这句话成真而改变 web_search 的默认行为（那颗 279MB 的意外下载是
    R02-020 的另一问，本刀不顺手改调度，只在 decisions.md 记下这句话依赖的是今天的默认值）。
"""

from __future__ import annotations

import inspect
import pathlib
import re

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer, _TOP_LEVEL_ARGS

SENTENCE_RE = re.compile(r"`?([a-z_]+)`?=")
# 单词边界是必要的：旧话里 "The reranker **model** is not downloaded" 数得出 "mode"
# 这四个字母，却一个参数都没点。`mode` 只有在不成词时才算被点名。
MODE_RE = re.compile(r"\bmode\b")


def _tool_accepts(tool: str, arg: str) -> bool:
    return arg in _TOP_LEVEL_ARGS[tool]


def _names_mode(text: str) -> bool:
    return MODE_RE.search(text) is not None


# ─── 1 · 出路点名的每一颗参数都必须真的存在 ───


class TestTheWayOutNamesReachableDoors:

    def test_the_remedy_exists_and_has_one_author(self):
        remedy = server_mod._neural_focus_remedy("call")
        assert "neural" not in remedy.lower() or "web_search has no" in remedy, remedy
        # 两处抄本并一位：主语随工具变，句子只有一个作者。
        assert server_mod._neural_focus_remedy("crawl") != remedy
        src = inspect.getsource(server_mod)
        assert src.count("The reranker model is not downloaded yet") == 1, (
            "出路的那句话在 server.py 里只能有一位作者（从前是两处抄本）")

    def test_every_argument_named_in_the_remedy_is_reachable(self):
        """一般守卫：`xxx=` 写进建议里，就必须是那颗工具收得到的名字。

        归属规则与句子同源：提到 web_search 的那半句里的名字归 web_search，其余归本行工具
        （web_read / web_crawl）。这里用整句扫，凡是扫到的名字必须在**某一侧**成立，且
        `mode` 必须在 web_search 侧、`focus_engine` 必须在本行工具侧。
        """
        for subject, own in (("call", "web_read"), ("crawl", "web_crawl")):
            remedy = server_mod._neural_focus_remedy(subject)
            names = set(SENTENCE_RE.findall(remedy))
            assert names, f"句子里一个参数名都没有：{remedy}"
            for name in names:
                assert _tool_accepts("web_search", name) or _tool_accepts(own, name), (
                    f"{name}= 在 {own} 与 web_search 两侧都不存在：{remedy}")
            assert _tool_accepts("web_search", "mode"), "mode 必须是 web_search 收的那颗"

    def test_the_remedy_says_web_search_has_no_focus_engine(self):
        remedy = server_mod._neural_focus_remedy("call")
        assert "focus_engine" in remedy and "has no" in remedy, remedy
        assert not _tool_accepts("web_search", "focus_engine"), (
            "前提：web_search 确实不收这颗 —— 旧那句话正是把它当成收的")

    def test_the_remedy_still_offers_the_no_model_way_out(self):
        remedy = server_mod._neural_focus_remedy("call")
        assert "bm25" in remedy and _tool_accepts("web_read", "focus_engine"), remedy
        assert "no request was made" in remedy.lower(), remedy


# ─── 2 · 判决位一位不动（本刀只修出路） ───


@pytest.mark.asyncio
async def test_the_refusal_still_refuses_without_fetching(monkeypatch):
    import dhole_mcp.reranker as reranker_mod

    monkeypatch.setattr(reranker_mod, "get_reranker", lambda *a, **k: None)
    # R02-F16 之后这道门按成因分两支，本文件钉的是「盘上没有模型」那一支：
    # 那一支的出路才是一颗普通 web_search（F11 的话）。
    monkeypatch.setattr(reranker_mod, "model_present", lambda *a, **k: False)
    monkeypatch.setattr(reranker_mod, "ensure_reranker",
                        _async_none)
    r = await MasterFetchServer().web_read(
        url="https://example.com/", focus="reliability", focus_engine="neural")
    assert r.status == 0 and r.content_ok is not True
    assert (r.error or "").startswith("invalid_request: neural_focus_model_missing"), r.error
    assert _names_mode(r.next_action or ""), (
        f"出路仍然没点名那颗真的存在的参数：{r.next_action!r}")
    assert "no request was made" in (r.next_action or "").lower(), r.next_action


def test_the_shape_the_old_hint_pointed_at_is_refused():
    """旧那句话暗示的调用形状，当场证明它不可达（这就是 P1 的全部证据）。

    走的是 CLI 那一发实际撞上的闸门（`_dispatch` 第一件事就是 `_reject_unknown_args`），
    不是 Python 方法签名 —— 签名会在参数到达闸门之前就 TypeError，量到的不是产品的话。
    """
    from dhole_mcp.params import ArgumentRefusal

    with pytest.raises(ArgumentRefusal) as exc:
        server_mod._reject_unknown_args("web_search",
                                        {"query": "跨境仓配 合规",
                                         "focus_engine": "neural"})
    text = f"{exc.value} {getattr(exc.value, 'next_action', '') or ''}"
    assert "focus_engine" in text and "pported" in text, (
        f"这一发竟然被接受了？：{text[:200]}")


# ─── 3 · web_crawl 那一侧同一位作者 ───


@pytest.mark.asyncio
async def test_the_crawl_gate_shares_the_author(monkeypatch):
    import dhole_mcp.reranker as reranker_mod

    monkeypatch.setattr(reranker_mod, "get_reranker", lambda *a, **k: None)
    # R02-F16 之后这道门按成因分两支，本文件钉的是「盘上没有模型」那一支：
    # 那一支的出路才是一颗普通 web_search（F11 的话）。
    monkeypatch.setattr(reranker_mod, "model_present", lambda *a, **k: False)
    monkeypatch.setattr(reranker_mod, "ensure_reranker", _async_none)
    r = await MasterFetchServer().web_crawl(
        url="https://example.com/", focus="reliability", focus_engine="neural")
    assert "neural_focus_model_missing" in (r.error or ""), r.error
    assert _names_mode(r.next_action or ""), r.next_action
    assert "crawl" in (r.next_action or ""), (
        f"爬取那一侧拿到的还是'这一发调用'的话：{r.next_action!r}")


# ─── 4 · 手册与实施同一个说法 ───


def test_the_manual_stops_pointing_at_a_door_that_does_not_open():
    """每一处讲这把拒绝的手册行文，都必须与实施同一位作者。

    手册里讲这件事的有五行（SKILL.md / tools.md / examples.md / configuration.md /
    troubleshooting.md），旧版本里五行都把调用方支向「a neural `web_search`」（
    configuration.md 讲模型那一行是第六处）。守卫按行扫**整本手册**，不按文件扫 ——
    一个文件改好了、另一个文件漏着，正是这种改法会留下的形状。
    """
    root = (pathlib.Path(inspect.getsourcefile(server_mod)).resolve().parent
            / "skills" / "dhole-web")
    all_lines = []
    for path in sorted(root.rglob("*.md")):
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            all_lines.append((f"{path.name}:{no}", line))
    for where, line in all_lines:
        assert "neural web_search" not in line.replace("`", ""), (
            f"{where} 还在把调用方支向一发不存在的形状：{line[:200]}")
    lines = [(w, body) for w, body in all_lines if "neural_focus_model_missing" in body]
    assert lines, "手册里找不到讲这把拒绝的那一行了 —— 守卫的前提先变了"
    for where, line in lines:
        assert "bm25" in line.lower(), f"{where} 把不需要模型的那条出路写丢了：{line[:200]}"
        if "web_search" in line:
            # 提到"让模型回来"那一侧，就必须当场拦住那颗不存在的名字。
            assert "has no `focus_engine`" in line, (
                f"{where} 说了去用 web_search，却没说它不收 `focus_engine`：{line[:260]}")
    # 手册要认得那颗真的参数名。
    joined = "\n".join(line for _, line in lines)
    assert _names_mode(joined), "手册没说清 web_search 是用 mode 排序的那颗工具"


async def _async_none(*a, **k):
    return None
