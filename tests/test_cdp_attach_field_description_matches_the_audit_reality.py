"""cdp_attached 描述声称「every attach attempt is audited」，而审计通道默认关闭。

症状（2026-10-06 实测）：models.py 的 cdp_attached 描述写"and every attach
attempt is audited"，但 audit.py 的 _audit_event 开头就是
`target = (env_registry.value_of("DHOLE_USAGE_LOG") or "").strip(); if not
target: return` —— 默认零落盘，被拒的 attach 尝试同样无记录。字段描述把
「有审计设施」写成了「无条件已审计」：安全叙事强于现实。

判据：字段描述改成条件式（点名 DHOLE_USAGE_LOG），不再含无条件的
"every attach attempt is audited"；并用行为对照钉住这个条件是真的——
通道关闭时一次事件零落盘（早退，不碰文件系统），开启时落一行。
"""

from __future__ import annotations


from dhole_mcp import audit, env_registry
from dhole_mcp.models import ResponseModel


def test_the_description_is_conditional_not_unconditional():
    desc = ResponseModel.model_fields["cdp_attached"].description or ""
    assert "DHOLE_USAGE_LOG" in desc, "描述要点名审计的开关，而不是声称无条件已审计"
    assert "every attach attempt is audited" not in desc


def test_with_the_channel_off_an_event_touches_nothing(monkeypatch):
    """关闭侧的现实：通道未设 → 早退，不碰文件系统。"""
    monkeypatch.setattr(env_registry, "value_of", lambda name: "")

    def _boom(*a, **k):
        raise AssertionError("audit touched the filesystem with the channel off")

    monkeypatch.setattr(audit.os, "makedirs", _boom)
    audit._audit_event("cdp_attach", outcome="refused")


def test_with_the_channel_on_an_event_is_recorded(tmp_path, monkeypatch):
    """开启侧的现实：通道指向文件 → 事件落一行（被拒的 attach 也记）。"""
    target = tmp_path / "usage.jsonl"
    monkeypatch.setattr(env_registry, "value_of", lambda name: str(target))
    audit._audit_event("cdp_attach", outcome="refused")
    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1 and "cdp_attach" in lines[0] and "refused" in lines[0]
