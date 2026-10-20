# -*- coding: utf-8 -*-
"""R02-076 · 生成器的「可以为空」记号 `?1` 不许当作值发出去。

实测（`review/.runtime/p4/r75_who_sends.py` 那一趟，加上连打 6 发的回环 echo）：

  * `_stealthy_baseline()` 交出的字典里，`sec-fetch-site` 在 **537/600 副**是 `?1`；
  * dhole 的 `HTTPSession(impersonate="chrome")` 打 6 发 echo，**6/6 行**收到
    `sec-fetch-site: ?1`；
  * 同一颗头**裸 primp**（不盖我们的字典）发出去的是 `none`。

⇒ 那个 `?1` 是 browserforge 给「这颗头可以留空」写的记号，不是任何浏览器会发的值；我们逐颗
把字典盖到 primp 的请求上，正好盖掉了 primp 自己那份合法枚举词，换成一个语法非法、又恰好是
「合成头」招牌的形状 —— 反检测那一档最不该带的就是这种自我申报。

另一颗长得像但不是病的：`sec-ch-ua-mobile: ?0`。那**就是** Client Hints 布尔 hint 的合法语法
（`?1` 手机 / `?0` 不是），600 副里 531 副带着它。所以修法不能写成「值以 `?` 开头就丢」——
那会把布尔 hint 一起瞎掉。判据是**这颗头自己的语法**：fetch 族（`sec-fetch-site` / `-mode` /
`-user` / `-dest`）的取值是枚举词，里面没有 `?n` 这一种拼法；丢掉的键交给 TLS 预设那一侧
（一位作者：我们不替 primp 决定它的头该说什么，也不给它编一个"更像"的值）。

反面锁：不按域名/UA/某颗名字特判；不误伤合法枚举词；不丢 `sec-ch-ua-*`。
"""

from __future__ import annotations

import re

import pytest

from dhole_mcp import fetcher as FT

MARKER = re.compile(r"\?\d+")
needs_generator = pytest.mark.skipif(
    not FT._stealthy_baseline(), reason="这台机器上没有 browserforge，摇不出身份")


def _feed(monkeypatch, values: dict):
    """把生成器钉成一份已知的字典：测的是交给线之前的处理，不是运气。

    `HeaderGenerator` 是在 `_stealthy_baseline()` 里按需 import 的，所以要钉的是
    `browserforge.headers` 那一侧的名字 —— 钉 `FT.HeaderGenerator` 会钉出一颗没人读的属性。
    """
    import browserforge.headers as bfh

    class _G:
        def __init__(self, **kw):
            pass

        def generate(self):
            return dict(values)

    monkeypatch.setattr(bfh, "HeaderGenerator", _G)


@needs_generator
def test_no_empty_marker_survives_a_hundred_draws():
    """真抽签 120 副：fetch 族里不许出现 `?n`。这是现场那一格（0/120 vs 曾经的 ~100/120）。"""
    bad = []
    for _ in range(120):
        for key, val in FT._stealthy_baseline().items():
            if key.startswith("sec-fetch-") and MARKER.fullmatch(str(val)):
                bad.append((key, val))
    assert not bad, f"120 副里 {len(bad)} 次把「可以为空」的记号当值交出：{bad[:4]}"


@needs_generator
def test_the_boolean_hint_keeps_its_legal_question_mark():
    """正控制：`sec-ch-ua-mobile: ?0/?1` 是合法语法，清理不许把它瞎掉。"""
    seen = [FT._stealthy_baseline().get("sec-ch-ua-mobile") for _ in range(120)]
    legal = [v for v in seen if v]
    assert legal, "120 副里一颗布尔 hint 都没摇到，这一格量不到（换更大的 N 再跑）"
    assert all(MARKER.fullmatch(str(v)) for v in legal), legal
    assert all(str(v) in ("?0", "?1") for v in legal), legal


def test_a_marker_is_dropped_and_its_neighbours_untouched(monkeypatch):
    fake = {
        "user-agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/101.0.4951.54",
        "sec-fetch-site": "?1",
        "sec-fetch-mode": "same-site",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "referer": "https://www.google.com/",
    }
    _feed(monkeypatch, fake)

    got = FT._stealthy_baseline()

    assert "sec-fetch-site" not in got, f"记号还在字典里，就会被逐颗盖到请求上：{got}"
    assert got["sec-fetch-mode"] == "same-site", got
    assert got["sec-ch-ua-mobile"] == "?0", f"布尔 hint 被瞎掉了：{got}"
    assert got["sec-ch-ua-platform"] == '"Windows"', got
    assert got["user-agent"] == fake["user-agent"], got
    assert got["referer"] == fake["referer"], got


def test_legal_enumerations_are_left_alone(monkeypatch):
    """尺自己要能弯：枚举词是合法值，一个都不许误伤；同一族里的记号照样丢。"""
    fake = {"user-agent": "UA/1", "sec-fetch-site": "cross-site",
            "sec-fetch-mode": "navigate", "sec-fetch-dest": "?3",
            "sec-fetch-user": "document", "upgrade-insecure-requests": "1"}
    _feed(monkeypatch, fake)

    got = FT._stealthy_baseline()

    assert got["sec-fetch-site"] == "cross-site", got
    assert got["sec-fetch-mode"] == "navigate", got
    assert got["sec-fetch-user"] == "document", got
    assert got["upgrade-insecure-requests"] == "1", got
    assert "sec-fetch-dest" not in got, got


def test_the_repair_is_a_grammar_rule_not_a_name_or_a_host(monkeypatch):
    """不许按某颗名字/某个域名特判：任何 fetch 族的键、任何一次抽签，走的都是同一条语法判据。"""
    for key in ("sec-fetch-site", "sec-fetch-mode", "sec-fetch-user", "sec-fetch-dest"):
        _feed(monkeypatch, {"user-agent": "UA/1", key: "?9"})
        assert key not in FT._stealthy_baseline(), key
    # 同一条判据不看 referer 不看 host —— 换一颗域名形状也还是它
    _feed(monkeypatch, {"user-agent": "UA/1", "referer": "https://anywhere.example/",
                        "sec-fetch-site": "?1"})
    assert "sec-fetch-site" not in FT._stealthy_baseline()
