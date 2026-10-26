"""test_actions_3.py —— 由 4 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import json
import re
import pytest
from pathlib import Path
from dhole_mcp import guide
from dhole_mcp.actions import _VALID_KEYS, _validate_actions, action_payload_keys
from dhole_mcp.server import MasterFetchServer
import asyncio
from dhole_mcp import actions as actions_mod
from dhole_mcp.actions import _settle_quiet, action_label, build_page_action
from dhole_mcp.context import set_mcp_session
from dhole_mcp import quality
from dhole_mcp.crawl import _scope_hint


# ============================================================================
# 来源：test_an_agent_that_only_reads_the_wire_can_name_an_actions_payload.py
# ============================================================================
# 只读 `tools/list` 的 agent 能不能拼出一条合法的 action —— 必填载荷键必须住在 wire 上。

WIRE = next(td for td in MasterFetchServer._TOOL_DEFS
            if td["name"] == "web_act")["inputSchema"]["properties"]["actions"]["description"]

# 那张表只有一个作者：`_validate_actions` 的正文（`action_payload_keys` 从它自己推出）。
# 这里原先手抄了一份，而手抄的那份已经在 `read` 上落后一颗（1.1 加的 `property` 不在表里，
# 于是这一整份守卫当时量的是一张不再等于行为的清单 —— 表跟着正文走之后，这类落后不可能
# 再发生，理由钉在 tests/test_the_action_key_table_has_one_author.py）。
PAYLOAD_BY_KEY: dict[str, set[str]] = action_payload_keys()

ALL_KEYS = set().union(*PAYLOAD_BY_KEY.values())

# 寻址用的键：每颗动作都认，因而不是"你漏了它"的那种必填键。
ADDRESSING = {"selector", "index", "expect"}


def _legal_payloads(key: str) -> list[dict]:
    """照着那份表拼出来的合法载荷，字符串与 dict、selector 与 index 各一遍。

    刻意一次给一种寻址：`selector` 与 `index` 同时出现是被拒的（"needs exactly one
    of"），那条裁决本身也有用例守着，这里不许顺手把它绕过去。
    """
    if key == "wait":
        return [{"wait": 1000}, {"wait": "1000"}]
    if key == "snapshot":
        return [{"snapshot": True}]
    if key == "scroll":
        return [{"scroll": 3},
                {"scroll": {"steps": 3, "selector": "#feed", "ms_per_step": 400}}]
    if key == "wait_selector":
        return [{"wait_selector": ".card"},
                {"wait_selector": {"selector": ".card", "count": 2,
                                   "state": "visible", "timeout_ms": 5000}}]
    if key == "press":
        return [{"press": "Enter"},
                {"press": {"key": "Enter", "selector": "#q"}},
                {"press": {"key": "Enter", "index": 1}}]
    if key == "read":
        # `read` 是 A3-b 加的那一颗：名单里多一颗名字，本文件第一条判据就会红，
        # 而它的可选旋钮（`as` / `attribute`）走的是同一套"能过真验证器"的量法。
        return [{"read": "#cap"},
                {"read": {"selector": "#cap", "as": "code"}},
                {"read": {"index": 4, "attribute": "value"}},
                {"read": {"selector": "#cap", "expect": "码", "attribute": "href",
                          "as": "link"}}]
    if key == "drag":
        return [{"drag": {"selector": "#src", "expect": "滑块", "to": "#dst"}},
                {"drag": {"index": 3, "to": {"index": 7, "expect": "落点"}}},
                {"drag": {"from_xy": [10, 20], "to_xy": [30, 40]}}]
    if key == "evaluate":
        return [{"evaluate": "document.title"},
                {"evaluate": {"expr": "1+1", "confirm": True}}]
    if key == "select":
        return [{"select": {"selector": "#country", "option": "CN"}},
                {"select": {"index": 4, "expect": "国家", "option": "中国"}},
                {"select": {"selector": "#top", "option": ["a", "b"]}}]
    if key == "navigate":
        return [{"navigate": "back"}, {"navigate": "forward"}]
    if key == "pdf":
        return [{"pdf": {"save_to": "C:/tmp/out.pdf"}}]
    if key == "upload":
        # files 必须指向这台机器上真实存在的文件——校验在浏览器起来之前就发生
        return [{"upload": {"selector": "#doc", "files": [str(Path(__file__))]}},
                {"upload": {"index": 4, "files": [str(Path(__file__))]}}]
    if key == "dialog":
        return [{"dialog": "accept"},
                {"dialog": {"answer": "dismiss", "prompt_text": "no thanks"}}]
    if key == "tab":
        return [{"tab": "new"}, {"tab": "close"}, {"tab": "list"},
                {"tab": 2}, {"tab": {"open": "https://fixture.test/next"}}]
    if key == "screenshot":
        return [{"screenshot": "#cap"},
                {"screenshot": {"selector": "#cap", "decode_qr": True}},
                {"screenshot": {"index": 4, "expect": "码", "image_type": "jpeg",
                                "quality": 60, "save_to": "C:/tmp/shot.jpg"}},
                {"screenshot": {"full_page": True}}]
    by = []
    for addr in ({"selector": "#a"}, {"index": 2}):
        payload = {**addr, "expect": "登录"}
        if key == "fill":
            payload["text"] = "search terms"
        if key in ("click", "submit"):
            payload["confirm"] = True
        by.append({key: payload})
    if key == "click":
        by.append({"click": {"at": [12, 34]}})
        by.append({"click": {"at": [12, 34], "confirm": True}})
    return by


def _listed_action_names() -> set[str]:
    """`"Keys: click, fill, ... submit - each targets"` → 那 11 个名字。"""
    tail = WIRE.split("Keys:", 1)[1]
    listed = set()
    for token in re.split(r"[|,;]\s*|\s+-\s+", tail):
        token = token.strip().strip(".").rstrip("?!")
        if re.fullmatch(r"[a-z_]+", token):
            listed.add(token)
    return listed


def _keys_the_errors_name() -> dict[str, set[str]]:
    """逐颗动作只给寻址、不给载荷，看验证器报错时**点名要哪一颗键**。

    这不是猜：那串报错就是调用方第一次失败后读到的东西。把它当唯一真源，本刀的判据
    就成了"报错会说的，声明也必须说"，而不是"作者记得写了几颗"。
    """
    out: dict[str, set[str]] = {}
    for name in sorted(_VALID_KEYS):
        try:
            _validate_actions([{name: {"selector": "#x"}}])
        except ValueError as exc:
            wanted: set[str] = set()
            for group in re.findall(r"\{([^{}]*)\}", str(exc)):
                for token in re.split(r"[|,]", group):
                    token = token.strip().strip('"\'').rstrip("?")
                    if re.fullmatch(r"[a-z_][a-z_0-9]*", token) and token not in ADDRESSING:
                        wanted.add(token)
            if wanted:
                out[name] = wanted
    return out


REQUIRED = _keys_the_errors_name()


# ─── 1 · 动作名单：从唯一的那份注册表推，不手抄 ───────────────────

def test_every_registered_action_is_named_on_the_wire():
    """`_VALID_KEYS` 是动作唯一的真源；wire 那份清单必须与它逐颗相等。

    加一颗动作而没上 wire 的调用方会碰上"报错里认得、描述里不认得"的键 —— 那正是本刀
    要修的形状，所以这一条不许写成"包含"，要写成相等。
    """
    listed = _listed_action_names()
    assert listed == set(_VALID_KEYS), (
        f"wire 上的动作清单与 _VALID_KEYS 不是同一份：只在 wire "
        f"{sorted(listed - _VALID_KEYS)} / 只在注册表 {sorted(_VALID_KEYS - listed)}")


def test_the_listing_mechanism_actually_notices_a_new_action():
    """机制自检：注册表多一颗而清单没跟上时，上面那条判据是真的会红。"""
    listed = _listed_action_names()
    assert "teleport" not in listed
    assert listed != (_VALID_KEYS | {"teleport"}), "名单比对是摆设"


# ─── 2 · 必填载荷键：从验证器的报错推出来，再要求 wire 说得出 ──────

def test_the_probe_actually_probes():
    """机制自检：报错点名必填键这件事今天真实存在，且点名的是这五颗。

    若验证器改了措辞不再在花括号组里列必填键，`REQUIRED` 会空掉，下面两条判据就此变成
    摆设 —— 所以这里先钉住"探针有效"，再钉住 wire。upload/dialog 是 2026-10-24 加的
    两颗，报错各自点名 files / answer。
    """
    # tab 不在这张表里：它的拒绝句把形状写在引号里（"open": "https://..."），
    # 探针的裸键名花括号抓不到它——形状由 `_legal_payloads` 与手册守卫管。
    # navigate 同理（它的拒绝句也不在花括号里列键名，值就是全部载荷）。
    # select / pdf 是 2026-10-25 加的两颗（对标 playwright-mcp 的
    # select_option / pdf_save），报错各自点名 option / save_to。
    assert REQUIRED == {"drag": {"to"}, "fill": {"text"}, "press": {"key"},
                        "upload": {"files"}, "dialog": {"answer"},
                        "select": {"option"}, "pdf": {"save_to"}}, REQUIRED
    for name in REQUIRED.values():
        assert name <= ALL_KEYS, f"报错点名的 {sorted(name)} 不在手抄表里 —— 表该更新了"


def _named(word: str) -> str:
    r"""这颗键名**作为键名**出现在文本里的正则。

    连字符也要排除：`drag needs a source and a to-target` 那种措辞里的 `to` 是个形容性
    复合词，不是 `{"to": ...}` 的那个键 —— HEAD 的清单就是这么写的，而只按词边界匹配的
    判据会把这句当成说了。
    """
    return rf"(?<![\w.-]){word}(?![-\w])"


@pytest.mark.parametrize("action,keys", sorted(REQUIRED.items()))
def test_every_key_the_error_would_have_to_tell_you_is_already_in_the_declaration(action, keys):
    """报错会说、声明不说的键，就是要求调用方先失败一次才知道的信息。

    这一条是整刀的正面对治：`text` 与 `key` 从前只活在 `must be {selector, text}` 那句
    报错里，而 `tools/list` 的清单只写了动作名。
    """
    for key in keys:
        assert re.search(_named(key), WIRE), (
            f"{action} 缺 {key!r} 时验证器会点名它，但 wire 的声明里没有它")


def test_the_three_pairs_are_said_as_pairs_not_as_a_vocabulary():
    """光出现 `text` 不够，得与它的动作连起来 —— 否则等于换了个地方猜。

    判据量的是"离得够近、中间不许有别的动作名"，不是某一句措辞：`fill needs text`、
    `fill = target + text`、`{fill:{selector,text}}` 都算说过，把九个键名堆成一句
    "以下键名可能出现" 则不算 —— 那种清单不携带路由信息，正是本刀要修的错。
    """
    others = set(_VALID_KEYS)
    for action, keys in sorted(REQUIRED.items()):
        for key in sorted(keys):
            windows = [WIRE[m.start():m.end() + 25]
                       for m in re.finditer(rf"\b{action}\b", WIRE)]
            ok = False
            for window in windows:
                after = window[len(action):]
                hit = re.search(_named(key), after)
                if not hit:
                    continue
                between = after[:hit.start()]
                # 中间不许隔着另一颗动作名（那说明这句话在讲别的动作）。
                if not any(re.search(rf"\b{o}\b", between) for o in others - {action, key}):
                    ok = True
                    break
            assert ok, f"{action} 与 {key} 没被连起来说过：{WIRE}"


# ─── 3 · 那份手抄完整表的三个半边 ────────────────────────────────

@pytest.mark.parametrize("key", sorted(PAYLOAD_BY_KEY))
def test_the_keys_the_hand_copy_names_build_payloads_the_validator_takes(key):
    """半边一：照着那份表拼出来的载荷，真验证器必须收。

    这条不是形式：`wait` 收数字串、`scroll.ms_per_step` 与 `wait_selector.timeout_ms`
    各有归一化路径，而 `screenshot` 的 `full_page` 与目标互斥 —— 键名或形状一改，
    这里先红，而不是等下一次真浏览器跑起来。
    """
    for payload in _legal_payloads(key):
        assert len(_validate_actions([payload])) == 1, payload


def test_the_wire_names_no_key_the_validator_does_not_read():
    """半边二：wire 上以**键名身份**出现的 token，必须全是验证器真读的键或动作名。

    前两半只保证"该有的都有"。`{"fill": {..., "value": ...}}` 那种猜错如果被人顺手
    抄进清单（或者将来改键名只改了描述），这一条拦住。键名身份只有两处：例子花括号里
    的 `name:`，和 `X needs Y` 的两侧 —— 散文里的 "object:" 不是键名，不该混进来。
    """
    example = re.search(r"\[[^\]]*\]", WIRE).group(0)
    for token in re.findall(r'([a-z_][a-z_0-9]*)\s*:', example):
        assert token in _VALID_KEYS, f"例子把 {token!r} 放在键名位，但它不是动作名：{example}"
    for action, key in re.findall(r"([a-z_][a-z_0-9]*) needs ([a-z_][a-z_0-9]*)", WIRE):
        assert action in _VALID_KEYS, f"{action!r} 不是注册过的动作"
        assert key in ALL_KEYS, f"{action} needs {key} —— {key!r} 不在验证器读的键名里"
    assert "value" not in WIRE, "把猜错的那颗键写进声明，等于把错教给所有人"


def test_the_full_action_table_is_in_a_volume_the_guide_serves():
    """半边三：没上 wire 的那些可选旋钮，得在 `sys_guide` 真送得出的那一卷里。

    这是"搬家，不是缩写"的第二半 —— 常驻层短了，知识不能同时消失。只钉"在磁盘上"
    也不够（`test_wire_moves_land_in_the_manual` 的那一半账）：topic 表指错文件，
    知识就在读者拿不到的地方。
    """
    _header, behavior = guide.read("behavior")
    for name in sorted(ALL_KEYS):
        assert re.search(rf"(?<![\w.]){name}(?![\w])", behavior), (
            f"{name!r} 不在 sys_guide(topic='behavior') 送出去的那一卷里")


# ─── 4 · wire 自己给的那条写法要合法 ─────────────────────────────

def test_the_example_printed_on_the_wire_is_a_chain_the_validator_takes():
    """`[{scroll:3},{wait:1000}]` 是描述里唯一的具体形状，它必须真能过。

    占位符式的示例（`{...}`）在这个位置上一文不值；喂真验证器才说得出"照抄可用"。
    """
    raw = re.search(r"\[[^\]]*\]", WIRE)
    assert raw, f"清单里没有一条可照抄的例子：{WIRE}"
    chain = json.loads(re.sub(r'([{\[,])([a-z_][a-z_0-9]*):', r'\1"\2":', raw.group(0)))
    assert len(_validate_actions(chain)) == len(chain), chain


def test_the_table_carries_no_placeholder():
    """清单里不许留占位符（"..." / TODO）—— 那等于把猜错的地方从键名搬到值。"""
    assert "..." not in WIRE and "TODO" not in WIRE, WIRE


# ============================================================================
# 来源：test_actions_hover_drag.py
# ============================================================================
# hover / drag 两个动作：寻址语法与 click 同族，收尾等的是页内安静。

# ─── 校验：什么形状被接受，什么被当场拒绝 ─────────────────────────────

class TestHoverShapes:
    def test_a_bare_selector_is_the_shorthand(self):
        assert _validate_actions([{"hover": ".menu > li"}]) == [
            {"hover": {"selector": ".menu > li"}}]

    def test_snapshot_index_and_expect_work_like_click(self):
        spec = _validate_actions(
            [{"hover": {"index": 12, "expect": "文件"}}])[0]["hover"]
        assert spec == {"index": 12, "expect": "文件"}

    def test_a_bare_row_number_hovers_that_row(self):
        """刀9-6c（F8）：`{"hover": 12}` 从前是一句拒绝，现在就是 `{"index": 12}`。

        这一格原本钉的是"裸数字不是合法形状"，而它钉住的正是用户连撞四次的那一类
        （2026-10-11 全真模拟 S10："又格式错了？"）—— 所以它按新语义改口，不是删掉。
        """
        assert _validate_actions([{"hover": 12}]) == [{"hover": {"index": 12}}]
        assert _validate_actions([{"hover": 0}]) == [{"hover": {"index": 0}}]

    @pytest.mark.parametrize("bad", [
        {"hover": "   "},
        {"hover": {}},
        {"hover": {"selector": "#a", "index": 1}},
        {"hover": {"index": 1, "expect": 9}},
        {"hover": 12.5},
        {"hover": True},
    ])
    def test_a_hover_that_cannot_be_addressed_is_refused_up_front(self, bad):
        # 一个跑不起来的动作值一整个浏览器调用的时间（校验在前，浏览器冷启动在后）。
        with pytest.raises(ValueError):
            _validate_actions([bad])


class TestDragShapes:
    def test_two_css_endpoints(self):
        spec = _validate_actions(
            [{"drag": {"selector": "#src", "to": "#dst"}}])[0]["drag"]
        assert spec == {"selector": "#src", "to": {"selector": "#dst"}}

    def test_both_ends_can_be_snapshot_indexes(self):
        spec = _validate_actions(
            [{"drag": {"index": 3, "expect": "滑块", "to": {"index": 7}}}])[0]["drag"]
        assert spec == {"index": 3, "expect": "滑块",
                        "to": {"index": 7}}

    def test_the_to_side_accepts_a_bare_selector_string(self):
        spec = _validate_actions(
            [{"drag": {"index": 3, "to": ".trash"}}])[0]["drag"]
        assert spec["to"] == {"selector": ".trash"}

    def test_the_to_side_accepts_a_bare_row_number(self):
        """两端同一套寻址语法（上面那句注释就是这么写的），落点也接裸行号。"""
        spec = _validate_actions(
            [{"drag": {"index": 3, "to": 7}}])[0]["drag"]
        assert spec == {"index": 3, "to": {"index": 7}}, spec

    @pytest.mark.parametrize("bad", [
        {"drag": "#src"},                       # 没有落点 = 没意义，不是简写
        {"drag": {"selector": "#src"}},          # 同上，dict 形态也不放行
        {"drag": {"to": "#dst"}},                # 没有源
        {"drag": {"selector": "#a", "to": {}}},
        {"drag": {"selector": "#a", "to": 4.5}},
        {"drag": 12},                            # 一个裸数说不清是哪一端，drag 不设简写
    ])
    def test_a_drag_that_cannot_be_run_is_refused_up_front(self, bad):
        with pytest.raises(ValueError):
            _validate_actions([bad])

    def test_drag_is_in_the_valid_key_announcement(self):
        """报错清单是 agent 的自我纠错入口：新键必须在里面。"""
        with pytest.raises(ValueError, match="hover"):
            _validate_actions([{"nope": True}])


# ─── 执行器：伪造 page，记录交互调用 ──────────────────────────────────

class _FakeLocator:
    def __init__(self, page, selector, log):
        self._page, self._sel, self._log = page, selector, log

    @property
    def first(self):
        # `_target_locator` 对选择器寻址走 `page.locator(sel).first`。
        return self

    async def hover(self, **kw):
        self._log.append(("hover", self._sel, kw.get("timeout")))

    async def drag_to(self, target, **kw):
        self._log.append(("drag_to", self._sel, target._sel, kw.get("timeout")))


class _FakePage:
    """locator() 按选择器发伪定位器；DOM 快照恒定 → 收尾立即判安静。"""

    def __init__(self, log):
        self._log = log

    def locator(self, selector):
        return _FakeLocator(self, selector, self._log)

    async def wait_for_timeout(self, ms):
        pass

    async def evaluate(self, *_a, **_kw):
        return ["stable"]


@pytest.fixture
def _fast_settle(monkeypatch):
    """收尾轮询是真实计时器；单测里把预算缩到两拍以内。"""
    monkeypatch.setattr(actions_mod, "QUIET_SETTLE_MS", 50)
    monkeypatch.setattr(actions_mod, "QUIET_SETTLE_MIN_MS", 0)


def _run(page, actions):
    pa = build_page_action(actions)
    return asyncio.run(pa(page)), pa.outcomes


class TestExecution:
    def test_hover_resolves_the_selector_and_settles(self, _fast_settle):
        log: list = []
        outcomes, rows = _run(_FakePage(log), [{"hover": ".menu > li"}])
        assert log == [("hover", ".menu > li", 10_000)]
        assert rows == [{"action": "hover '.menu > li'", "ok": True}]

    def test_hover_by_snapshot_index(self, monkeypatch, _fast_settle):
        log: list = []
        page = _FakePage(log)
        idx_loc = _FakeLocator(page, "resolved-row-12", log)

        async def fake_resolve(p, i):
            assert (p, i) == (page, 12)
            return idx_loc

        monkeypatch.setattr(actions_mod, "resolve_index", fake_resolve)
        outcomes, rows = _run(page, [{"hover": {"index": 12}}])
        assert log == [("hover", "resolved-row-12", 10_000)]
        assert rows[0]["ok"] is True

    def test_drag_records_both_endpoints_in_order(self, _fast_settle):
        log: list = []
        outcomes, rows = _run(
            _FakePage(log),
            [{"drag": {"selector": "#card-1", "to": "#card-3"}}])
        assert log == [("drag_to", "#card-1", "#card-3", 10_000)]
        assert rows == [{"action": "drag '#card-1' -> '#card-3'", "ok": True}]

    def test_drag_by_indexes_with_expect_on_the_source(self, monkeypatch, _fast_settle):
        log: list = []
        page = _FakePage(log)

        async def fake_resolve(p, i):
            return _FakeLocator(p, f"row-{i}", log)

        async def fake_expect(p, i, expect):
            assert (i, expect) == (3, "滑块")

        monkeypatch.setattr(actions_mod, "resolve_index", fake_resolve)
        monkeypatch.setattr(actions_mod, "_check_expect", fake_expect)
        outcomes, rows = _run(
            page, [{"drag": {"index": 3, "expect": "滑块", "to": {"index": 7}}}])
        assert log == [("drag_to", "row-3", "row-7", 10_000)]
        assert rows[0]["ok"] is True

    def test_a_failed_drag_is_a_row_not_a_crash(self, _fast_settle):
        """一条失败的动作不.abort 整条链——收执行说清哪端没找到。"""

        class _Boom(_FakeLocator):
            async def drag_to(self, target, **kw):
                raise TimeoutError("waiting for locator")

        class _DeadPage(_FakePage):
            def locator(self, selector):
                return _Boom(self, selector, self._log)

        log: list = []
        _, rows = _run(_DeadPage(log),
                       [{"drag": {"selector": "#a", "to": "#b"}}])
        assert rows[0]["ok"] is False
        assert "waiting for locator" in rows[0]["error"]


class TestReceiptLabels:
    def test_hover_label_names_the_target(self):
        assert action_label({"hover": {"selector": ".x"}}) == "hover '.x'"
        assert action_label({"hover": {"index": 5}}) == "hover [5]"

    def test_drag_label_names_both_ends(self):
        a = _validate_actions(
            [{"drag": {"index": 3, "to": {"selector": ".trash"}}}])[0]
        assert action_label(a) == "drag [3] -> '.trash'"


class _FakeClock:
    """替身时钟：每次 monotonic() 推进一拍。actions.time 是全局 time 模块，
    直接 monkeypatch 它会让替身调到自己（递归爆炸），所以换整个对象。"""

    def __init__(self):
        self.t = 0.0

    def monotonic(self) -> float:
        self.t += 0.4
        return self.t


class TestQuietSettle:
    def test_settle_exits_after_two_quiet_polls(self, monkeypatch):
        """安静两拍（且过了最短时限）就出来，不等满预算。"""
        monkeypatch.setattr(actions_mod, "QUIET_SETTLE_MS", 10_000)
        monkeypatch.setattr(actions_mod, "QUIET_SETTLE_MIN_MS", 0)
        monkeypatch.setattr(actions_mod, "time", _FakeClock())
        polls = {"n": 0}

        class _P:
            async def wait_for_timeout(self, ms):
                polls["n"] += 1

            async def evaluate(self, *_a, **_kw):
                return ["stable"]  # DOM 恒定 = 安静

        asyncio.run(_settle_quiet(_P()))
        assert polls["n"] == 2  # 两拍安静即出，没有等满 10 秒

    def test_settle_keeps_waiting_while_the_dom_grows(self, monkeypatch):
        """菜单还在展开（DOM 一直变）就继续等，直到预算到顶。"""
        monkeypatch.setattr(actions_mod, "QUIET_SETTLE_MS", 2_000)
        monkeypatch.setattr(actions_mod, "time", _FakeClock())
        polls = {"n": 0}

        class _P:
            async def wait_for_timeout(self, ms):
                polls["n"] += 1

            async def evaluate(self, *_a, **_kw):
                polls["n"] += 1
                return ["growing", polls["n"]]  # 每拍都在变 = 菜单还没展开完

        asyncio.run(_settle_quiet(_P()))
        assert polls["n"] > 2  # 越过了"两拍安静"的出口，预算到顶才出来


# ============================================================================
# 来源：test_a_click_that_would_submit_pauses_for_approval.py
# ============================================================================
# 落在提交型控件上的 click 必须过 submit 的闸（评审R1刀1）。
# 重名消歧：_FakeLocator → _FakeLocator__a_click_that_would_submi_abfe、_FakePage → _FakePage__a_click_that_would_submi_abfe

class _FakeElement:
    def __init__(self, name="登录", kind=None, probe="ok"):
        self._name = name
        self.kind = kind      # _SUBMIT_CONTROL_JS 的应答；None = 不是提交型控件
        self.probe = probe    # "boom" = 探测调用抛错（等得到元素，读不出类型）
        self.clicked: list[str] = []

    async def click(self, timeout=None):
        self.clicked.append(self._name)

    async def evaluate(self, js):
        if "el.form" in js:  # _SUBMIT_CONTROL_JS 的判别子（_NAME_JS 不含它）
            if self.probe == "boom":
                raise RuntimeError("evaluate unavailable")
            return self.kind
        return self._name

    async def wait_for(self, **kw):
        pass


class _FakeLocator__a_click_that_would_submi_abfe:
    def __init__(self, el):
        self._el = el

    async def click(self, timeout=None):
        await self._el.click()

    async def evaluate(self, js):
        return await self._el.evaluate(js)

    async def wait_for(self, **kw):
        await self._el.wait_for(**kw)


class _FakeRequest:
    def __init__(self, method="POST", resource_type="document"):
        self.method = method
        self.resource_type = resource_type


class _FakePage__a_click_that_would_submi_abfe:
    """一页能听 request 的最小事理（D-29）：`submitted` 现在只认从这里发出去的请求。"""

    def __init__(self):
        self.url = "https://shop.example.com/checkout"
        self._listeners: list = []

    def on(self, event, handler):
        if event == "request":
            self._listeners.append(handler)

    def remove_listener(self, event, handler):
        if handler in self._listeners:
            self._listeners.remove(handler)

    def sent(self, request=None) -> None:
        for handler in list(self._listeners):
            handler(request or _FakeRequest())


class _StubSession:
    """SDK ServerSession 的最小桩（与批次8 刀1 的测试同一形状）。"""

    def __init__(self, capable=True, action="accept", fail=False):
        self._capable = capable
        self._action = action
        self._fail = fail
        self.asked: list[str] = []

    def check_client_capability(self, caps):
        return self._capable and bool(getattr(caps, "elicitation", None))

    async def elicit_form(self, message, requested_schema):
        self.asked.append(message)
        if self._fail:
            from mcp.shared.exceptions import NoBackChannelError
            raise NoBackChannelError("elicitation/create")
        from mcp.types import ElicitResult
        return ElicitResult(action=self._action)


class TestAClickThatWouldSubmit:

    @staticmethod
    async def _run(monkeypatch, actions, **el_kwargs):
        el = _FakeElement(**el_kwargs)
        page = _FakePage__a_click_that_would_submi_abfe()

        async def fake_locator(page, target):
            return _FakeLocator__a_click_that_would_submi_abfe(el)

        async def fake_settle(page, loc):
            await loc.click()
            if el.kind == "submit":
                page.sent()

        monkeypatch.setattr("dhole_mcp.actions._target_locator", fake_locator)
        monkeypatch.setattr("dhole_mcp.actions._settle_after_click", fake_settle)
        pa = build_page_action(actions)
        await pa(page)
        return pa, el

    @pytest.mark.asyncio
    async def test_an_unconfirmed_click_on_a_submit_control_pauses(
            self, monkeypatch):
        """与 {"submit": 2} 同一目标、同一后果 → 同一道闸：暂停，不点。"""
        pa, el = await self._run(monkeypatch, [{"click": {"index": 2}}],
                                 kind="submit")
        (row,) = pa.outcomes
        assert row["ok"] is False and row.get("pending_approval") is True
        assert "Nothing was submitted" in row["error"]
        assert el.clicked == [], "闸在点击之前：表单一个字节都没发"

    @pytest.mark.asyncio
    async def test_a_confirmed_click_on_a_submit_control_clicks(
            self, monkeypatch):
        set_mcp_session(None)
        pa, el = await self._run(
            monkeypatch, [{"click": {"index": 2, "confirm": True}}], kind="submit")
        (row,) = pa.outcomes
        assert row["ok"] is True and el.clicked
        assert row.get("submitted") is True, "这条链里没有 submit 动作，提交事实要落回执"

    @pytest.mark.asyncio
    async def test_a_string_selector_click_pauses_and_teaches_the_rewrite(
            self, monkeypatch):
        """字符串简写不带 confirm 键：暂停行教它换成 dict 写法。"""
        pa, el = await self._run(monkeypatch, [{"click": "input[type=submit]"}],
                                 kind="submit")
        (row,) = pa.outcomes
        assert row.get("pending_approval") is True and el.clicked == []
        assert "confirm" in row["error"] and "click" in row["error"]

    @pytest.mark.asyncio
    async def test_a_plain_control_is_never_gated(self, monkeypatch):
        """探测答"不是提交型" → click 一字不改。闸不发明。"""
        pa, el = await self._run(monkeypatch, [{"click": {"index": 2}}], kind=None)
        (row,) = pa.outcomes
        assert row["ok"] is True and el.clicked
        assert "pending_approval" not in row and "submitted" not in row

    @pytest.mark.asyncio
    async def test_a_probe_that_cannot_read_the_element_invents_no_gate(
            self, monkeypatch):
        """探测本身炸了（等得到元素、读不出类型）→ 不虚构关卡，click 照旧。"""
        pa, el = await self._run(monkeypatch, [{"click": {"index": 2}}],
                                 kind="submit", probe="boom")
        (row,) = pa.outcomes
        assert row["ok"] is True and el.clicked
        assert "pending_approval" not in row


class TestClickConfirmValidation:

    def test_confirm_rides_the_click_when_true(self):
        (a,) = _validate_actions([{"click": {"index": 2, "confirm": True}}])
        assert a == {"click": {"index": 2, "confirm": True}}

    def test_confirm_absent_or_false_keeps_the_shape_bit_identical(self):
        (a,) = _validate_actions([{"click": {"index": 2}}])
        assert a == {"click": {"index": 2}}
        (b,) = _validate_actions([{"click": {"selector": "#go", "confirm": False}}])
        assert b == {"click": {"selector": "#go"}}

    def test_confirm_must_be_a_boolean(self):
        with pytest.raises(ValueError):
            _validate_actions([{"click": {"index": 2, "confirm": "yes"}}])


class TestTheGateMatchesTheSubmitGate:

    @staticmethod
    async def _run(monkeypatch, actions, **el_kwargs):
        return await TestAClickThatWouldSubmit._run(monkeypatch, actions,
                                                    **el_kwargs)

    @pytest.fixture
    def connected(self, request):
        def _attach(stub):
            set_mcp_session(stub)
            request.addfinalizer(lambda: set_mcp_session(None))
            return stub
        return _attach

    @pytest.mark.asyncio
    async def test_a_human_accept_via_elicitation_clicks_without_a_param(
            self, monkeypatch, connected):
        """能力为真的连接：钥匙来自人按键——click 自己没带 confirm。"""
        stub = connected(_StubSession(action="accept"))
        pa, el = await self._run(monkeypatch, [{"click": {"index": 2}}],
                                 kind="submit")
        assert pa.outcomes[0]["ok"] is True and el.clicked
        assert "submission" in stub.asked[0].lower()

    @pytest.mark.asyncio
    async def test_a_decline_or_dead_channel_pauses_even_with_confirm(
            self, monkeypatch, connected):
        for stub in (_StubSession(action="decline"), _StubSession(fail=True)):
            connected(stub)
            pa, el = await self._run(
                monkeypatch, [{"click": {"index": 2, "confirm": True}}],
                kind="submit")
            row = pa.outcomes[0]
            assert row["ok"] is False and row.get("pending_approval") is True
            assert el.clicked == [], "fail-closed：confirm=true 不能替人点头"
            assert "skips the ask" in row["error"]


class TestTheAuditSeesIt:

    @pytest.mark.asyncio
    async def test_a_paused_click_gated_submission_is_audited_as_pending(
            self, tmp_path, monkeypatch):
        import json as _json

        log = tmp_path / "usage.jsonl"
        monkeypatch.setenv("DHOLE_USAGE_LOG", str(log))
        pa, el = await TestAClickThatWouldSubmit._run(
            monkeypatch, [{"click": {"index": 2}}], kind="submit")
        from dhole_mcp.models import ResponseModel
        from dhole_mcp.server import _report_action_outcomes
        result = ResponseModel(url="https://shop.example.com/checkout",
                               status=200, content=["body"],
                               fetcher_used="http")
        _report_action_outcomes(result, pa)
        rows = [_json.loads(line) for line
                in log.read_text(encoding="utf-8").splitlines()]
        (write,) = [r for r in rows if r.get("event") == "write"]
        assert write["pending"] is True
        assert "shop.example.com" in write["url"]


# ============================================================================
# 来源：test_a_display_layer_says_what_it_means.py
# ============================================================================
# R3刀3（展示批量）：读得到 ≠ 读得懂 —— 五处各自把话说准。

class TestTheTopicList:

    def test_a_topic_without_an_alias_does_not_claim_one(self):
        text = guide.index()
        assert "(aka None)" not in text, "别名表查反了方向，缺省被打成了别名"
        # 表头说"别名在括号里"，那有别名的那一卷就得真的带着括号
        assert "(aka 'skill')" in text, text

    def test_a_section_miss_lists_its_headings_once(self):
        topic = next(iter(guide.TOPICS))
        _header, body = guide.read(topic=topic, section="zzz-no-such-heading")
        headings = [line[2:] for line in body.splitlines() if line.startswith("- ")]
        assert headings, body
        tail = body.split("Headings in this volume:")[-1]
        for h in headings:
            assert tail.count(h) == 1, f"{h!r} 列了 {tail.count(h)} 遍"


class TestParameterRefusalsCarryARepair:
    """一颗显式点名的参数错了，就要说怎么改 —— 一个引擎都没问，重来是免费的。"""

    @pytest.mark.asyncio
    async def test_an_unknown_engine_names_the_aliases(self):
        out = await MasterFetchServer().web_search("widgets", engines=["nope"])
        assert "Invalid engine" in out.error, out.error
        assert "Nothing was searched" in out.next_action, out.next_action
        assert "360" in out.next_action and "ddg" in out.next_action, out.next_action

    @pytest.mark.asyncio
    async def test_a_bad_location_says_what_it_takes(self):
        out = await MasterFetchServer().web_search("widgets", location="worldwide")
        assert "Invalid location" in out.error, out.error
        assert "ISO country code" in out.next_action, out.next_action

    @pytest.mark.asyncio
    async def test_a_bad_page_says_pages_count_from_zero(self):
        out = await MasterFetchServer().web_search("widgets", page=99)
        assert "Invalid page" in out.error, out.error
        assert "page 0 IS the first page" in out.next_action, out.next_action

    @pytest.mark.asyncio
    async def test_a_bad_date_says_the_accepted_shapes(self):
        out = await MasterFetchServer().web_search("widgets", after="last tuesday")
        assert out.error, "unreadable date must not be dropped silently"
        assert "ISO date" in out.next_action, out.next_action

    @pytest.mark.asyncio
    async def test_no_engine_was_asked_for_any_of_them(self, monkeypatch):
        """这些拒绝的价值全在"一个包都没发"：真去问了引擎，错误就变成一次开销。"""
        from dhole_mcp import search as search_mod

        called = []

        async def spy(*a, **k):
            called.append(a)
            return ([], [])

        monkeypatch.setattr(search_mod, "multi_search", spy)
        for kwargs in ({"engines": ["nope"]}, {"location": "worldwide"}, {"page": 99}):
            out = await MasterFetchServer().web_search("widgets", **kwargs)
            assert out.results == []
        assert called == [], f"参数拒绝之后还是去问了引擎: {called}"


class TestTheClosedDoorHasAKnob:

    @pytest.mark.asyncio
    async def test_setting_allow_private_false_shows_the_way_off(self):
        out = await MasterFetchServer().cred_defaults(set={"allow_private": False})
        assert "allow_private" in out.error, out.error
        assert 'unset=["allow_private"]' in out.error, out.error

    @pytest.mark.asyncio
    async def test_an_unparseable_value_does_not_get_the_off_hint(self):
        """`"banana"` 不是"想关门"，是拼错：那句出路只给真正的假值。"""
        out = await MasterFetchServer().cred_defaults(set={"allow_private": "banana"})
        assert "unset=" not in out.error, out.error


class TestTheScopeSuggestionFollowsTheQueue:

    def test_a_real_subtree_majority_is_named_with_its_share(self):
        queue = [f"https://s.test/blog/post-{i}" for i in range(6)] + \
                ["https://s.test/about", "https://s.test/contact"]
        hint = _scope_hint(queue)
        assert "path_include=['/blog']" in hint, hint
        assert "6 of the 8" in hint, hint

    def test_a_site_without_a_dominant_subtree_is_not_offered_a_fake_one(self):
        queue = [f"https://s.test/{name}" for name in
                 ("about", "pricing", "careers", "press")]
        assert _scope_hint(queue) == ""

    def test_a_queue_of_bare_origins_cannot_invent_a_subtree(self):
        assert _scope_hint(["https://s.test/", "https://s.test/"]) == ""

    def test_a_thin_majority_is_not_worth_recommending(self):
        """/blog 只有 2 条而队列有 20 条：推荐它省不下预算，只会误导。"""
        queue = [f"https://s.test/a{i}" for i in range(18)] + \
                ["https://s.test/blog/1", "https://s.test/blog/2"]
        assert _scope_hint(queue) == ""


class TestTheShapesUseOnePairOfBraces:
    """`{{` 在非 f 字面量里就是两个字符 —— 给 agent 看的形状示例不能长这样。"""

    @pytest.mark.parametrize("action,expected", [
        ({"drag": "just-a-selector"}, "{selector|index, to}"),
        ({"snapshot": False}, '{"snapshot": true}'),
        ({"submit": "form"}, "{selector, confirm?}"),
        ({"fill": "hello"}, "{selector, text}"),
        ({"wait_selector": 5}, "{selector, count, state, timeout_ms}"),
    ])
    def test_each_refusal_shows_the_shape_it_means(self, action, expected):
        with pytest.raises(ValueError) as exc:
            build_page_action([action])
        msg = str(exc.value)
        assert expected in msg, msg
        assert "{{" not in msg, msg

    @pytest.mark.asyncio
    async def test_the_credential_shape_hint_is_not_escaped_either(self):
        """cred_defaults 的 cookies 拒绝同属这一类：`{{name: value}}` 不是形状。"""
        out = await MasterFetchServer().cred_defaults(
            set={"cookies": "not-a-cookie-shape", "host": "example.com"})
        assert "cookies" in out.error, out.error
        assert "{name: value}" in out.error, out.error
        assert "{{" not in out.error, out.error


class TestTheRejectionSummaryBreaksAtAWord:
    """`msg[:80]` 把拒绝句的 summary 剪成 "…can compile (S" —— 半截 token 看着像坏了的字符串。

    整句仍在 `error` 里（summary 是标签，不是正文），所以这里只要求帽落在词界上。
    """

    MSG = ("css_selector 'a >> b {;}' is not a selector the extraction engine can compile "
           "(SelectorSyntaxError: Expected selector, got <DELIM '>' at 3>) - it would have "
           "been ignored and the whole page returned")

    def test_the_summary_ends_where_a_word_ends(self):
        out = quality._invalid_request_result("https://example.com", self.MSG)
        prefix = "invalid request · "
        assert out.summary.startswith(prefix)
        tail = out.summary[len(prefix):]
        assert self.MSG[len(tail)] == " ", f"summary 停在词中间: {tail!r}"

    def test_the_error_still_carries_the_whole_sentence(self):
        out = quality._invalid_request_result("https://example.com", self.MSG)
        assert out.error == f"invalid_request: {self.MSG}"

    def test_a_short_rejection_is_not_padded(self):
        """短句子不需要剪，也就不该多出词界的痕迹。"""
        out = quality._invalid_request_result("https://example.com", "url is required")
        assert out.summary == "invalid request · url is required"


class TestTheRejectionSummaryStopsAtAClause:
    """词界只保证不剪出半个 token，不保证那句话说得完（2026-10-08 真实使用模拟）。

    同一轮真宿主调用里抓到三处：`…stored credentials for`（悬空介词）、
    `…POST / PUT / PATCH /`（悬空斜杠）、`…Valid values:`（冒号后面正是那串合法值 ——
    被剪掉的正是解药）。整句仍在 `error` 里，所以标签可以短；但一个以 "for" 或 ":"
    收尾的标签不是"短"，是"坏"：它承诺了一个自己没有的东西。
    """

    # 四条都是这一轮实测打出来的原话，不是编的样本。
    POST = ("method=POST is a write: it changes server state, and stored credentials "
            "for this host ride along automatically")
    GET = ("method=GET is a read, not a request - web_request takes POST / PUT / "
           "PATCH / DELETE / HEAD")
    ENUM = ("extraction_type='markdownx' is not a value this tool accepts. Valid "
            "values: markdown, html, text, article, structured, check. No request "
            "was made.")
    GUARD = ("URL hostname localtest.me resolves to internal/private IP (127.0.0.1 in "
             "127.0.0.0/8). This guard is fail-closed: on a fake-ip host only the "
             "tunnel ranges are tolerated - it is refused whatever this machine measured.")

    @pytest.mark.parametrize("msg", [POST, GET, ENUM, GUARD])
    def test_the_label_never_ends_on_a_dangling_word_or_separator(self, msg):
        label = quality._summary_label(msg)
        assert label, "剪完什么都不剩就是另一种坏"
        assert label.lower().split(" ")[-1] not in {
            "for", "and", "the", "a", "to", "of", "with", "in", "not", "is", "takes"}, (
            f"标签停在一个需要宾语的词上：{label!r}")
        assert not label.rstrip().endswith((",", ":", ";", "-", "/", "(")), (
            f"标签停在一个分隔符上，像在列一份没列完的东西：{label!r}")
        assert msg.startswith(label), "标签必须是原话的前缀，不许改写"

    def test_the_enumeration_is_not_promised_and_then_cut(self):
        """`Valid values:` 收尾 = 承诺了一份不存在的清单；句子边界在那之前。"""
        label = quality._summary_label(self.ENUM)
        assert label == "extraction_type='markdownx' is not a value this tool accepts"

    def test_a_clause_inside_the_cap_beats_a_word_at_the_edge(self):
        """有子句边界就用它，哪怕词边界能多带几个字符。"""
        label = quality._summary_label(self.POST)
        assert label == "method=POST is a write: it changes server state"
        assert "stored credentials" not in label

    def test_no_boundary_in_the_cap_falls_back_to_the_word(self):
        """一条边界都没有时，退回原来的词界规则 —— 这一条不许被新规则吃掉。"""
        msg = "alpha " * 30 + "omega"
        label = quality._summary_label(msg)
        assert msg[len(label)] == " "
        assert label == ("alpha " * 13).strip()

    def test_the_whole_sentence_still_travels_in_error(self):
        out = quality._invalid_request_result("https://example.com", self.ENUM)
        assert out.error == f"invalid_request: {self.ENUM}"
        assert "structured" in out.error, "剪的是标签，不是正文"
