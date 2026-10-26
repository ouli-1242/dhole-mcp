# round-15 P5 验证（并行与隔离轮 · 1 CONFIRM）

> 分支 review/round-15 · 基线 ea25700 · 端口 9215/8399。
> **并发前提**：工作树含另一位 agent 的未提交改动；本轮只改 `actions.py` property 分支 3 行，
> 以及 `tests/test_actions_2.py` 一枚新测试。P6 只暂存本轮 hunk。

## 1. 真重启

- 2026-10-26T11:40:1x 杀掉 dhole-9215 旧进程 **pid 18560**，`ensure_up.ps1` 起新进程
  **pid 27864**；靶站 8399 保持 **pid 22360** 不动。
- 重启后 `tools/list` = 13；V1 第一发即打在新进程上（修复已加载）。

## 2. 复验 V1-V5（scripts/verify_r15.py · sessions/verify-r15.log）

| # | 主题 | 读数 | 判 |
|---|---|---|---|
| V1 | R15-001：空框 property 读 | 动作行 `ok=False`，error=`read found no property 'value': 'value' is empty on this field - that is its current value, not a missing read`；无 `empty_hint`/无 Python 异常 | PASS |
| V2 | 双 pin session 隔离 | A 填「甲」、B 填「乙」，回 A 仍读回「甲」且 `pin hit` | PASS |
| V3 | 缓存回放时间戳方向 | live=`03:40:17Z`、replay=`03:40:17Z`、`cached=True`；断言「回放不晚于 live」 | PASS |
| V4 | r14 三个新特性 | `since=2026-10-23T03:40:42…+00:00`（绝对 ISO）；窗口 `473B of 7.0KB markdown`；check 首问 baseline、再问 changed=True | PASS |
| V5 | r13 三件回归 | css 收窄 chars=10 且 content_ok=True；selector 0 匹配注记 live/hit 都在；feed -3 具名拒绝 | PASS |

**阴性说明**：V1 的阴性形状（非空 property 读成功）由既有测试矩阵承担；
V3 的 1 秒级漂移无法稳定造阴性，按「回放不晚于 live」的方向断言（R15-002 记 E）。

## 3. 全量 pytest

- 命令：`python -m pytest -q`（当前工作树，含另一位 agent 在途测试）
- 结果：**7845 passed, 23 skipped, 3 warnings in 151.54s**（log: pytest-full.log）
- 对账：r14 收官 7844 + 本轮新增 1 枚（R15-001 回归测试）→ 7845，平账。

## 4. ruff

- `python -m ruff check .` → **Found 16 errors**，与 r10-r14 基线一致（未增未减）。

## 5. 并发纪律

- 修复只落在 `src/dhole_mcp/actions.py` 的 property 读分支（3 行）；该文件同时含对方 336 行
  在途改动，P6 用单 hunk 暂存（`git apply --cached`），不吞其改动。
- 未编辑对方正在改的 models.py / server.py / quality.py / skills docs。
- review/backlog.md 含对方在途状态更新：本轮不暂存，round-15 的 E 记录写进 common-issues.md。
