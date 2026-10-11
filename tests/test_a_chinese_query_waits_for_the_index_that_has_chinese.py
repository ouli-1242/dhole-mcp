"""验收刀9-2（F2）：一条中文查询不许因为境外索引先填满配额就收工。

机制在 `search_metasearch` 的早退门上：`enough_results and (engines_ok >= min_engines
or elapsed >= soft_deadline)` 一成立，还没答话的引擎就被 cancel 并记成 `preempted`
（"别人够说了，不等它了"）。这条对英文是正当的 —— 一家快索引填满的配额就是本轮的答案。
对中文不是：**配额是境外索引填的，而中文长尾不在它们的库里**。2026-10-11 全真模拟 S3
量的就是这一次错配：混搜里 baidu 被这样砍掉，两轮中文查询交回的全是 low 结果，手动
`engines=["baidu"]` 一次就出公众号与知乎原文 —— 用户当时的话是"中文源是不是根本搜不到"，
而它一直在飞，只是我们决定不等。

修法不是给境内引擎加权重，也不是加一张名单让谁养：是让"等不等"这个问题按**这一条查询
用什么文字写**来回答。判据只有字符（`[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]`），
所以纯英文查询走进来的次数是零 —— 上面那条既有的早退钉子必须一字不改地继续绿。

两条边界一起钉：

* 等的上限不新开。它用的还是本轮本来就有的硬期限，到点没等到就记 `timeout`（一次真的
  没等到）而不是 `preempted`（一次我们决定不等）—— 这两桶主张相反的修法，把前者写成
  后者就是替上游把"baidu 挂了"这句话编出来。
* 没有可等的对象时一行都不许多等：baidu 已答完、已熔断、或这一轮没被点名，
  `cn_index_engines` 里那个名字就不在飞的集合里。
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from dhole_mcp import search_metasearch as m
from dhole_mcp.search_engines import cn_index_engines


def _install(monkeypatch, spec: dict):
    """spec: backend -> (delay_seconds, result_count)，替掉真索引，时钟由用例定。"""
    for name, (delay, count) in spec.items():
        def _make(name=name, delay=delay, count=count):
            class _Fake:
                disabled = False

                def __init__(self, **kwargs):
                    pass

                def search(self, *a, **k):
                    time.sleep(delay)
                    return [SimpleNamespace(title=f"{name}-{i}",
                                            href=f"https://{name}.test/{i}", body="b")
                            for i in range(count)]
            _Fake.name = name
            return _Fake
        monkeypatch.setitem(m._TEXT_ENGINES, name, _make())
    monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
    monkeypatch.setattr(m, "_BACKEND_HEALTH", {}, raising=False)
    monkeypatch.setattr(m, "_CHALLENGE_COUNTS", {}, raising=False)
    monkeypatch.setattr(m, "_SOFT_DEADLINE", 0.0)


# ─────────────── 判据本身：只有字符，没有语言猜测 ───────────────

@pytest.mark.parametrize("query", [
    "PCIe 7.0 规范 发布 128GT/s",       # 报告 S3 的原始查询：混着术语
    "缓存 过期 时间",
    "日本語のクエリです",               # 假名
    "한국어 질의입니다",                 # 谚文
])
def test_a_query_written_with_cjk_characters_names_the_engines_to_wait_for(query):
    assert cn_index_engines(query) == frozenset({"baidu", "sogou", "so360"}), query


@pytest.mark.parametrize("query", [
    "cache expiry time", "PCIe 7.0 specification release", "", None, "128 GT/s",
])
def test_a_query_without_those_characters_waits_for_nobody(query):
    """英文那一侧不许因为这一刀多等一秒：交回空集，门上的判断因此短路。"""
    assert cn_index_engines(query) == frozenset(), repr(query)


# ─────────────── 门：中文等，英文照旧收工 ───────────────

@pytest.mark.asyncio
async def test_a_chinese_query_waits_for_the_engine_that_still_owes_it(monkeypatch,
                                                                       tmp_path):
    """bing 0 秒填满配额、baidu 1.6 秒才回：中文这一轮拿到 baidu 的答案。

    软截止钉在 0 秒，所以"境外一家答完就该收工"这个条件在第一次循环就成立 —— 唯一拦住
    它的就是这一条查询写着中文。慢的那一家给 1.6 秒，比 `_LONE_FAMILY_GRACE`(1.2s) 长：
    没有这道门时它是在短等到点那一刻被取消的，不是自己没答。
    """
    monkeypatch.setattr(m, "_circuit_state_file", lambda: str(tmp_path / "cb.json"))
    _install(monkeypatch, {"bing": (0.0, 10), "baidu": (1.6, 4)})
    results, status = await m.metasearch("PCIe 7.0 规范 发布 128GT/s", 6,
                                         engines=["bing", "baidu"])
    assert status["baidu"] == "ok", status
    assert any(r["backend"] == "baidu" for r in results), results


@pytest.mark.asyncio
async def test_the_same_clock_on_an_english_query_still_preempts(monkeypatch, tmp_path):
    """对照组：同一条路、同一个时钟，纯英文查询里 baidu 仍被记 `preempted`。

    这一条是本刀的"没动别的东西"。摘掉门上的判断它照样绿；把它摘掉而上一条也照样绿，
    才说明中文那一条测的是别的事。
    """
    monkeypatch.setattr(m, "_circuit_state_file", lambda: str(tmp_path / "cb.json"))
    _install(monkeypatch, {"bing": (0.0, 10), "baidu": (1.6, 4)})
    results, status = await m.metasearch("pcie specification release", 6,
                                        engines=["bing", "baidu"])
    assert status["baidu"] == "preempted", status
    assert all(r["backend"] != "baidu" for r in results), results


@pytest.mark.asyncio
async def test_a_dead_engine_is_called_timeout_not_a_decision_we_made(monkeypatch,
                                                                       tmp_path):
    """硬期限到点还没等到：记 `timeout`。把这两桶写反就是编出一句"baidu 挂了"。

    `preempted` 进 engine_preempted（健康快池的正常现象，主张"再问一家"），`timeout` 进
    engine_blocked（主张"这一家今天别问了"）。这里把硬期限压到 1 秒、让 baidu 飞 5 秒，
    量的就是"到点"那一句而不是"等到"那一句。
    """
    monkeypatch.setattr(m, "_circuit_state_file", lambda: str(tmp_path / "cb.json"))
    _install(monkeypatch, {"bing": (0.0, 10), "baidu": (5.0, 4)})
    monkeypatch.setattr(m, "_SEARCH_DEADLINE", 1.0)
    _t0 = time.monotonic()
    _results, status = await m.metasearch("缓存 过期 时间", 6, engines=["bing", "baidu"])
    assert status["baidu"] == "timeout", status
    assert time.monotonic() - _t0 < 3.0, "等的是硬期限，不是慢引擎自己的秒数"


@pytest.mark.asyncio
async def test_nothing_is_waited_for_when_the_cn_engine_answered_first(monkeypatch,
                                                                       tmp_path):
    """baidu 先答完：门上没有可等的对象，本轮立刻收工（不许为"中文"二字白等）。"""
    monkeypatch.setattr(m, "_circuit_state_file", lambda: str(tmp_path / "cb.json"))
    _install(monkeypatch, {"baidu": (0.0, 10), "brave": (5.0, 4)})
    _t0 = time.monotonic()
    _results, status = await m.metasearch("缓存 过期 时间", 6,
                                         engines=["baidu", "brave"])
    assert status["baidu"] == "ok", status
    assert status["brave"] == "preempted", status
    assert time.monotonic() - _t0 < 2.5, "brave 是决定不等的那一家"
