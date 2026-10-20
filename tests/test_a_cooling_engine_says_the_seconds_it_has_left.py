"""验收刀9-4（F11）：引擎冷却要报得出"还剩几秒"，不是报得出"有 60s 这一档"。

报告里那句话是"越用越难搜，但没人说为什么"。量的时候池子从 8 家掉到 2 家：brave / sogou
在冷却、yandex 连不上、baidu 超时。响应里**从来不是没有名字** —— `engine_blocked` 与
`fetch_hint` 那句"Engines X, Y didn't contribute"一直在报谁；缺的是**还要多久**。而那个数
dhole 自己知道：`search_metasearch._BACKEND_HEALTH` 存的就是每家绝对解禁时刻，
`search_engines.py` 给 `circuit_open` 那一格写 "retried in Ns" 用的也是它 —— 只有
`_recall_levers` 这一句把它写成了档位（60s / 600s），于是调用方手里有一张表却没有数。

档位句留着，但它现在的作用是解释那个数是**怎么来的**；两个方向都不许说谎：

* 量不到就不报秒。`cooldowns()` 读的是进程内那份表，被替身层调到的时候它是空的；一句
  关于没量过的事的秒数比不报更坏（这一族的本仓库病根就是这个）。
* 缓存命中那一轮不许报秒。它根本没重新问引擎，"retried in 41s" 是一个兑现不了的承诺 ——
  同一个缺陷从前写在 "retry shortly" 上（体验修复刀2-S7），这次只是换了个数字。
"""

from __future__ import annotations

from time import time

import pytest

from dhole_mcp import search as S
from dhole_mcp import search_metasearch as M


def _row():
    return S.SearchResult(title="t", url="https://e.test/1", relevance_score=0.9,
                          fetch_relevance="high")


@pytest.fixture
def cooled(monkeypatch):
    """把熔断表钉成"brave 还有 41 秒、sogou 还有 611 秒"。

    `_BACKEND_HEALTH` 存的是绝对解禁时刻，所以这里写 `time() + 剩余`，与真代码同一个
    形状 —— 不是替 `cooldowns()` 打桩：那等于只测了自己的编排。
    """
    table = {"brave": time() + 41.0, "sogou": time() + 611.0}
    monkeypatch.setattr(M, "_BACKEND_HEALTH", table, raising=False)
    return table


def test_the_cooling_engines_are_named_with_the_seconds_they_have_left(cooled):
    got = S._recall_levers(["brave", "sogou"], [], replay=False)
    assert "brave 41s" in got, got
    assert "sogou 611s" in got, got
    # 排在前面的必须是剩得最久的那一家：`whole again in` 说的就是它。
    assert "the pool is whole again in 611s" in got, got


def test_the_tiers_stay_written_as_the_origin_of_the_number(cooled):
    """秒数不许变成一个新的、没有出处的数：档位那一半还在，它解释这个数怎么来的。"""
    got = S._recall_levers(["brave"], [], replay=False)
    assert "60s after a hard block" in got and "600s after" in got, got


def test_a_refusal_that_is_not_a_cooldown_gets_no_invented_seconds():
    """没有观测就不写秒：这一族被替身层直接调到时 `_BACKEND_HEALTH` 是空的。"""
    got = S._recall_levers(["yandex"], [], replay=False)
    assert "cooling right now" not in got, got
    assert "retry after the cooldown clears" in got, got


@pytest.mark.parametrize("seconds", [0.0, -3.0])
def test_a_cooldown_that_already_expired_is_not_counted(cooled, seconds):
    cooled["brave"] = time() + seconds
    got = S._recall_levers(["brave", "sogou"], [], replay=False)
    assert "brave 41s" not in got, got
    assert "sogou 611s" in got, got


def test_a_replayed_round_promises_no_seconds(cooled):
    """缓存命中那一轮根本没重新问引擎：这里只能说出处，不能说还剩几秒。"""
    got = S._recall_levers(["brave", "sogou"], [], replay=True)
    assert "replayed from the cache" in got, got
    assert "cooling right now" not in got, got
    assert "retry after the cooldown clears" not in got, got


def test_an_engine_that_answered_is_still_offered_no_wait(cooled):
    """反向的钉子（S7）：等冷却治不好一个已经开口的引擎，这一族一条都不许多说。"""
    got = S._recall_levers([], ["brave"], replay=False)
    assert "cooldown" not in got and "cooling" not in got, got
    assert "rephrase if the query is narrow" in got, got


def test_the_seconds_reach_the_field_agents_read(cooled):
    """`_recall_levers` 的话必须落在 `next_action` 上，不停在私有函数里。

    ratio 故意压在 0.6 之下（2 of 6）：那条 LOW diversity 的 WARNING 不会触发，于是
    这一格里能读出来的就只剩"谁在冷却、还剩几秒"这一句。
    """
    got = S._search_next_action([_row()], ["brave", "sogou"], "", ["bing"],
                                total_engines=6)
    assert "brave 41s" in got and "sogou 611s" in got, got
    assert "the pool is down to 4 of the 6 engines it asked" in got, got


def test_the_pool_count_is_not_said_when_only_one_engine_is_cooling(cooled):
    """两家还有一家在冷却不算"池子掉了"：那句只在 ≥2 时说（计划里的门槛）。"""
    got = S._search_next_action([_row()], ["brave"], "", ["bing", "sogou", "baidu"],
                                total_engines=6)
    assert "the pool is down to" not in got, got
    assert "brave 41s" in got, got


def test_the_pool_count_is_not_said_without_a_denominator(cooled):
    """`asked` 不知道就不说剩几家 —— 一个没有分母的"down to 4"比不说更误导。"""
    got = S._recall_levers(["brave", "sogou"], [], replay=False)
    assert "the pool is down to" not in got, got
    assert "cooling right now" in got, got


def test_the_parenthesis_opens_on_a_word_not_on_a_semicolon(cooled):
    """R01-047（R01-016 真 CLI 复验时读到）：那对括号里第一眼是一个分号。

    ```
    retry after the cooldown clears (; cooling right now: so360 58s; the pool is whole
    again in 59s - 60s after a hard block …)
    ```

    `who` 那一段是为"接在前一句话后面"写的，所以自带一个 "; " 前缀；把它整个塞进
    "(" 之后，读者第一眼看到的是一件还没开始就说完的事。分句的接缝由句子的作者收尾，
    不由被拼接的那一段自己带。
    """
    got = S._recall_levers(["brave", "so360"], [], replay=False)
    assert "(; " not in got, got
    assert "(cooling right now: brave 41s" in got or "(cooling right now: so360 61s" in got, got
