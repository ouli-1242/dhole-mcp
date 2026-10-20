"""批次 11 → 看得见与否是**一次调用**要问的问题，不是一台机器的配置。

判据来自操作员那句话：「帮我搜索抓取应该是不显示的，帮我操作、登录此类就应该显示，
方便我随时辅助；多两个配置我觉得没必要，我升级到 17.0 就是避免过多配置」。

批次 10 把它做成了两颗 env/config 旋钮，方向错了两处：①"什么时候需要人在场"只有正在
做事的那一方知道，写死在机器配置里意味着每次切换都要人跑去命令行；②17.0 的卖点是配置
面小，加两颗正好反着走。现在：配置回到 13 颗，能力落在 `web_act(headed=true)` 一扇门上。

三条必须一起成立的规矩，缺一条这颗参数就是假的：

一、**门只有一扇。** `web_read` 收一个内部 `headed` kwarg（因为 web_act 把动作链委托给
它执行），但那颗名字不在它的 wire schema 上 —— 调用方直接给 web_read 传 `headed` 会被
dispatch 当表外名拒掉。那是设计。

二、**可见性是"那台浏览器"的属性，不是"这次请求"的属性。** 第一次把它起来的那次决定
形态，之后的调用复用同一台，所以回执问的是实例自己（`session.launch_note`），不是问
这次的请求；旋钮那份读法会骗人：旋钮后来关掉了，窗口还在屏幕上。

三、**不许用接管或关窗来兑现这句话。** 一台正在跑的 headless 浏览器不会"变成可见"
（用户的 profile 突然出现在屏幕上，那像魔法），也不会被关掉另起一台可见的（扔掉的可能
正是他在用的标签页）。这种情况具名拒绝 `browser_busy`，并把"先把窗口交回去"的那一步
写在回执里。反向不冲突：可见的那台能干后台的活。
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from dhole_mcp import env_registry, identity
from dhole_mcp import browser as B
from dhole_mcp.browser import BrowserBusyError
from dhole_mcp.server import BulkResponseModel, MasterFetchServer, ResponseModel


# ─── 1 · 配置面退回原样 ──────────────────────────────────────────────────────

def test_no_browser_knobs_survived_the_reversal():
    """两颗旋钮整体退掉：注册表里没有名字，设了也不读（不是"文档没写"，是不存在）。"""
    names = {e.name for e in env_registry.ALL}
    assert "DHOLE_BROWSER_HEADED" not in names
    assert "DHOLE_BROWSER_CHANNEL" not in names
    assert len(env_registry.ALL) == 15, (
        f"注册表应当是 15 颗（那两颗浏览器旋钮整体退掉之后，行级垃圾过滤补了一颗；"
        f"多模型档位又补了 DHOLE_RERANK_MODEL），现在 {len(env_registry.ALL)} 颗：{sorted(names)}")
    assert not hasattr(B, "_browser_headed") and not hasattr(B, "_forced_browser_channel"), (
        "读旋钮的那两个函数还活着 —— 名字退了、代码没退，等于留了两扇门")


def test_setting_the_retired_channel_env_changes_nothing(monkeypatch):
    """反向守卫：把旧旋钮名设上，启动判定必须**当作没看见**。

    留着读它的代码就是留着第二扇门：`dhole config set` 拒它，但 env 谁都设得了。
    """
    monkeypatch.setenv("DHOLE_BROWSER_CHANNEL", "msedge")
    monkeypatch.setenv("DHOLE_BROWSER_HEADED", "1")
    assert B._launch_channel(False) == B._detect_chrome_channel()
    assert B.BrowserSession()._headless is True


def test_headed_is_not_a_persistent_state():
    """`headed` 不进 cred_defaults：那是"这台机器一直这样"，而它每次都可能不同。"""
    assert "headed" not in identity.GLOBAL_KEYS


# ─── 2 · 门只有一扇 ─────────────────────────────────────────────────────────

def test_the_public_door_is_web_act_only():
    defs = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}
    act = json.dumps(defs["web_act"], ensure_ascii=False)
    read = json.dumps(defs["web_read"], ensure_ascii=False)
    assert '"headed"' in act, "参数上了 web_act 的 schema —— 没有它这条能力没人能调用"
    assert "headed" not in read and "headless" not in read, (
        "web_read 的 schema 里出现可见性：那扇内部门被开到了外面")


def test_callers_cannot_pass_headed_to_web_read():
    """web_read 收这个 kwarg（web_act 要用），但表外名拒答必须仍然拒得住外部调用方。

    拒答的形态是 dispatch **抛** ValueError（`_reject_unknown_args` 就这么设计的），
    不是返回一条信封 —— 所以判据问的是"有没有被拒"，不是"回执长什么样"。
    """
    with pytest.raises(ValueError) as boom:
        asyncio.run(MasterFetchServer()._dispatch(
            "web_read", {"url": "https://example.test/", "headed": True}))
    msg = str(boom.value)
    assert "headed" in msg, f"拒的不是这颗名字：{msg[:300]}"
    assert "Unsupported argument" in msg
    assert "headed" not in msg.split("Supported:")[1], (
        "白名单把 headed 放行了 —— 那扇内部门就被开到了外面")


def _act_schema_names() -> set:
    """dispatch 认的参数名（`_TOP_LEVEL_ARGS`）—— 不是 schema，两者必须一致。"""
    from dhole_mcp import server as S
    return set(S._TOP_LEVEL_ARGS["web_act"])


def test_headed_is_whitelisted_for_web_act_only():
    """门只有一扇的另一半：白名单里 web_act 有、web_read 没有。

    判据落在 `_TOP_LEVEL_ARGS` 而不是 `_TOOL_DEFS`，因为 dispatch 读的是前者 —— schema
    上写了却没进白名单，参数会被拒；进了白名单却没上 schema，调用方无从得知。两边都查。
    """
    from dhole_mcp import server as S
    assert "headed" in _act_schema_names()
    assert "headed" not in S._TOP_LEVEL_ARGS["web_read"]
    defs = {d["name"]: d for d in S.MasterFetchServer._TOOL_DEFS}
    assert set(defs["web_act"]["inputSchema"]["properties"]) == _act_schema_names(), (
        "web_act 的 schema 与 dispatch 白名单不一致：多出来的名字要么没人能调，"
        "要么调了被静默拒 —— 两边都算说谎")
    assert "headed" not in defs["web_read"]["inputSchema"]["properties"]


# ─── 3 · 这次的请求怎么变成那台浏览器的形态 ──────────────────────────────────

def _fake_lane(monkeypatch):
    """整条 stealthy 车道：起浏览器那一刀与抓取那一刀各记一次 headless。

    两个都要记，因为它们是两个不同的时刻：`_ensure_auto_session` 决定**起**的是哪一台
    （一起来就改不回去了），`bulk_stealthy_fetch` 只是沿用同一个要求。只查后者的话，
    "起了一台 headless 的、回执却说这是可见调用"这种谎抓不住。
    """
    seen = {}

    async def _ensure(self, *, lock_wait=None, profile_name=None, headless=True):
        seen["launched_headless"] = headless
        return "fake-session"

    async def _fake(self, urls, **kwargs):
        seen["headless"] = kwargs.get("headless")
        return BulkResponseModel(
            results=[ResponseModel(url=urls[0], status=200, content=["ok"],
                                   fetcher_used="stealthy")],
            total=1, successful=1)

    monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session", _ensure)
    monkeypatch.setattr(MasterFetchServer, "bulk_stealthy_fetch", _fake)
    return seen


def test_act_headed_true_launches_hidden_false(monkeypatch):
    seen = _fake_lane(monkeypatch)
    asyncio.run(MasterFetchServer().web_act(url="https://example.test/",
                                            actions=[{"snapshot": True}], headed=True))
    assert seen["headless"] is False, "headed=true 到了抓取层又变回 headless —— 白说"
    assert seen["launched_headless"] is False, (
        "起浏览器那一刀还是 headless：抓取层说不要，launcher 照样起了台看不见的，"
        "于是回执讲的是这次**想要**什么，不是屏幕上**有没有**")


def test_act_without_headed_stays_headless(monkeypatch):
    seen = _fake_lane(monkeypatch)
    asyncio.run(MasterFetchServer().web_act(url="https://example.test/",
                                            actions=[{"snapshot": True}]))
    assert seen["headless"] is True, (
        "默认必须还是档位表写死的 headless —— 这批不许顺手改反爬行为")
    assert seen["launched_headless"] is True


def test_a_string_false_is_not_a_window(monkeypatch):
    """字符串客户端的 `headed: "false"` 必须读成**假**。

    按裸值传，非空字符串就是真 —— 于是一次没人要窗口的调用把浏览器开到用户屏幕上。
    这是 `confirm` 一族的老缺陷（"false" 授权过一次 attach），而这一颗授权的是"往
    屏幕上放东西"，代价不比那道门小。判据问 dispatch 之后的**实际**形态，不问参数到没到。
    """
    seen = _fake_lane(monkeypatch)
    asyncio.run(MasterFetchServer()._dispatch(
        "web_act", {"url": "https://example.test/",
                    "actions": [{"snapshot": True}], "headed": "false"}))
    assert seen["launched_headless"] is True, (
        '"false" 被读成真：窗口弹出来了，而调用方说的是不要')
    assert seen["headless"] is True


def test_a_string_true_is_a_window(monkeypatch):
    """反向也要成立：`"true"` 读成真，否则这颗参数只对一半的客户端存在。"""
    seen = {}

    async def _ensure(self, *, lock_wait=None, profile_name=None, headless=True):
        seen["launched_headless"] = headless
        raise BrowserBusyError("browser_busy: stop here - the form is already decided")

    monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session", _ensure)
    out = asyncio.run(MasterFetchServer()._dispatch(
        "web_act", {"url": "https://example.test/",
                    "actions": [{"snapshot": True}], "headed": "true"}))
    assert seen["launched_headless"] is False
    assert out[1]["error"].startswith("browser_busy"), "拒绝没走信封"


def test_headed_is_not_a_cache_or_stealth_rung(monkeypatch):
    """可见性只动 headless 一位：不缓存照旧，档位表其余部分一个字不改。

    两半各有历史事故。①`headed` 若让人顺手带上缓存，窗口里读到的就是别的时刻的页面，
    用户正看着的那个和回执说的不是同一份。②它若进了 `_ANTI_DETECT` 的档位，就等于把
    "看不见"从反爬强度里摘掉了 —— 而 headless 正是那台浏览器存在的理由之一。
    """
    from dhole_mcp import rungs

    seen = {}

    async def _fake_read(self, url, **kwargs):
        seen.update(kwargs)
        return ResponseModel(url=url, status=200, content=["ok"],
                             fetcher_used="stealthy")

    monkeypatch.setattr(MasterFetchServer, "web_read", _fake_read)
    asyncio.run(MasterFetchServer().web_act(url="https://example.test/",
                                            actions=[{"snapshot": True}], headed=True))
    assert seen["headed"] is True, "headed 没传到执行车道 —— 门在，链没接上"
    assert seen["cache_ttl"] == 0, "可见的那台也不能从缓存里读页面 —— 动作要落在真页面上"
    for rung in ("off", "basic", "auto", "aggressive"):
        assert rungs._anti_detect(rung)["headless"] is True, (
            f"档位 {rung} 的 headless 被动过：可见性不该是反爬强度的一档")


# ─── 4 · 复用判据：不对称，且不接管不关窗 ────────────────────────────────────

def _warm(monkeypatch, *, headless, profile=None):
    """一台已经热着的 auto 会话，形态由它自己说。

    那颗假浏览器的画布噪声 / WebRTC 位必须**照当下档位表**摆：能不能复用问的是这两颗
    加上 profile 加上可见与否，三位里任何一位对不上都会走到"另起一台"，而这批的用例
    在离线跑 —— conftest 的 `_nobody_launches_a_browser` 会把它拦成红。
    """
    from dhole_mcp import rungs

    hard = rungs._default_hardening()
    srv = MasterFetchServer()
    session = SimpleNamespace(_is_alive=True, _hide_canvas=hard["hide_canvas"],
                              _block_webrtc=hard["block_webrtc"], _headless=headless)
    srv._sessions = {"warm": SimpleNamespace(session=session, _alive=True)}
    srv._auto_stealthy_id = "warm"
    srv._auto_stealthy_last_used = 0
    srv._auto_stealthy_profile = profile
    monkeypatch.setattr("dhole_mcp.server._browser_deps_available", lambda: True)
    monkeypatch.setattr(MasterFetchServer, "_ensure_idle_monitor", lambda self: None)
    return srv


def test_a_hidden_browser_is_not_taken_over_and_not_closed(monkeypatch):
    srv = _warm(monkeypatch, headless=True)
    closed = []
    monkeypatch.setattr(MasterFetchServer, "_close_one_session",
                        lambda self, sid: closed.append(sid))
    with pytest.raises(BrowserBusyError):
        asyncio.run(srv._ensure_auto_session(headless=False))
    assert closed == [], "为了让这次可见就把别人的浏览器关掉了 —— 扔的是用户正在用的标签页"


def test_a_visible_browser_serves_a_background_call(monkeypatch):
    srv = _warm(monkeypatch, headless=False)
    assert asyncio.run(srv._ensure_auto_session(headless=True)) == "warm"


def test_a_visible_browser_is_reused_by_a_visible_call(monkeypatch):
    srv = _warm(monkeypatch, headless=False)
    assert asyncio.run(srv._ensure_auto_session(headless=False)) == "warm"


def test_headless_behaviour_is_unchanged(monkeypatch):
    """反向：后台请求之间的复用一条都没动（这批不许顺手改反爬行为）。"""
    srv = _warm(monkeypatch, headless=True)
    assert asyncio.run(srv._ensure_auto_session(headless=True)) == "warm"


# ─── 4b · 同一条规则也要管具名会话（2026-10-08 验收 D-22）───────────────────

def _warm_named(monkeypatch, *, headless, sid="qa"):
    """一台已经活着的**具名**会话浏览器。

    具名会话走 `_ensure_session_browser`，不是 `_ensure_auto_session`：那里管的是共享的
    auto 池，而具名会话的浏览器就是那个会话的意义本身（登录态在里面）。批次 11 把可见性
    规则只写进了前者，于是实连测到：隐藏浏览器在场的同一个 session_id 上
    `web_act(headed=true)` 回 200、`metadata.browser` 为空 —— 用户等的那个窗口从来没出现，
    而回执一句话都没说。
    """
    srv = MasterFetchServer()
    session = SimpleNamespace(_is_alive=True, _headless=headless)
    srv._sessions = {sid: SimpleNamespace(session=session, session_type="stealthy",
                                          _alive=True)}
    monkeypatch.setattr("dhole_mcp.server._browser_deps_available", lambda: True)
    return srv, sid


def test_a_named_session_with_a_hidden_browser_refuses_a_visible_call(monkeypatch):
    srv, sid = _warm_named(monkeypatch, headless=True)
    closed = []
    monkeypatch.setattr(MasterFetchServer, "_close_one_session",
                        lambda self, s: closed.append(s))
    with pytest.raises(BrowserBusyError) as boom:
        asyncio.run(srv._ensure_session_browser(sid, headless=False))
    assert closed == [], "为了让这次可见就把会话浏览器关了 —— 里面可能是用户的登录态"
    assert sid in str(boom.value), "拒绝必须点名是哪个会话，否则没人知道要交回哪一台"
    assert "sys_state(clear=" in str(boom.value), "出路要写在同一句里"


def test_a_named_session_with_a_visible_browser_serves_a_hidden_call(monkeypatch):
    """不对称照旧：可见的那台乐意干后台的活。"""
    srv, sid = _warm_named(monkeypatch, headless=False)
    assert asyncio.run(srv._ensure_session_browser(sid, headless=True)) == sid


def test_a_named_visible_session_is_reused_by_a_visible_call(monkeypatch):
    srv, sid = _warm_named(monkeypatch, headless=False)
    assert asyncio.run(srv._ensure_session_browser(sid, headless=False)) == sid


# ─── 5 · 那句话问实例，不问旋钮 ─────────────────────────────────────────────

def test_a_headless_session_says_nothing():
    assert B.BrowserSession()._headless is True
    assert B.BrowserSession().launch_note == "", (
        "headless 是常态，说了就是给每一次抓取加一句废话")


def test_a_visible_session_names_the_profile_it_survives_on():
    s = B.BrowserSession(headless=False, profile_name="main")
    s._persistent_profile = True
    note = s.launch_note
    assert "visible window" in note and "main" in note and "survive" in note
    assert "sys_state" in note, "把窗口交回来的那一步必须写在同一句里"


def test_a_visible_session_on_a_throwaway_dir_says_that():
    note = B.BrowserSession(headless=False).launch_note
    assert "THROWAWAY" in note and "cred_defaults" in note, \
        "只说问题不给出路，等于让调用方猜那颗旋钮叫什么"


class _FakeBrowser:
    """一台"被 dhole 起来的"假浏览器：只回答两件事 —— 我怎么起来的、抓取回来了什么。"""

    def __init__(self, note):
        self.cdp_attached = False
        self.launch_note = note

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def fetch(self, url, **kw):
        return SimpleNamespace(url=url, html="<p>x</p>", status_code=200, title="",
                               headers={}, redirect_url="", response_time=0.01)


def _live_row(monkeypatch, note):
    # 抓取车道里 `StealthyBrowser` 是**函数内**从 dhole_mcp.browser 取的（server 顶层
    # 没有这个名字 —— 浏览器依赖不在的时候导入期就不能炸），所以假件要装在它被取的那
    # 个模块上，装在 server 上只会 AttributeError，或更糟：悄悄加一个没人读的属性。
    monkeypatch.setattr("dhole_mcp.server._browser_deps_available", lambda: True)
    monkeypatch.setattr("dhole_mcp.browser.StealthyBrowser",
                        lambda *a, **k: _FakeBrowser(note))
    monkeypatch.setattr("dhole_mcp.server._translate_response",
                        lambda page, *a, **k: ResponseModel(
                            url=page.url, status=200, content=["ok"],
                            fetcher_used="stealthy"))
    bulk = asyncio.run(MasterFetchServer().bulk_stealthy_fetch(
        urls=["https://example.test/"]))
    return bulk.results[0]


def test_a_live_visible_fetch_stamps_every_row(monkeypatch):
    """回执说**实际跑的那台**，而不是这次想要的那种。"""
    row = _live_row(monkeypatch, "visible window, profile 'main'")
    assert row.metadata["browser"] == "visible window, profile 'main'"


def test_a_headless_fetch_stays_silent(monkeypatch):
    row = _live_row(monkeypatch, "")
    assert "browser" not in (row.metadata or {}), (
        "后台抓取也被盖了一句 visible —— 那句话就不再是事实而是噪音了")


def test_a_cache_hit_does_not_replay_a_window_claim(monkeypatch):
    """真跑一次"抓到 → 存下 → 从缓存读回"，而不是在测试里重写一遍那段字典推导。

    上一版这条是把生产代码的那一行抄进测试再断言它等于自己 —— 那种测试永远绿，而
    存进去的那句 "visible window" 讲的是**上一次抓取时开着的那台机器**，缓存命中时
    它可能早就退了。整份 metadata 一起丢也不行（标题没了）。
    """
    url = "https://example.test/window-claim"
    srv = MasterFetchServer()

    async def _ensure(self, *, lock_wait=None, profile_name=None, headless=True):
        return "fake-session"

    async def _fake(self, urls, **kwargs):
        return BulkResponseModel(
            results=[ResponseModel(url=urls[0], status=200, content=["body text"],
                                   fetcher_used="stealthy",
                                   metadata={"browser": "visible window, profile 'main'",
                                             "title": "the page"})],
            total=1, successful=1)

    monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session", _ensure)
    monkeypatch.setattr(MasterFetchServer, "bulk_stealthy_fetch", _fake)

    first = asyncio.run(srv.web_read(url=url, force_fetcher="stealthy", cache_ttl=600,
                                     ignore_robots=True))
    assert first.metadata.get("browser") == "visible window, profile 'main'", first.metadata
    second = asyncio.run(srv.web_read(url=url, force_fetcher="stealthy", cache_ttl=600,
                                      ignore_robots=True))
    assert second.fetcher_used == "cache", (
        f"第二次没吃到缓存（{second.fetcher_used}），这条就在测别的东西")
    assert "browser" not in (second.metadata or {}), second.metadata
    assert second.metadata.get("title") == "the page", "摘一句话不该把标题也带走"


# ─── 6 · 拒绝走信封，并有自己的词 ───────────────────────────────────────────

def test_browser_busy_travels_the_envelope(monkeypatch):
    from dhole_mcp import security  # noqa: F401  (import parity with the lane)

    async def _boom(*a, **k):
        raise BrowserBusyError("browser_busy: a browser is already running on profile "
                               "'main' and this call asked for a VISIBLE one.")

    monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session",
                        lambda self, **k: _boom())
    out = asyncio.run(MasterFetchServer().web_read(
        url="https://example.test/", force_fetcher="stealthy", cache_ttl=0))
    assert out.status == 0 and out.content == [] and out.content_ok is False
    assert out.error.startswith("browser_busy")
    assert "browser in use" in out.summary, out.summary
    assert "network error" not in out.summary, "没有请求在路上，没什么网络可查"
    assert "clear=" in out.next_action and "headed=true" in out.next_action, out.next_action


def test_a_busy_refusal_fetches_nothing(monkeypatch):
    """拒绝发生在起浏览器之前：一个页面都不该被打开。"""
    def _never(*a, **k):
        raise AssertionError("被 browser_busy 拦住的调用不许起浏览器")

    monkeypatch.setattr(B, "StealthyBrowser", _never)
    monkeypatch.setattr(B, "BrowserSession", _never)
    with pytest.raises(BrowserBusyError):
        asyncio.run(_warm(monkeypatch, headless=True)._ensure_auto_session(headless=False))


# ─── 7 · 空闲回收问实例 ─────────────────────────────────────────────────────

def _reap_once(monkeypatch, *, headless):
    """跑**一轮**空闲回收（会话当作早已过期），返回它关掉的 session id。

    行为判据而不是源码判据：源码守卫只看得见"`_headless` 有没有出现在函数里"，而真正的
    缺陷是把算出来的 `_visible` 从那条 `if` 里忘掉 —— 算而不问，函数里照样有这个字样。
    """
    from dhole_mcp import server as S

    closed: list = []
    srv = _warm(monkeypatch, headless=headless)
    srv._auto_stealthy_last_used = 0          # 远过 AUTO_SESSION_IDLE_TIMEOUT

    async def _close(self, sid):
        closed.append(sid)

    passes: list = []

    async def _one_sleep(_seconds):
        passes.append(1)
        if len(passes) > 1:
            raise asyncio.CancelledError      # 第二圈进 sleep 就收摊：只看一轮

    monkeypatch.setattr(MasterFetchServer, "_close_one_session", _close)
    monkeypatch.setattr(S, "asyncio_sleep", _one_sleep)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(srv._start_idle_monitor())
    return closed


def test_the_reaper_leaves_a_visible_window_alone(monkeypatch):
    assert _reap_once(monkeypatch, headless=False) == [], (
        "屏幕上开着的那台被空闲回收关掉了 —— 人在里面打字，扔掉的是半张登录表单")


def test_the_reaper_still_closes_the_hidden_auto_browser(monkeypatch):
    """反向：不许有人用"保护窗口"当理由把回收整条关掉，那笔代价是每个会话的浏览器。"""
    assert _reap_once(monkeypatch, headless=True) == ["warm"]


def test_the_idle_reaper_asks_the_browser_itself():
    import inspect

    body = inspect.getsource(MasterFetchServer._start_idle_monitor)
    assert "_headless" in body, (
        "回收器又在只读时间了 —— 屏幕上开着的窗口不能因为 5 分钟没流量就被关掉")
    assert "env_registry" not in body and "DHOLE_BROWSER_HEADED" not in body, \
        "它读回旋钮了：旋钮是'这次想要什么'，不是'现在有没有一台'"
    assert "AUTO_SESSION_IDLE_TIMEOUT" in body, "headless 的那台照旧回收"
