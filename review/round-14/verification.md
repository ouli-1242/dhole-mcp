# round-14 P5 验证（回访与新鲜度轮 · 0 CONFIRM）

> 分支 review/round-14 · 基线 f5f9340 · 端口 9214/8399。
> **并发前提**：工作树含另一位 agent 的未提交改动（src/tests/docs），被测对象即该工作树；
> 本轮 0 fix，未改任何产品代码或测试。

## 1. 真重启

- 2026-10-26T11:24:14+08:00 杀掉 dhole-9214 旧进程 **pid 16212**，`ensure_up.ps1` 起新进程
  **pid 23964**；靶站 8399 保持 **pid 22360** 不动。
- 重启后 `tools/list` = **13/13**；自测 search 「dhole mcp」回 2 行（rerank=neural）。
- 进程内状态清零（cred_defaults 等），复验脚本各自先置 `allow_private`，与 r10/r12 同法。

## 2. 复验 V1-V5（scripts/verify_r14.py · sessions/verify-r14.log）

| # | 主题 | 读数 | 判 |
|---|---|---|---|
| V1 | feed 相对 since `3d` 从当下回量 | `since=2026-10-23T03:24:36Z`、older=3、items=5（旧闻被切掉、3 天窗口内 5 条） | PASS |
| V2 | 缓存回放（`cache_ttl`） | first cached=None → second cached=True，两次 `fetched_at` 同值 | PASS |
| V3 | `extraction_type=check` 两相 | 首问「1 baseline recorded」；再问 `changed=True` | PASS |
| V4 | offset 续片窗口 | next=478；summary `473B of 7.0KB markdown ... truncated`；回放句在场 | PASS |
| V5 | round-13 回归 | css 收窄 chars=10 不判 collapse；selector 注记 live=True/hit=True；feed max_items=-3 具名拒绝 | PASS |

**阴性说明**：本轮无 fix，V1-V5 全部是「在途新特性/上轮修复」的正向复验；不存在需要造阴性的新判决尺。

## 3. 全量 pytest

- 命令：`python -m pytest -q`（当前工作树，含另一位 agent 在途测试）
- 结果：**7844 passed, 23 skipped, 3 warnings in 153.52s**（log: pytest-full.log）
- 对账：r13 收官 7775 + 对方在途新增/修改测试 69 → 7844，无失败、无 error。
- 归因声明：本数字是**整个工作树**的绿，不是「本轮修复带来的绿」（本轮 0 fix）。若对方后续
  改动引入红，与本轮无关。

## 4. ruff

- `python -m ruff check .` → **Found 16 errors**，与 r10-r13 基线一致（本轮未增未减）。

## 5. 并发纪律

- 全程未编辑/暂存另一位 agent 的 src、tests、skills docs；P3 发现的两条文档类 E
  （discover `max_depth` 漂移、summary 的 `depth <= 2`）正落在其编辑路径上，明确「不碰」。
- review/backlog.md 含对方在途状态更新：本轮**不暂存该文件**，round-14 的 E 记录写进
  common-issues.md，待其落地后再合并回 backlog。
- P6 只暂存 review/round-14 与 review/ 下本轮编辑的跨轮资产。
