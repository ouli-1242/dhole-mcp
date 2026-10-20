"""动作的形状表只有一个作者：校验器自己的正文。

为什么这是一条要钉住的判据，而不是一次整理：`/hoverdrag` 那一次量到的是**三条 0 秒的
参数拒绝、约 90 秒白烧**（15 场景体测的场景 11），而答案一直住在报错文本里、不在声明里
—— 只读 `tools/list` 的调用方必须先失败一次才知道 `fill` 要的是 `text` 而不是 `value`。
G25 那一刀把三颗必填键送上常驻层之后，同一张表有了**三个**作者：

1. `_validate_actions` 的正文（唯一真的那份：它决定一颗键是被读、被拒还是被静默忽略）；
2. 常驻层那句 `fill needs text, press needs key, drag needs to`（`server._TOOL_DEFS`）；
3. `tests/test_an_agent_that_only_reads_the_wire_can_name_an_actions_payload.py` 里手抄的
   一份 `PAYLOAD_BY_KEY`。

手抄那份在本刀动笔时就落后了一整颗：期 1.1 给 `read` 加了 `property`，而那张表里没有它
—— 后果不是测试红，是测试**照绿**（它量的是一份与行为无关的清单）。这正是本项目最防的
那一类失败："说的和做的对不上，且没有任何一处报错"。于是表改成从第 1 份的正文推出来
（`actions.action_payload_keys()`，ast 走那颗函数自己读的键），第 3 份删掉、直接调用它。

本文件量四件事，都不看措辞：

* §1 覆盖面 —— 每一颗 `_VALID_KEYS` 都有一格，且只有 `wait` / `snapshot` 这两颗是空的
  （它们**故意**没有可调的东西：快照没有旋钮、wait 只有一个数，多一个键就多一种拼错法）。
* §2 每一颗在册的键都被** honors**：拿一枚与默认值不同的值传进去，归一化产物必须跟着变
  （或当场具名拒绝）。一颗被静默忽略的键不该出现在任何声明里 —— 那正是 `nested_max`
  那一族（审计 D6）立的规矩，反过来读同样成立：**声明里出现的名字必须做决定**。
* §3 常驻层点名的三颗必填键落在表里（第 2 份不许说出第 1 份不读的名字）。
* §4 手册那一节把表里每一颗都写了 —— 常驻层只放三颗必填，其余按 `test_wire_moves_land_
  in_the_manual.py` 的裁决住在语料里；语料缺一颗就是那句话没了作者。
"""

from __future__ import annotations

import re

import pytest

from dhole_mcp import guide
from dhole_mcp.actions import _VALID_KEYS, _validate_actions, action_payload_keys
from dhole_mcp.server import MasterFetchServer

TABLE = action_payload_keys()

#: 每颗键一枚**与默认值不同**的值。相同值会让 §2 的差分探针看不见效果 —— 第一版正是
#: 这样把 `press.key`、`screenshot.image_type` 这些"传了等于没传"的假阴性放过去的。
PROBE_VALUE = {
    "selector": "#another-one", "index": 4, "expect": "另一个名字",
    "text": "另一段字", "key": "Tab", "to": "#dropzone",
    "attribute": "href", "property": "checked", "as": "别的名字",
    "confirm": True, "steps": 5, "ms_per_step": 700, "count": 6,
    "state": "hidden", "timeout_ms": 4000, "full_page": True, "decode_qr": True,
    "image_type": "jpeg", "quality": 50, "save_to": "somewhere.png",
}
#: 载荷的起点：一颗元素动作总得有个目标，而两颗动作少了这一颗键就根本不成形。
SEED = {"press": {"key": "Enter"}, "fill": {"text": "hi"}, "drag": {"to": "#b"}}


# ─── 1 · 覆盖面 ───────────────────────────────────────────────────

def test_every_action_key_has_a_row_in_the_table():
    """表与动作名单必须同颗 —— 少一格就是新动作没进尺子。"""
    assert set(TABLE) == set(_VALID_KEYS), (set(TABLE) ^ set(_VALID_KEYS))


def test_only_the_two_deliberately_knobless_actions_are_empty():
    """`wait` 只有一个数、`snapshot` 没有可调的东西，这两格空是**判决**不是遗漏。

    反过来也钉住：给它们加一颗键，这一条会红，而那是一次显式决定 —— 与 §2 一起读，
    意思是"新形状要么被 honors，要么不许出现"。
    """
    empty = {k for k, v in TABLE.items() if not v}
    assert empty == {"wait", "snapshot"}, empty


def test_the_table_is_still_derived_not_recopied():
    """静态守卫：`actions.py` 里那张表不许长出第二份字面量抄本。

    `action_payload_keys()` 的正文里不许出现 `"click": {…}` 这种成套的动作键；推导一次、
    读取多次，是本仓库对"一张表三个家"的一贯答复。
    """
    import inspect

    src = inspect.getsource(action_payload_keys)
    assert "ast.parse(inspect.getsource(_validate_actions))" in src
    for action in sorted(_VALID_KEYS):
        assert not re.search(rf'"{action}":\s*{{', src), action


# ─── 2 · 在册的键都真的被读到 ─────────────────────────────────────

@pytest.mark.parametrize("action", sorted(TABLE))
def test_a_listed_payload_key_changes_the_verdict_or_the_normalized_row(action):
    for key in sorted(TABLE[action]):
        if key == "selector":
            continue   # 起点本身就是 selector，换它不改变形状，只换寻址

        def build(with_key: bool):
            payload = {"selector": "#a", **SEED.get(action, {})}
            if with_key:
                payload[key] = PROBE_VALUE[key]
            return _validate_actions([{action: payload}])

        try:
            plain = build(False)
        except ValueError:
            pytest.skip(f"{action} 的基本形状需要别的种子（{key} 不在这一格的可比范围）")
        try:
            with_key = build(True)
        except ValueError:
            continue   # 具名拒绝 = 这颗键被读到了，§2 允许的第二种答案
        assert with_key != plain, (
            f"{action}.{key} 在册却被静默忽略：归一化产物与不传它一字不差")


def test_the_probe_seed_covers_every_action_in_the_table():
    """§2 不许悄悄跳过整颗动作：每一格都要被上面那条走到（skip 只许是键级的特例）。"""
    # `wait` / `snapshot` 不传 dict，两颗都在 §1 单独钉着，这里数其余的动作。
    probed = {a for a, keys in TABLE.items() if keys}
    assert probed == set(TABLE) - {"wait", "snapshot"}, probed


def test_an_unknown_payload_key_is_not_in_the_table():
    """尺子的另一边：一张"什么都能进"的表等于没有表。

    `zz_noise` 这类名字被校验器怎么对待不由本文件判（那是 D6 那一族的事），本文件只钉
    "推导出来的表不许因为它也变宽" —— 表若开始照单收，第 1 份作者与这份表就又是两件事。
    """
    for action in sorted(TABLE):
        assert "zz_noise" not in TABLE[action], action
    assert "value" not in TABLE["fill"], "fill 的载荷叫 text；`value` 是被猜错的那一颗"


# ─── 3 · 常驻层点名的必填键 ───────────────────────────────────────

def test_the_three_required_keys_the_wire_names_are_read_by_the_validator():
    """`fill needs text, press needs key, drag needs to` 那句不许说出表里没有的名字。

    常驻层是第 2 份作者：它只有在这一句与第 1 份一致时才是真话。这一条读的是当天
    serve 的那颗描述，不是一句抄在测试里的字符串。
    """
    wire = next(td for td in MasterFetchServer._TOOL_DEFS
                if td["name"] == "web_act")["inputSchema"]["properties"]
    text = wire["actions"]["description"]
    named = re.findall(r"(\w+) needs (\w+)", text)
    assert named, f"常驻层那句必填键的话读不到形状：{text!r}"
    for action, key in named:
        assert key in TABLE[action], (action, key, sorted(TABLE[action]))


# ─── 4 · 语料把其余的键都写了 ────────────────────────────────────

def test_the_manual_lists_every_payload_key_the_validator_reads():
    """可选项住在按需付的那一卷（`test_wire_moves_land_in_the_manual.py` 的裁决）。

    常驻层只放三颗必填，其余全靠这一节；卷里少一颗，那张表就有一个名字谁都没写过 ——
    调用方的下一步是猜，而猜的代价本刀开头就量过（三条 0 秒拒绝、约 90 秒）。
    """
    tools = (guide._ROOT / "references" / "tools.md").read_text(encoding="utf-8")
    universe = set().union(*TABLE.values())
    missing = [k for k in sorted(universe)
               if not re.search(rf"(?<![\w-]){re.escape(k)}(?![\w-])", tools)]
    assert not missing, f"tools.md 没写出这些键：{missing}"
