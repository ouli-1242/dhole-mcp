# -*- coding: utf-8 -*-
"""R02-056 · 一副说不出自己 brand 表的 Chrome 身份，不许就这么发出去。

题面（全量线抓的）：`test_the_ua_and_the_brand_list_agree_in_the_baseline` 每次进程重摇一副，
约 0.4% 的概率红 —— 而它的 skip 只写了"非 Chromium 的一次抽签"，"抽中了 Chromium 但 brand 表
整颗缺席"那一支没有出口。台账留的下一问是「一副声称 Chrome 却不带任何 Client Hints 的身份该不该发」。

**实测把这一问的前提换掉了**（`review/.runtime/p4/r56_wire_measure.py`：钉 chrome 家族摇 1500 副
= 6 副缺 brand 表；把其中一副经真 `HTTPSession` 打进只绑回环的 echo）：

    字典里 sec-ch-ua  = ''
    线上收到 sec-ch-ua = '"Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"'
    线上 user-agent   = ... Chrome/114 ...

也就是线上看到的**不是**"没有 Client Hints"，而是 UA 说 114、brand 表说 149 —— 两颗头来自两台机器，
因为字典缺位时说话的是 primp 自己的预设。对照组一副带 brand 表（`v="147"`）的身份，线上逐字等于
字典 ⇒ 字典在场时盖得住预设，缺位时那一颗头交给**另一次抽签**（同一进程里两发还各摇出 149 与 152，
见 R02-080）。把字典 UA 硬改成不可能的 `Chrome/999`，brand 表照样来 ⇒ 它从不跟着 UA 走。

所以这一族的罪不是"少发一颗头"（那种形状不存在），而是 `_stealthy_baseline` 的 docstring 早就
立过的那一条：**几颗头来自三台机器**。修法 accordingly：交出去之前判一句「UA 声称 Chromium 一家
（`Chrome/` 或 `Edg/`）的字典，brand 表必须带着自己那一个大版本」，不带就**有界地重摇**。

三条边界，都是判据：
* **不补值**。字典缺的那一颗头由发出请求的那一层决定，我们替它编一个"更像的"就是第三份判决
  —— `_drop_empty_markers` 立的同一条例（第 7 格守着：`_build_headers` 不许凭空长出 sec-ch-ua）。
* **不判非 Chromium**。firefox / safari 本来就不发 Client Hints，缺得合法，一颗都不许多摇。
* **兜底不比今天差**。有界次数内始终摇不到自洽的一副，交回最后一副而不是空手或抛错。

真实生成器那一条腿从此不再"摇一副看运气"：连摇 K 副，每一副都要自洽（§14 的门要可复现）。
"""
from __future__ import annotations

import re

import pytest

from dhole_mcp import fetcher as FT

CHROME_147_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36")
CHROME_114_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36")
EDGE_120_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.2210.91")
FIREFOX_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:139.0) "
              "Gecko/20100101 Firefox/139.0")

GOOD = {"user-agent": CHROME_147_UA,
        "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
        "sec-ch-ua-platform": '"Windows"', "accept-language": "en-US,en;q=0.9"}
BRANDLESS_CHROME = {"user-agent": CHROME_114_UA,
                    "sec-ch-ua-platform": '"macOS"', "accept-language": "en-US,en;q=0.9"}
BRANDLESS_EDGE = {"user-agent": EDGE_120_UA, "accept-language": "en-GB,en;q=0.8"}
FIREFOX = {"user-agent": FIREFOX_UA, "accept-language": "en-US,en;q=0.5"}


@pytest.fixture
def stub_generator(monkeypatch):
    """把 `HeaderGenerator` 换成一台按脚本发牌的 dealer，数得出它被摇了几次。"""
    from types import SimpleNamespace

    real = pytest.importorskip("browserforge.headers")
    state = SimpleNamespace(draws=[], calls=0, last_kwargs={})

    class _Dealer:
        def __init__(self, **kwargs):
            state.last_kwargs = dict(kwargs)

        def generate(self):
            state.calls += 1
            if len(state.draws) == 1:
                return dict(state.draws[0])
            return dict(state.draws.pop(0))

    def _set(*draws):
        state.draws = [dict(d) for d in draws]

    state.set = _set
    monkeypatch.setattr(real, "HeaderGenerator", _Dealer, raising=False)
    FT.reset_process_identity()
    yield state
    FT.reset_process_identity()


def _major(ua: str) -> str:
    m = re.search(r"(?:Chrome|Edg)/(\d+)", ua)
    return m.group(1) if m else ""


def test_a_brandless_chrome_draw_is_redrawn_instead_of_shipping(stub_generator):
    stub_generator.set(BRANDLESS_CHROME, GOOD)
    got = FT._stealthy_baseline("chrome")
    assert got == GOOD, got
    assert stub_generator.calls == 2, "第一副就该被拦下，第二副才交出去"


def test_an_edge_draw_without_a_brand_list_is_redrawn_too(stub_generator):
    """判据认的是"声称 Chromium 一家"，不是某一个 UA 字符串 —— 别家同名形状一起管。"""
    good_edge = {"user-agent": EDGE_120_UA,
                 "sec-ch-ua": '"Microsoft Edge";v="120", "Chromium";v="120", '
                              '"Not_A Brand";v="8"'}
    stub_generator.set(BRANDLESS_EDGE, good_edge)
    got = FT._stealthy_baseline("edge")
    assert got == good_edge, got
    assert stub_generator.calls == 2


def test_the_redraw_is_bounded_and_never_leaves_the_call_empty_handed(stub_generator):
    stub_generator.set(BRANDLESS_CHROME)          # 永远摇不到自洽的一副
    got = FT._stealthy_baseline("chrome")
    assert got == BRANDLESS_CHROME, f"兜底要交回最后一副，不许空手：{got}"
    assert stub_generator.calls == FT._IDENTITY_REDRAWS * 2, (
        f"重摇必须有界：摇了 {stub_generator['calls']} 次")


def test_a_firefox_draw_needs_no_brand_list_and_ships_at_once(stub_generator):
    """firefox / safari 不发 Client Hints 是**合法形状**，多摇一次都是白付。"""
    stub_generator.set(FIREFOX)
    assert FT._stealthy_baseline("firefox") == FIREFOX
    assert stub_generator.calls == 1, stub_generator.calls


def test_the_process_identity_stores_the_coherent_draw(stub_generator):
    """钉在进程上的那一份必须是自洽的那一副 —— 会话与批量读的都是它。"""
    stub_generator.set(BRANDLESS_CHROME, GOOD)
    first = FT._process_identity("chrome")
    second = FT._process_identity("chrome")
    assert first == GOOD, first
    assert second is first or second == first, "同一进程同一家族只许摇一次"
    assert stub_generator.calls == 2


def test_a_missing_brand_slot_is_not_filled_by_invention(stub_generator):
    """字典缺的那一颗由发出请求的那一层决定；`_build_headers` 不许凭空长出一颗 sec-ch-ua。

    这一格守的是修法的**边界**：重摇是为了不再让预设替我们说话，不是为了自己编一套。
    """
    stub_generator.set(BRANDLESS_CHROME)
    s = FT.HTTPSession(stealthy_headers=True)
    s._baseline = dict(BRANDLESS_CHROME)
    out = s._build_headers()
    assert "sec-ch-ua" not in out, sorted(out)
    assert "user-agent" in out


def test_the_real_generator_ships_an_identity_whose_brands_follow_their_ua():
    """可复现的那一条腿：连摇 K 副，每一副自称 Chromium 的都必须带自己那一颗大版本。

    从前这里是"摇一副，抽中 Chromium 就断言"——同一份代码两种结局（全量里红、单跑里绿），
    而 §14 的『全量绿』门不许建在概率上。
    """
    if not FT._stealthy_baseline("chrome"):
        pytest.skip("这台机器上没有 browserforge")
    bad = []
    for _ in range(40):
        b = FT._stealthy_baseline("chrome")
        major = _major(b.get("user-agent", ""))
        if major and f';v="{major}"' not in b.get("sec-ch-ua", ""):
            bad.append((b.get("user-agent", "")[:60], b.get("sec-ch-ua", "")))
    assert not bad, f"{len(bad)} 副交不出自己的 brand 表：{bad[:3]}"


def test_the_pinned_draw_still_pins_desktop_and_family(stub_generator):
    """重摇不许把原先钉住的那两位丢掉：桌面形状与家族都是 primp 握手要跟着走的。"""
    stub_generator.set(BRANDLESS_CHROME, GOOD)
    FT._stealthy_baseline("chrome")
    assert stub_generator.last_kwargs == {"device": ["desktop"], "browser": ["chrome"]}
