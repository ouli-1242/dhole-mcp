# -*- coding: utf-8 -*-
"""每张登记表都要被门名册问过一遍（B-75 的正解 · 一把会自己报红的尺）。

三轮修下来的形状是同一个：出路、门话、回执形状各自住在一张"门名 → 值"的表里，而**表外那一支
永远不会自己报红** —— 它只是安静地用别人的话或别人的壳答一句。已经因此开的账：

  · R02-114 `feed_read` 的出路族表掉两族（F96 补）
  · R02-103 非抓取门借抓取壳（F97：`_STATE_DOOR_MODELS`）
  · R02-116 `sys_state` 的门话由表外默认支代答（F98）
  · R02-115 `sys_guide` 的门话与语言都在表外（F99：`_DOOR_KIND` + `_PROSE_DOORS`）

这三把都修的是"某一扇没进某张表"，而**没有任何一颗判据会在下一扇门漏进表时报红** —— 那就一定会
再来一把同样的刀。本文件就是那把缺席的尺，判据按门名册 `_TOOL_DEFS` 自己推，不手数：

  ① 每一扇都得在 `context._DOOR_KIND` 里（不许有门由 `_UNDECLARED_DOOR_WAY_OUT` 代答 —— 那一支的
     意义是"没人认领"，今天十一扇全有主，所以它只该兜住未来忘了登记的门）；
  ② 每一扇**非抓取**的门（kind 不在 read/write/action/search 里）都得在 `server._STATE_DOOR_MODELS`
     或 `server._PROSE_DOORS` 里有一副自己的拒绝形状 —— 缺了就是又去借那 13 格的抓取壳；
  ③ 一张门不许同时有两副形状（模型与散文都登记 = 两条路两种语言的复发）；
  ④ 名单来自 `_TOOL_DEFS`：新增一扇而没进表，这里红，而不是在线上安静地借壳（反向锁：判据不许
     自己抄一份门名单）；
  ⑤ 每扇都得在 `server._TOP_LEVEL_ARGS` 里有参数面，且这张表不许挂着不发的门名（F112 补的
     第四张表 —— 评审 B-76 要的常驻版：一次性普查数得出谁漏，数不到下一扇新门）。
"""
from __future__ import annotations

import pytest

from dhole_mcp import context
from dhole_mcp.server import MasterFetchServer

FETCH_KINDS = {'read', 'write', 'action', 'search'}


def _roster() -> list[str]:
    return [td['name'] for td in MasterFetchServer._TOOL_DEFS]


@pytest.mark.parametrize('door', _roster())
def test_every_door_has_a_declared_clause(door):
    """① 在册：这扇门行首那句话有人写过，不是 `_UNDECLARED_DOOR_WAY_OUT` 代答。"""
    assert door in context._DOOR_KIND, (
        f'{door} 的门话仍由"没人认领"那一支代答（B-75 的形状：表外一支不会自己报红）')


@pytest.mark.parametrize('door', _roster())
def test_non_fetch_doors_have_their_own_refusal_shape(door):
    """② + ③ 非抓取的门要有自己的拒绝形状，而且只许有一副。"""
    kind = context._DOOR_KIND[door]
    if kind in FETCH_KINDS:
        # 抓取那一族本来就说抓取：13 格是它们的衣服（F97 的反向锁在
        # tests/test_a_state_door_refuses_in_its_own_shape.py 里钉着"一格不许少"）。
        return
    in_model = _projection_table(door)
    in_prose = door in _prose_doors()
    assert in_model or in_prose, (
        f'{door}（kind={kind}）既不在 `_STATE_DOOR_MODELS` 也不在 `_PROSE_DOORS` —— '
        '它的参数拒绝会又去借抓取壳（R02-103 的复发路径）')
    assert not (in_model and in_prose), (
        f'{door} 同时登记了两副形状：同一扇门两条路两种语言（R02-115 的复发路径）')


def _projection_table(door: str) -> bool:
    import dhole_mcp.server as sm
    return door in sm._STATE_DOOR_MODELS


@pytest.mark.parametrize('door', _roster())
def test_every_door_has_a_parameter_face(door):
    """⑤ 第四张表（B-76 要的常驻版）：每扇都得在 `_TOP_LEVEL_ARGS` 里有自己的参数面。

    这张表是陌生参数名那颗守卫的名单来源（`server._reject_unknown_args`），表外的一扇
    不报错 —— 它只是**谁的话都不收**。反向也问：一张参数面挂在一个不发的门名上，
    就是给一条走不到的句子养裁判。
    """
    import dhole_mcp.server as sm
    assert door in sm._TOP_LEVEL_ARGS, f'{door} 没有参数面（B-75 的形状：表外一支不会自己报红）'
    assert set(sm._TOP_LEVEL_ARGS) <= set(_roster()), sorted(
        set(sm._TOP_LEVEL_ARGS) - set(_roster()))


def _prose_doors() -> set:
    import dhole_mcp.server as sm
    return set(sm._PROSE_DOORS)


def test_the_roster_is_not_hand_copied():
    """反向锁：判据的门名单必须现场从 `_TOOL_DEFS` 取，不许自己抄一份。"""
    doors = _roster()
    assert len(doors) == len(set(doors)) > 8, doors
    assert 'sys_state' in doors and 'sys_guide' in doors and 'cred_defaults' in doors
    # 表外默认支还得留着兜未来的生门：它不是死代码，删了它上面两颗就失去意义
    assert context._UNDECLARED_DOOR_WAY_OUT
    with context.tool_scope('a_tool_that_does_not_exist_yet'):
        assert context.nothing_went_out() == context._UNDECLARED_DOOR_WAY_OUT
