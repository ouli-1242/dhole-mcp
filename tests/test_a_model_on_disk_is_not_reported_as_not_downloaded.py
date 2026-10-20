"""一个 `None` 不许有三种读法：模型在盘上而加载失败时，不能说「还没下载」。

现场（2026-10-15 · P4 写 R02-F11 的真机复验里量到的同族第二问 · R02-042 · P2 · 组 C）：

    # 进程内构造：模型**在盘上**（model_present() 为真），但加载这一路失败
    reranker.model_present  -> True
    reranker.get_reranker   -> None
    reranker.ensure_reranker-> None      （= 缺 onnxruntime / 加载抛错那一类）

    web_read(url=…, focus='reliability', focus_engine='neural')
      → error=「neural_focus_model_missing: focus_engine='neural' needs the local reranker
        model, which is **not downloaded yet**. No request was made.」
      → next_action=「The reranker model is not downloaded yet. A plain web_search brings
        it down on its first run …」

这一发把调用方支去**重下一份已经在盘上的模型**，而真正的成因（装不上 / 起不来）一个字没提。
同一家族的旁证是 `install.rerank_install_advice()`：它在依赖齐全时自己就写「the failure was
model state, not deps」—— 与那句"not downloaded yet"在同一台机器上互相否认。

根因是一行门只看得到一个数：`server` 的 neural focus 门问的是 `get_reranker() is None` +
`ensure_reranker(download=False) is None`。这一个 None 同时是三种情形的答案 ——
①模型不在盘上；②模型在盘上、依赖缺失；③模型在盘上、依赖齐全但加载抛错。判决把三种都写成
「not downloaded yet」。`reranker` 那边其实**已经**留着区分器（`model_present()` 与
`unavailable_reason()` 里那条 "reranker init failed: …"），门没有去读。

契约（本文件钉住）：
  * 盘上有模型时，那句话不许说"还没下载"，要把过程知道的那半说出来（读得到
    `unavailable_reason()` 就带上它），也不许把人支去下一份已经在的模型；
  * 盘上确实没有时，F11 那句话一字不动（反面锁：这一刀不许把真话改花）；
  * 两支的判决位一致且不动：`status=0`、`error` 仍以 `neural_focus_model_missing` 开头
    （那个名字标识的是**这道门**，不是成因 —— 成因由句子说）、`content_ok` 不为 true、
    「No request was made」还在；
  * `focus_engine='bm25'` 那条不要模型的出路两支都给（这是调用方这一发真能走的）。
"""

from __future__ import annotations


import pytest

from dhole_mcp import reranker as reranker_mod
from dhole_mcp.server import MasterFetchServer


async def _none(*a, **k):
    return None


@pytest.fixture
def gate_blocked(monkeypatch):
    """把这道门的两种成因都摆出来：模型在不在盘上，是这里唯一变化的那颗。"""
    monkeypatch.setattr(reranker_mod, "get_reranker", lambda *a, **k: None)
    monkeypatch.setattr(reranker_mod, "ensure_reranker", _none)

    async def _read():
        return await MasterFetchServer().web_read(
            url="https://example.com/", focus="reliability", focus_engine="neural")
    return _read


# ─── 1 · 盘上有模型：不许说"还没下载" ───

@pytest.mark.asyncio
async def test_a_model_that_is_on_disk_is_not_reported_as_undownloaded(
        gate_blocked, monkeypatch):
    monkeypatch.setattr(reranker_mod, "model_present", lambda *a, **k: True)
    monkeypatch.setattr(reranker_mod, "unavailable_reason",
                        lambda: "reranker init failed: onnxruntime is not installed")
    r = await gate_blocked()
    text = f"{r.error} {r.next_action}"
    assert "not downloaded" not in text.lower(), text
    assert "onnxruntime" in text, f"过程知道的成因没上回执：{text!r}"
    assert "bring" not in (r.next_action or "").lower() or "already" in (
        r.next_action or "").lower(), r.next_action


@pytest.mark.asyncio
async def test_the_loaded_but_failed_branch_still_offers_the_no_model_way_out(
        gate_blocked, monkeypatch):
    monkeypatch.setattr(reranker_mod, "model_present", lambda *a, **k: True)
    r = await gate_blocked()
    assert "focus_engine='bm25'" in (r.next_action or ""), r.next_action
    assert "no request was made" in (r.error or "").lower(), r.error


# ─── 2 · 盘上没有：F11 的那句话一字不动 ───

@pytest.mark.asyncio
async def test_an_absent_model_still_gets_the_download_sentence(
        gate_blocked, monkeypatch):
    monkeypatch.setattr(reranker_mod, "model_present", lambda *a, **k: False)
    r = await gate_blocked()
    na = r.next_action or ""
    assert "not downloaded yet" in (r.error or "").lower(), r.error
    assert "A plain web_search brings it down" in na, na
    assert "has no `focus_engine`" in na, na


# ─── 3 · 判决位：两支一致，一位不动 ───
@pytest.mark.asyncio
@pytest.mark.parametrize("present", [True, False])
async def test_the_verdict_is_the_same_door_both_ways(gate_blocked, monkeypatch, present):
    monkeypatch.setattr(reranker_mod, "model_present", lambda *a, **k: present)
    r = await gate_blocked()
    assert r.status == 0, r.status
    assert r.content_ok is not True, r.content_ok
    assert (r.error or "").startswith("invalid_request: neural_focus_model_missing"), r.error
    assert "No request was made" in (r.error or ""), r.error
    assert r.fetcher_used == "none", r.fetcher_used


# ─── 4 · 反面锁：盘上有模型时不许再指向那颗下载（F11 的口径） ───

@pytest.mark.asyncio
async def test_the_on_disk_branch_does_not_point_at_the_download_door(
        gate_blocked, monkeypatch):
    """`web_search` 那一发**取不回**这一发缺的东西 —— 模型已经在盘上。

    F11 立的是「出路不许点名一门开不了的门」；这一刀的现场正是它的同族：把调用方支去
    重下一份已经在的模型。所以这一支里 `web_search` 一个字都不许出现，而句子要说清
    "没有东西要下载"。
    """
    monkeypatch.setattr(reranker_mod, "model_present", lambda *a, **k: True)
    r = await gate_blocked()
    na = r.next_action or ""
    assert "web_search" not in na, na
    assert "already on disk" in na, na
    assert "nothing to download" in na, na


@pytest.mark.asyncio
async def test_the_crawl_gate_shares_both_branches(monkeypatch):
    """两位读者读的同一位作者：`web_crawl` 那一侧也按成因分两支。"""
    import dhole_mcp.reranker as reranker_mod
    from dhole_mcp.server import MasterFetchServer

    async def _none(*a, **k):
        return None

    monkeypatch.setattr(reranker_mod, "get_reranker", lambda *a, **k: None)
    monkeypatch.setattr(reranker_mod, "ensure_reranker", _none)

    async def _crawl():
        return await MasterFetchServer().web_crawl(
            url="https://example.com/", focus="reliability",
            focus_engine="neural")

    monkeypatch.setattr(reranker_mod, "model_present", lambda *a, **k: True)
    monkeypatch.setattr(reranker_mod, "unavailable_reason",
                        lambda: "reranker init failed: onnxruntime is missing")
    on_disk = await _crawl()
    assert "neural_focus_model_missing" in (on_disk.error or ""), on_disk.error
    assert "not downloaded" not in f"{on_disk.error} {on_disk.next_action}".lower()
    assert "onnxruntime" in f"{on_disk.error} {on_disk.next_action}", on_disk.next_action
    assert "crawl" in (on_disk.next_action or ""), on_disk.next_action

    monkeypatch.setattr(reranker_mod, "model_present", lambda *a, **k: False)
    absent = await _crawl()
    assert "not downloaded yet" in (absent.error or "").lower(), absent.error
    assert "A plain web_search brings it down" in (absent.next_action or "")
