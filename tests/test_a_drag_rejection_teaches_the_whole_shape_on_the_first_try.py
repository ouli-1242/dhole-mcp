"""R01-010：drag 的第一拒就要教全 `{selector|index, to}`。

现场（2026-10-25 · P08 Devin · S3 悬停拖拽三连拒 · review/round-01）：
`{"drag": {"from": "#a", "to": "#b"}}` → 第一拒说「needs exactly one of
'selector' (CSS) or 'index' …」（不提 to、不给全形）；改成 `{"drag":
{"selector": "#a"}}` → 第二拒才说 `must be {selector|index, to}`。三连拒才摸到
正形，8 秒 ×3。

根因：drag 分支先对源端跑 `_act_target`（它对 `{"from": …}` 报"selector/index
二选一"），`to` 的全形检查排在**后面**——源端的半句话抢在前面出门。

契约（本文件钉住）：
  * dict 里混进 drag 不认识的键（from/from_selector/…）→ 第一拒就是全形句，
    点名冒出来的键；
  * 缺 `to` → 第一拒照旧全形（现状保住，防回归）；
  * 正形 `{"selector": …, "to": …}` 照旧放行。
"""

from __future__ import annotations

import pytest

from dhole_mcp.actions import _validate_actions


FULL_SHAPE = "{selector|index, to}"


def test_a_foreign_key_gets_the_full_shape_on_the_first_rejection():
    with pytest.raises(ValueError) as e:
        _validate_actions([{"drag": {"from": "#a", "to": "#b"}}])
    assert FULL_SHAPE in str(e.value), str(e.value)
    assert "from" in str(e.value), f"冒出来的键要被点名：{e.value}"


def test_a_missing_to_still_gets_the_full_shape():
    with pytest.raises(ValueError) as e:
        _validate_actions([{"drag": {"selector": "#a"}}])
    assert FULL_SHAPE in str(e.value), str(e.value)


def test_the_correct_shape_still_passes():
    actions = _validate_actions([{"drag": {"selector": "#a", "to": "#b"}}])
    assert actions, actions
