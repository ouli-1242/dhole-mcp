"""R04-002：watch 私网出路句要点名「flag 得在 watch 进程的环境里」。

现场（round-04 P03）：R02-001 的出路（DHOLE_PERSIST_CREDENTIALS=1 + cred_defaults）
方向验证为真——带 flag 的 watch 进程 baseline 200 exit 0；但 `dhole watch run`
是独立进程，flag 不在**它**的 env 里就永远读不到（不带 flag 的 watch run 依旧
exit 2 实测）。出路句要补这半个前题，否则读者会把 flag 设在 MCP 侧就收工。
"""
from dhole_mcp.ops import _WATCH_PRIVATE_HINT


def test_watch_hint_names_the_watch_process_environment():
    assert "DHOLE_PERSIST_CREDENTIALS" in _WATCH_PRIVATE_HINT
    assert "watch" in _WATCH_PRIVATE_HINT.lower()
    assert "environment" in _WATCH_PRIVATE_HINT.lower() or "env" in _WATCH_PRIVATE_HINT.lower(), (
        f"the hint must say the flag belongs in the watch process's own "
        f"environment, got: {_WATCH_PRIVATE_HINT!r}")
