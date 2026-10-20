# -*- coding: utf-8 -*-
"""三类等最久的东西必须在自己开始的时候说一句话 —— 静默不是"还在跑"，是"没消息"。

症状（15 场景真人体测，`review/2026-10-10-用户体验模拟报告.md`）：五个 ≥5 秒的静默窗口
（16.1 / 19.6 / 31.5 / **61.5** / 17.4 秒），主观分最低的一格就是"等待时的感受"（2/5）。
`context.report_progress` 早就在了，动作链每一步都发（`actions.py` 的链循环）、爬取每一行
也发（`crawl.py`）—— 缺的是**这三类最慢的东西一次都没发**：

* 档位跃迁 `http → stealthy`：一上去就是"起浏览器 + 渲染"，几秒到几十秒；
* 起浏览器进程：这台机器实测 3–5 秒，首用还要下一次 193MB（分钟级）；
* 等 network-idle 稳定：只在"页面看着还没渲染"时花，帽是 `SETTLE_IDLE_MS`。

判据的形状：

1. 这三处各自在**开始等**的那一刻发一句，话说清在等什么。
2. "还剩多少"只有一个作者：从 `call_deadline` 那颗 ContextVar 现读。某处自己估一个
   数（`30000 - 已用`）就是下一个漂移点，读不到预算时那半句整个**缺席**而不是补一个。
3. 首用那一发下载**不报**剩余：那段时间事后由 `_apply_install_shift` 还给预算，此刻读到
   的 0 会把"我要开始一场几分钟的下载"说成"这次调用已经没时间了"。
4. 送不出去不许弄坏正在跑的抓取：没有会话 → False；会话的 `report_progress` 抛任何
   Exception → 还是 False，异常不向上冒（取消照旧冒，那是另一件事）。
5. 宿主没带 progressToken 时 SDK 自己 no-op，这里不重复判能力（判了就是第二处会写错
   的地方）—— 所以这一族发射在旧宿主上是零成本。
6. 这**一句**阶段话走两条通道（progress 与 logging/message），两家的支持面不重合：只看
   logging 的宿主在 progress 上是全黑的。两条各自 fail-soft，一条坏了不带走另一条；
   而两条必须说同一句话 —— 两句话就是两个版本的事实，读哪条的人都以为自己看见了全部。
   （动作链与爬取那种**可数**的进度仍然只走 progress：那是每步/每页一发，抄成两发是洪水。）
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from dhole_mcp import browser as browser_mod
from dhole_mcp import context as ctx
from dhole_mcp import server as server_mod
from dhole_mcp.context import (
    call_deadline, report_progress, report_stage, set_mcp_session, stage_message)

REPO = Path(__file__).resolve().parents[1]
BROWSER_SRC = (REPO / "src" / "dhole_mcp" / "browser.py").read_text(encoding="utf-8")
SERVER_SRC = (REPO / "src" / "dhole_mcp" / "server.py").read_text(encoding="utf-8")


class _Session:
    def __init__(self, raises=None, log_raises=None):
        self.sent = []
        self.logs = []
        self._raises = raises
        self._log_raises = log_raises

    async def report_progress(self, progress, total=None, message=None):
        if self._raises is not None:
            raise self._raises
        self.sent.append((progress, total, message))

    async def send_log_message(self, level, data, logger=None,
                               related_request_id=None):
        if self._log_raises is not None:
            raise self._log_raises
        self.logs.append((level, data, logger))


@pytest.fixture
def session():
    s = _Session()
    set_mcp_session(s)
    try:
        yield s
    finally:
        set_mcp_session(None)


@pytest.fixture
def clock(monkeypatch):
    now = [1_000.0]
    monkeypatch.setattr(ctx, "time", SimpleNamespace(time=lambda: now[0]))
    return now


# ─── 1 · 那句话怎么拼 ───────────────────────────────────────────

def test_no_budget_in_scope_means_the_remaining_clause_is_absent(clock):
    assert ctx._CALL_DEADLINE.get() is None, "上一条用例漏了摘那颗 ContextVar"
    line = stage_message("starting a browser")
    assert line == "starting a browser", line
    assert "left" not in line, "没人点过预算，那句'还剩多少'是编的"


def test_the_remaining_comes_from_the_calls_own_deadline(clock):
    with call_deadline(30):
        assert stage_message("starting a browser") == (
            "starting a browser | 30000ms left in this call's budget")


def test_spent_is_said_only_when_the_site_knows_it(clock):
    with call_deadline(30):
        assert stage_message("tier jump", 1204.4) == (
            "tier jump | spent 1204ms | 30000ms left in this call's budget")
    assert stage_message("tier jump") == "tier jump", "不知道花了多少就说 0ms 是一种谎"


def test_the_first_use_download_declines_to_quote_a_remaining(clock):
    """`show_left=False` 不是关掉算术，是关掉一次误导（判据 3）。"""
    with call_deadline(0.2):
        assert "left" not in stage_message("downloading chromium", show_left=False)
        # 同一颗时钟下别的句子照报剩余 —— 关掉的是这一处，不是那半句本身
        assert "200ms left" in stage_message("downloading chromium")


# ─── 2 · 送不出去的时候，抓取照跑 ────────────────────────────────

def test_no_session_sends_nothing_and_says_so():
    set_mcp_session(None)
    assert asyncio.run(report_stage("anything")) is False


def test_a_host_that_does_not_want_progress_is_not_re_implemented_here():
    """SDK 的 `report_progress` 自己就是"没带 progressToken 时 no-op"（实测）。

    这一条守的是**这里不多判一遍**：判了就有第二处会写错的地方，而错的方式是"这一格
    今天没人看见"——正是这一刀要消灭的那种静默。查的是 AST 里的名字，不是源码文本 ——
    docstring 本来就该把这件事讲清楚，用文本判会把讲道理的那几行判成违例。
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(report_progress))
    read = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    read |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    banned = {"capabilities", "progressToken", "progress_token", "experimental"}
    assert not (read & banned), f"这里又判了一遍宿主能力：{sorted(read & banned)}"


def test_a_broken_back_channel_costs_one_sentence_not_the_call(session):
    """progress 那一条炸了不许冒到抓取上；另一条照发（判据 4 + 6）。"""
    session._raises = RuntimeError("pipe closed")
    assert asyncio.run(report_stage("starting a browser")) is True
    assert len(session.logs) == 1, session.logs


# ─── 2b · 双通道：一条宿主不看的通道不许成为唯一的出口 ─────────────

def test_the_same_sentence_rides_both_channels(session):
    """progress 与 logging 的支持面不重合：只看 logging 的宿主在 progress 上是全黑的。"""
    assert asyncio.run(report_stage("starting a browser", 1500)) is True
    assert len(session.sent) == 1 and len(session.logs) == 1, (session.sent, session.logs)
    progress_message = session.sent[0][2]
    level, log_data, logger = session.logs[0]
    assert log_data == progress_message, (
        "两条通道说的是两句话：读哪一条的人都以为自己看见了全部")
    assert level == "info" and logger == "dhole.stage", (level, logger)


def test_a_host_without_the_logging_channel_still_gets_progress(session):
    session._log_raises = RuntimeError("no such capability")
    assert asyncio.run(report_stage("waiting for the network to go quiet")) is True
    assert len(session.sent) == 1, session.sent


def test_a_host_without_the_progress_channel_still_gets_the_log_line(session):
    session._raises = RuntimeError("no progressToken")
    assert asyncio.run(report_stage("waiting for the network to go quiet")) is True
    assert len(session.logs) == 1, session.logs


def test_a_cancelled_call_is_not_swallowed_as_a_missed_message(session):
    session._raises = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(report_stage("starting a browser"))


def test_the_stage_line_reaches_the_session(session):
    assert asyncio.run(report_stage("starting a browser", 2500)) is True
    assert len(session.sent) == 1
    progress, total, message = session.sent[0]
    assert progress == 2.5 and total is None, (
        "这类等待没有分母；报一个 total 就是编一个百分比")
    assert message.startswith("starting a browser | spent 2500ms")


# ─── 3 · 接线：三处最慢的等待真的在开始的那一刻发 ──────────────────

def test_the_tier_jump_speaks_before_the_browser_starts():
    # 先把要判的数取成小对象：把整份源码留在断言表达式里，红了会打印一整个模块
    jumps = "escalating to the JS tier" in SERVER_SRC
    emits = SERVER_SRC.count("await report_stage(")
    assert jumps, "http→stealthy 那一跃没发：它是最长的一次静默的入口"
    assert emits >= 2, f"server 侧只剩 {emits} 处发射：首用下载那一发（分钟级）呢？"


def test_the_first_use_install_does_not_quote_a_remaining_it_has_already_spent():
    ok = ("downloading it now" in SERVER_SRC) and ("show_left=False" in SERVER_SRC)
    assert ok, ("那段时间事后由 `_apply_install_shift` 还给预算 —— 当场报剩余会报出一个"
                "假的 0，把'我要开始一场几分钟的下载'说成'这次调用已经没时间了'")


def test_the_browser_process_launch_speaks():
    # 两处静默各自的句子必须是它自己那句 —— 只数发射次数会放过"两处都发同一句话"
    launches = "browser process (a few seconds)" in BROWSER_SRC
    assert launches, "起进程那一句没说出它在等什么（launch_persistent_context 之前）"
    stages = [line for line in BROWSER_SRC.splitlines() if "await report_stage(" in line]
    assert len(stages) >= 2, f"browser 侧只剩 {len(stages)} 处发射：等 network-idle 那一处呢"


def test_the_idle_wait_names_its_own_cap():
    """帽的属主是 `SETTLE_IDLE_MS`，句子里不许再有第二颗 5000。"""
    assert "SETTLE_IDLE_MS}ms for its network" in BROWSER_SRC, (
        "那句等待话把帽写成了字面量 —— 帽改了话不会跟着改，就是下一句谎")


def test_the_emitters_import_the_one_sentence_author():
    """两处发射用的都是 `context` 那一句 —— 没有谁在本地重拼"还剩多少"。

    判的是**那句话的形状只有一处生产**：`left in this call's budget` 这半句出现在别的
    模块源码里，就意味着有人自己抄了一遍算术。
    """
    assert server_mod.report_stage is ctx.report_stage
    assert "from dhole_mcp.context import report_stage" in BROWSER_SRC
    for name, module_src in (("server.py", SERVER_SRC), ("browser.py", BROWSER_SRC)):
        assert "left in this call's budget" not in module_src, (
            f"{name} 自己在拼那半句剩余：又一位作者")


# ─── 4 · 行为：等 network-idle 那处真的发出去了 ───────────────────

class _IdlePage:
    def __init__(self):
        self.states = []

    async def wait_for_load_state(self, state=None, timeout=None):
        self.states.append((state, timeout))

    async def evaluate(self, script, arg=None):
        # `_looks_unrendered` 要的读数：正文 0 字而页上挂着 script —— 判成"还没长出来"
        return {"text": 0, "nodes": 3, "scripts": 1}


def test_the_unrendered_page_wait_announces_itself_before_it_starts(session):
    """这一格是量出来的：`/captcha` 那 61.5 秒里有 5 秒属于这里，而它一句都没发过。"""
    page = _IdlePage()
    holder = browser_mod.BrowserSession.__new__(browser_mod.BrowserSession)
    got = asyncio.run(holder._wait_for_stability(page))
    assert got is True, "这一等没走：夹具的读数不再算'还没渲染'了，用例要跟着换"
    assert ("networkidle", browser_mod.SETTLE_IDLE_MS) in page.states, page.states
    assert [p for p, _t, _m in session.sent] == [0.0], session.sent
    message = session.sent[0][2]
    assert "network to go quiet" in message, message
    # 话先于等待：发的时刻在 `wait_for_load_state` 之前，否则这句话要等静默结束才到
    assert session.sent[0][2].startswith("the page rendered nothing yet"), message


def test_the_rendered_page_stays_silent(session):
    """反面守卫：页面正常渲染时一句都不发 —— 这条通道不是日志洪水。"""
    class _RenderedPage(_IdlePage):
        async def evaluate(self, script, arg=None):
            return {"text": 4_200, "nodes": 120, "scripts": 0}

    page = _RenderedPage()
    holder = browser_mod.BrowserSession.__new__(browser_mod.BrowserSession)
    got = asyncio.run(holder._wait_for_stability(page))
    assert got is False
    assert session.sent == [], session.sent
    assert ("networkidle", browser_mod.SETTLE_IDLE_MS) not in page.states, page.states
