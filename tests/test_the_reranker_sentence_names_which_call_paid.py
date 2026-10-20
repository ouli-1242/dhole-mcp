r"""重排器那句话说得出「这一发付过钱」与「这一发只是引用别人的账」的分别是哪一半。

现场（P02-05 邱洛 S6 冷启动 · R02-020（题面已于 2026-10-15 复验后校正，见台账）·
存档原文 `sessions/P02-05-s9-cold-web-search.json` / `P02-05-s10-cold-search-2nd.json`）：

    第一发（冷 HOME，模型不在盘上）wall 9.6s · duration_ms=8043 · fetch_hint:
      「… | neural rerank is NOT active despite its deps being installed - using consensus +
        engine-position order. reranker model download failed (offline?)」
    第二发（同进程，`ensure_reranker` 因 `_reranker_tried` 不再重试）wall 2.57s ·
      duration_ms=1648 · fetch_hint: **逐字同一句**

先说什么没坏：那句话**在**。原题面「失败后回执里一个字没有」把「顶层键没有 model 那一颗」
读成了「没提」，复验后已改。剩下的缺口是句子的**主语**：它讲的是「这台机器上发生过一次失败的
下载」，于是同一句话同时是一次 8.0s 的真付款和一次 1.65s 的纯引用 —— 而 `reranker` 自己
知道是哪种（`_reranker_tried` 与这次尝试的耗时都在它手上，`server.py:8685` 那段注释写着
「don't retry this process」）。用户据此能做的判断完全不同：前者该等/该换网/该离线放模型，
后者再发一百遍也不会变。

契约（本文件钉住）：
  * 失败原因之后要接**这一发**的位置：这一发做了那次尝试 ⇒ 说出它等了多久 + 本进程不再重试；
    这一发没做 ⇒ 说出那次尝试是本进程早前付的、这一发没有重试它；
  * 那颗毫秒数只有一个作者：`reranker.last_attempt_ms()`（句子不许自己掐表，
    测试也不许自己掐 —— 断言的是「句子里的数 == 那位手里的数」）；
  * 「一进程一次」这句承诺必须与实现同源：计数器只在 `_load_reranker` 里加，
    `search` 这边只在任务创建前 / 等待后各读一次做比较；
  * **一条不退化锁**：`mode='auto'` 在 lean 安装（`neural rerank needs dhole-mcp[all]`）上
    照旧**沉默**（那是预期形状，每封都喊就是噪声）；`mode='neural'` 照旧每次都把原因说出来；
  * 「为什么不可用」那句原话一字不删，新子句接在它后面 —— 本刀加主语，不改判据；
  * 手册要同时有这两种形状（只读手册的 agent 也要能分辨自己刚付没付钱）。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp import guide, reranker, search


def _row():
    return [search.RawResult(title="t", url="https://x.test/1", snippet="s",
                             source="bing", position=1)]


DOWNLOAD_FAIL = "reranker model download failed (offline?)"
LEAN = ("neural rerank needs dhole-mcp[all] (ModuleNotFoundError: "
        "No module named 'onnxruntime')")


@pytest.fixture()
def recorded(monkeypatch):
    """把「那次尝试的账」交给 reranker 自己记：句子只引用，不另算。"""
    monkeypatch.setattr(reranker, "_reranker_unavailable_reason", DOWNLOAD_FAIL)
    monkeypatch.setattr(reranker, "_last_attempt_ms", 8043.0)
    monkeypatch.setattr(reranker, "_load_attempts", 1)
    return reranker


# ─── 1 · 这一发付过钱 ───────────────────────────────────────────

def test_the_call_that_paid_says_it_paid(recorded):
    line = search._rerank_absent_reason(attempted_here=True)
    assert line.startswith(DOWNLOAD_FAIL), line
    assert "this call" in line, line
    assert "8043 ms" in line, line
    assert "one attempt per process" in line, line


def test_the_call_that_only_reads_the_ledger_says_so(recorded):
    line = search._rerank_absent_reason(attempted_here=False)
    assert line.startswith(DOWNLOAD_FAIL), line
    assert "did not retry" in line, line
    assert "this call did not" in line, line
    # 不许把别人的账说成这一发的
    assert "this call waited" not in line and "this call made" not in line, line


def test_the_milliseconds_have_one_author(recorded):
    """句子里那颗数必须就是 reranker 记下的那颗 —— 测试与写句子的人都不许自己掐表。"""
    line = search._rerank_absent_reason(attempted_here=True)
    assert f"{round(reranker.last_attempt_ms())} ms" in line, (line, reranker.last_attempt_ms())


def test_the_attempt_counter_belongs_to_the_loader(monkeypatch):
    """计数器只在 `_load_reranker` 里加：它是「这个进程试过几次」的唯一读者。"""
    before = reranker.load_attempts()

    def boom():
        raise RuntimeError("no onnx here")

    monkeypatch.setattr(reranker, "_ensure_model", boom)
    monkeypatch.setattr(reranker, "_reranker_tried", False)
    monkeypatch.setattr(reranker, "_reranker", None)
    assert asyncio.run(reranker.ensure_reranker(download=True)) is None
    assert reranker.load_attempts() == before + 1, (before, reranker.load_attempts())


# ─── 2 · 一条都不许退化的旧形状 ─────────────────────────────────

def test_a_lean_install_stays_silent_under_auto(monkeypatch):
    monkeypatch.setattr(reranker, "_reranker_unavailable_reason", LEAN)
    monkeypatch.setattr(search, "neural_rerank", lambda *a, **k: None)
    rows = _row()
    _ranked, _scores, used, note = search._rank("q", rows, "auto")
    assert used == "merge", used
    assert note == "", f"lean 安装上 auto 照旧不该喊：{note!r}"


def test_a_named_neural_call_still_surfaces_the_reason(monkeypatch):
    monkeypatch.setattr(reranker, "_reranker_unavailable_reason", DOWNLOAD_FAIL)
    monkeypatch.setattr(reranker, "_last_attempt_ms", 1234.0)
    monkeypatch.setattr(search, "neural_rerank", lambda *a, **k: None)
    rows = _row()
    _ranked, _scores, used, note = search._rank("q", rows, "neural")
    assert used == "merge", used
    assert DOWNLOAD_FAIL in note, note


def test_the_surprising_case_is_still_the_one_that_nags(monkeypatch):
    """deps 装了却排不了 = 该被看到（这条判据本刀一个字不动，只是给句子补主语）。"""
    monkeypatch.setattr(reranker, "_reranker_unavailable_reason", DOWNLOAD_FAIL)
    monkeypatch.setattr(search, "neural_rerank", lambda *a, **k: None)
    rows = _row()
    _ranked, _scores, _used, note = search._rank("q", rows, "auto")
    assert "NOT active despite its deps being installed" in note, note


# ─── 3 · 上了线就要在册 ────────────────────────────────────────

def test_both_shapes_are_in_the_manual():
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    assert "one attempt per process" in corpus, (
        "「这一发付过钱 / 这一发没重试」这两形状没进手册：读者分不清 8s 与 1.6s 差在哪")
    assert "did not retry" in corpus, corpus[:200]
