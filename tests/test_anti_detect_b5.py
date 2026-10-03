"""B5-13a：反爬伪装收成一颗 `anti_detect` 四档（§3-19）。

判据是一句话：**伪装是"发版才能改"的东西 → 收档位、不留逃生门；渲染时机是
"agent 当场就能看见失败"的东西 → 不适用那条。** 所以这一刀收走 6 颗公开旋钮
（headless / real_chrome / hide_canvas / block_webrtc / solve_cloudflare /
useragent），留下 wait / network_idle 那一类。

两条由实测决定的落点，都不是"照表删"：

* **没有任何一档传 `real_chrome=True`。** `_launch_channel(False)` 已经用
  `_detect_chrome_channel()` 在装了 Chrome 的机器上走 channel='chrome'；传 True
  只是在没装的机器上硬失败。文档里那条"aggressive 会不会装错 artifact"的担心，
  因此从源头消失 —— 也顺手把 B3 的承诺（首用只装 chromium）钉住。
* **`useragent` 是真删，不是收进档位。** HTTP 层的 UA 必须配 primp 的
  TLS/JA3（`impersonate_os`），浏览器层的 UA 必须配 `_FINGERPRINT_PROFILES`；
  手传 UA 造出"UA 说 Chrome、指纹说别的"这种自相矛盾的指纹，是比不设更强的
  识别信号。

`auto` 档逐位等于 17.0 之前的默认：这一刀的定义是"收参数"，不是"改行为"，
所以下面第 3 条断言是整批里最贵的一条 —— 它红了就说明我在改反爬效果，而不是
在改参数名。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp import browser as _browser
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer as Server

URL = "https://example.com/"

RETIRED = ("headless", "real_chrome", "hide_canvas", "block_webrtc",
           "solve_cloudflare", "useragent")

# 17.0 B5-13 之前 web_read 签名里那六颗的默认值 —— 逐位抄下来，
# 用来证明 auto 档没动行为。
OLD_DEFAULTS = {"headless": True, "real_chrome": False, "hide_canvas": True,
                "block_webrtc": True, "solve_cloudflare": True, "useragent": None}


def _tier_kwargs(monkeypatch, **call):
    """走一次真实分发，抓浏览器层实际收到的参数。

    两条路径（force_fetcher='stealthy' 与 http→stealthy 自动升级）最后都汇到
    `stealthy_fetch`，所以断言打在汇点上，一次覆盖两条路。
    """
    seen: dict = {}

    async def fake(self, url, **kwargs):
        seen.update(kwargs)
        seen["url"] = url
        from dhole_mcp.server import ResponseModel
        return ResponseModel(url=url, status=200, content=["body"],
                             fetcher_used="stealthy")

    monkeypatch.setattr(Server, "stealthy_fetch", fake)
    # 自动会话会启动一台真浏览器；这一刀要读的是"浏览器层收到了什么参数"，
    # 不是浏览器本身。
    async def fake_session(self, **kw):
        return "auto-under-test"

    monkeypatch.setattr(Server, "_ensure_auto_session", fake_session)
    # cache_ttl=0：每一次都要真的走到浏览器层。第一档把 example.com 存进缓存之后，
    # 第二档会从缓存返回，`seen` 就成了空的 —— 这种假失败比真失败更难查。
    args = {"url": URL, "force_fetcher": "stealthy", "cache_ttl": 0}
    args.update(call)
    asyncio.run(Server()._dispatch("web_read", args))
    return seen


def _defs():
    return {td["name"]: td for td in Server._TOOL_DEFS}


# ─── 公开面：一颗换掉六颗 ──────────────────────────────────────────

def test_web_read_advertises_one_level_param_and_no_hardening_knobs():
    props = _defs()["web_read"]["inputSchema"]["properties"]
    assert "anti_detect" in props, sorted(props)
    assert set(props["anti_detect"]["enum"]) == set(server_mod._ANTI_DETECT), \
        "档位表与 schema 必须同一份"
    assert len(props["anti_detect"]["description"]) <= 200
    gone = sorted(k for k in RETIRED if k in props)
    assert not gone, f"档位收了这几颗却还留在公开面：{gone}"
    assert "anti_detect" in server_mod._TOP_LEVEL_ARGS["web_read"]
    assert not set(RETIRED) & server_mod._TOP_LEVEL_ARGS["web_read"]
    # 六颗换一颗，这一刀的净账是 40 → 35；B5-13c 再把 auth / cookies / proxy /
    # extra_headers / allow_private 五颗搬进 cred_defaults，净账 35 → 30；B5-13d
    # 又把渲染时机那两颗（wait / network_idle）收成浏览器层的一条规矩，30 → 28；
    # B5-14a 把写操作那三颗（method / body / content_type）搬去 web_request，28 → 25。
    # §3-12 的 ≤20 由 tests/test_wire_shape.py 整块 xfail(strict) 盯着，等 B5-14b
    #（web_act 拆走 actions 那一家）把剩下的搬完再摘牌 —— 这里若预先断言 ≤20，
    # 就是在替别的批次许它还没实现的愿。
    assert len(server_mod._TOP_LEVEL_ARGS["web_read"]) == 25, \
        len(server_mod._TOP_LEVEL_ARGS["web_read"])


def test_the_two_axes_stayed_two_axes():
    """两条轴不许混：伪装收进档位，时机收成规矩 —— 谁也不许替对方做决定。

    13a 的时候时机还留在参数面（那条测试钉的是"它俩没被吸进 anti_detect"）。13d
    之后时机那颗也没了：`wait` 彻底删，`network_idle` 变成认出壳页才付的 5 秒，
    判定的证人搬到 tests/test_render_settle_b5.py。这里守的是另一半：档位的枚举
    里不许混进时机（一个决定"像不像机器人"的旋钮不该同时决定"读不读得到内容"）。
    """
    props = set(_defs()["web_read"]["inputSchema"]["properties"])
    assert not {"wait", "network_idle"} & props, sorted(props)
    assert set(_defs()["web_read"]["inputSchema"]["properties"]["anti_detect"]["enum"]) \
        == {"off", "basic", "auto", "aggressive"}


@pytest.mark.parametrize("knob", RETIRED)
def test_a_retired_knob_raises_instead_of_being_ignored(knob):
    """§3-20 的静默忽略只管已注销的 DHOLE_* 环境变量。

    参数面上「未知键必须抛」是守卫（`_reject_unknown_args`），删掉的旋钮也是陌生键：
    悄悄吞掉一次 `real_chrome=true`，调用方就会相信那一档开过。
    """
    with pytest.raises(ValueError, match="Unsupported argument"):
        asyncio.run(Server()._dispatch("web_read", {"url": URL, knob: True}))


def test_an_unknown_level_names_the_four():
    # web_read 的坏参数走的是信封而不是异常（与 urls 那条同一条口径），
    # 所以这里读的是回包里那句话，不是 raise。
    content, structured = asyncio.run(
        Server()._dispatch("web_read", {"url": URL, "anti_detect": "paranoid"}))
    blob = str(content) + str(structured)
    assert "anti_detect" in blob, blob[:200]
    for level in server_mod._ANTI_DETECT:
        assert level in blob, f"报错没列出 {level} 这一档"


# ─── 行为账：auto 必须逐位等于删之前的默认 ─────────────────────────

def test_auto_is_bit_for_bit_the_pre_refactor_default(monkeypatch):
    seen = _tier_kwargs(monkeypatch)
    for name, was in OLD_DEFAULTS.items():
        assert seen.get(name) == was, f"auto 档把 {name} 从 {was!r} 改成了 {seen.get(name)!r}"


@pytest.mark.parametrize("level,expected", [
    ("off", {"hide_canvas": False, "block_webrtc": False, "solve_cloudflare": False}),
    ("basic", {"hide_canvas": True, "block_webrtc": True, "solve_cloudflare": False}),
    ("auto", {"hide_canvas": True, "block_webrtc": True, "solve_cloudflare": True}),
    ("aggressive", {"hide_canvas": True, "block_webrtc": True, "solve_cloudflare": True}),
])
def test_each_level_sets_the_hardening_it_claims(monkeypatch, level, expected):
    seen = _tier_kwargs(monkeypatch, anti_detect=level)
    for name, want in expected.items():
        assert seen.get(name) is want, f"{level} 档的 {name} 不是 {want}"


def test_aggressive_stops_trimming_resources(monkeypatch):
    """真浏览器不裁资源：这是 aggressive 相对 auto 唯一说得清的另一件事。

    强度不该靠"强制某个 channel"来表达（见下面那条 artifact 测试），而
    `disable_resources` 是既有管线里唯一一个"更像真浏览器"的开关。
    """
    assert _tier_kwargs(monkeypatch, anti_detect="auto")["disable_resources"] is True
    assert _tier_kwargs(monkeypatch,
                        anti_detect="aggressive")["disable_resources"] is False


def test_no_level_asks_for_an_artifact_the_machine_does_not_have(monkeypatch):
    """干净环境 + aggressive：不许去要它没装的东西，也不许触发新下载。

    `launch_artifact(real_chrome=True)` 回的是 SYSTEM_CHROME（"不用下载"），真正
    会痛的是起不来：channel='chrome' 在没装 Chrome 的机器上是硬失败。所以四档一律
    不传它。这里把"这台机器有没有 Chrome"钉成没有 —— 不钉的话，这条在装了 Chrome
    的开发机上读的是另一台机器。
    """
    monkeypatch.setattr(_browser, "_chrome_channel_cache", "chromium")
    for level in server_mod._ANTI_DETECT:
        seen = _tier_kwargs(monkeypatch, anti_detect=level)
        assert seen["real_chrome"] is False, f"{level} 档把 channel 钉死了"
        assert _browser.launch_artifact(seen["real_chrome"]) == "chromium", \
            f"{level} 档要的 artifact 不是自带 chromium"


def test_the_ladder_does_not_need_the_chrome_knob_because_detection_has_it(monkeypatch):
    """装了 Chrome 的机器本来就在用它，不必有人传 `real_chrome=true`。

    删掉这颗旋钮不是少了一种能力：`_launch_channel(False)` 走
    `_detect_chrome_channel()`，装了就用；传 True 只是把"没装"那种情况从降级
    变成硬失败。
    """
    monkeypatch.setattr(_browser, "_chrome_channel_cache", "chrome")
    seen = _tier_kwargs(monkeypatch, anti_detect="aggressive")
    assert seen["real_chrome"] is False
    assert _browser._launch_channel(seen["real_chrome"]) == "chrome"


def test_every_level_leaves_the_ua_with_its_profile(monkeypatch):
    """手传 UA 会与 TLS/指纹配套冲突：档位面必须根本没有这个口。"""
    for level in server_mod._ANTI_DETECT:
        assert _tier_kwargs(monkeypatch, anti_detect=level)["useragent"] is None


# ─── 扇出面：bulk 是第二条路 ───────────────────────────────────────

def test_the_rung_survives_the_bulk_fanout(monkeypatch):
    """`urls=[...]` 走的是 `_smart_fetch_bulk`，一条和单 URL 平行的路。

    这一条是本批唯一真红过的地方：档位参数换掉六颗时，bulk 的签名与转发都还是
    老样子，于是 `anti_detect` 在 `_fetch_one` 里 NameError，被那一层的
    `except Exception` 吞成一行 status=0 的错误 —— 回包里看不出任何事，只有
    "每个 URL 都失败"。单 URL 测试全绿也照不出来。
    """
    seen: list = []

    async def fake(self, url, **kwargs):
        seen.append(kwargs)
        from dhole_mcp.server import ResponseModel
        return ResponseModel(url=url, status=200, content=["body"],
                             fetcher_used="stealthy")

    async def fake_session(self, **kw):
        return "auto-under-test"

    monkeypatch.setattr(Server, "stealthy_fetch", fake)
    monkeypatch.setattr(Server, "_ensure_auto_session", fake_session)
    out = asyncio.run(Server().web_read(
        URL, urls=[URL, "https://other.example.org/"],
        force_fetcher="stealthy", cache_ttl=0, anti_detect="aggressive"))
    assert out.successful == out.total == len(seen) == 2, \
        f"bulk 里有一行没走到浏览器层：{[r.error for r in out.results]}"
    for kw in seen:
        assert kw["disable_resources"] is False
        assert kw["hide_canvas"] is True and kw["block_webrtc"] is True
        assert kw["useragent"] is None
