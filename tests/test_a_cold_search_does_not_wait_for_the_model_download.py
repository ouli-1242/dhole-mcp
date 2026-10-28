"""A cold search must not pay for the reranker download inside the call.

Measured on a real stdio server, 2026-10-28: with a fresh DHOLE_HOME the first
`web_search` took 200.0s and 206.5s (fetching the 278MB bge-zh model), while a
host cuts a tool call at 30-120s; the in-batch call was killed at 120s and
returned nothing. A model already on disk loads in ~1-2s, so the wait is bounded:
past it the download keeps running in the background (resumable) and this round
ranks by consensus with a sentence saying so.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dhole_mcp import search as search_mod
from dhole_mcp.search_engines import EngineReport, RawResult

WAIT = 0.1
REPO = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.smoke


def _reset_warm() -> None:
    search_mod._RERANK_WARM = None


async def _cancel_warm() -> None:
    cached = search_mod._RERANK_WARM
    if cached:
        task = cached[1]
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
    _reset_warm()


@pytest.fixture(autouse=True)
def _warm_is_per_test():
    _reset_warm()
    yield
    cached = search_mod._RERANK_WARM
    if cached:
        task = cached[1]
        if not task.done():
            task.cancel()


@pytest.mark.asyncio
async def test_the_warm_wait_is_bounded_and_the_download_keeps_running(monkeypatch):
    started = asyncio.Event()

    async def slow_ensure(*args, **kwargs):
        started.set()
        await asyncio.sleep(60)

    monkeypatch.setattr(search_mod, "ensure_reranker", slow_ensure)
    monkeypatch.setattr(search_mod, "RERANK_WARM_WAIT_S", WAIT)
    task = search_mod._rerank_warm_task()
    await asyncio.wait_for(started.wait(), 5)

    started_at = time.monotonic()
    warmed = await search_mod._warm_reranker_within_budget(task)
    elapsed = time.monotonic() - started_at

    try:
        assert warmed is False
        assert elapsed < 1.0, f"the search waited {elapsed:.1f}s for the download"
        assert not task.done(), "the wait timed out by cancelling the download"
    finally:
        await _cancel_warm()


@pytest.mark.asyncio
async def test_a_search_answers_by_consensus_while_the_model_is_fetching(monkeypatch):
    async def slow_ensure(*args, **kwargs):
        await asyncio.sleep(60)

    async def fake_multi_search(query, max_results, **kwargs):
        return ([RawResult(
            title="asyncio documentation",
            url="https://docs.python.org/3/library/asyncio.html",
            snippet="asyncio is a library to write concurrent code",
            source="bing", position=1)],
            [EngineReport(name="bing", ok=True)])

    async def no_cache(*args, **kwargs):
        return None

    async def keep(*args, **kwargs):
        return None

    monkeypatch.setattr(search_mod, "ensure_reranker", slow_ensure)
    monkeypatch.setattr(search_mod, "RERANK_WARM_WAIT_S", WAIT)
    monkeypatch.setattr(search_mod, "multi_search", fake_multi_search)
    monkeypatch.setattr(search_mod, "get_cached", no_cache)
    monkeypatch.setattr(search_mod, "set_cached", keep)

    started_at = time.monotonic()
    result = await search_mod.web_search(None, "asyncio tutorial", max_results=3)
    elapsed = time.monotonic() - started_at

    try:
        assert elapsed < 2.0, f"one search paid {elapsed:.1f}s for the download"
        assert result.results, "the round must still deliver its rows"
        assert "still being fetched" in (result.fetch_hint or ""), result.fetch_hint
        assert search_mod._RERANK_WARM is not None
        assert not search_mod._RERANK_WARM[1].done(), (
            "the download must survive the call that started it")
    finally:
        await _cancel_warm()


@pytest.mark.asyncio
async def test_a_done_warmup_is_not_replaced_by_a_second_download(monkeypatch):
    calls: list[int] = []

    async def fast_ensure(*args, **kwargs):
        calls.append(1)
        return None

    monkeypatch.setattr(search_mod, "ensure_reranker", fast_ensure)
    first = search_mod._rerank_warm_task()
    await first
    second = search_mod._rerank_warm_task()
    try:
        assert second is not first, "a finished task should not be awaited forever"
        await second
        assert len(calls) == 2
    finally:
        await _cancel_warm()


def test_a_one_shot_process_exits_while_the_download_still_runs():
    """The CLI is `asyncio.run` around one tool call: results print, then the
    process must return to the shell. The download lives in a daemon thread
    precisely because `asyncio.run` waits out default-executor threads - on a
    real cold `dhole search` that turned a 5.8s answer into a 3m17s process."""
    script = r"""
import asyncio, time
from dhole_mcp import reranker

def slow_load():
    time.sleep(60)
    return None

reranker._load_reranker = slow_load

async def main():
    task = asyncio.create_task(reranker.ensure_reranker())
    try:
        await asyncio.wait_for(asyncio.shield(task), 0.2)
    except asyncio.TimeoutError:
        pass

asyncio.run(main())
print("bye", flush=True)
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script],
        capture_output=True, text=True, env=env, timeout=30, cwd=str(REPO))
    elapsed = time.monotonic() - started

    assert proc.returncode == 0, proc.stderr[-800:]
    assert "bye" in proc.stdout
    assert elapsed < 10, f"the process waited {elapsed:.1f}s for a background download"
