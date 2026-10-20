"""只读 `tools/list` 的 agent 能不能拼出一条合法的 action —— 必填载荷键必须住在 wire 上。

症状（15 场景真人体测的交互组，实录见 `review/`）：常驻层的 `actions` 描述写着
"Keys: click, fill, press, hover, drag, wait, scroll, wait_selector, snapshot, screenshot,
submit - each targets a CSS selector or a snapshot index; drag needs a source and a
to-target." —— 它数了 11 个动作名，然后**一个必填载荷键都没点**。于是 `text` 与 `key`
这两个名字在任何 wire 文本里都不出现，调用方只能猜：实测猜错两次（`{"fill": {"selector":
..., "value": ...}}`，以及把 `press` 的键名也写成 `text`），每次代价是一句报错加一次重跑，
而**答案一直躺在报错里**（"must be {selector, text}"），不在声明里。一个不打算读手册的第一
次调用，拿不到它需要的唯一一个信息。

这与 B5 那条规矩同一家（**搬家，不是缩写**）：wire 带动作名单 + 三颗必填载荷键（`fill=text`、
`press=key`、`drag=to`），可选旋钮（`count/state/timeout_ms/steps/ms_per_step/full_page/
save_to/image_type/quality/expect/confirm`）留在 `sys_guide(topic='behavior')` 送得出的那一
卷按需付。296 字符那道闸（`tests/test_wire_shape.py`）就是为这个分工而立的 —— 把整表塞上
wire 会把它顶爆，而顶爆的修法从来不是抬闸，是搬家。

判据的形状（为什么这么量）：

1. **动作名单从 `_VALID_KEYS` 推**，不是手抄：加一颗新动作而忘了上 wire，本文件红。
2. **必填载荷键从验证器自己的报错里推**，也不是手抄：拿 `{"selector": "#x"}` 逐颗试，
   被拒时念出的花括号组里那个键名就是"你缺的东西"。报错会点名的键，声明里必须也有 ——
   这条判据正面写着本刀要修的错：**知识只活在报错里，就等于要求调用方先失败一次**。
3. **那份手抄的完整键表**由三半边钉住：照它拼出的合法载荷喂真验证器必须通过（键名或形状
   一改先红）；每个名字都要出现在 `sys_guide` 真送得出的那一卷里（搬过去的家不能是空的）；
   wire 上出现的键名 token 必须全在那份表或动作名里（把 `value` 这种猜错的东西抄上声明也红）。
"""

from __future__ import annotations

import json
import re

import pytest

from dhole_mcp import guide
from dhole_mcp.actions import (_VALID_KEYS, _validate_actions,
                               action_payload_keys)
from dhole_mcp.server import MasterFetchServer

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
                {"drag": {"index": 3, "to": {"index": 7, "expect": "落点"}}}]
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
    """机制自检：报错点名必填键这件事今天真实存在，且点名的就是那三颗。

    若验证器改了措辞不再在花括号组里列必填键，`REQUIRED` 会空掉，下面两条判据就此变成
    摆设 —— 所以这里先钉住"探针有效"，再钉住 wire。
    """
    assert REQUIRED == {"drag": {"to"}, "fill": {"text"}, "press": {"key"}}, REQUIRED
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
