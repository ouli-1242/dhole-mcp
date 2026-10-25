# 画像报告 · P03 库尔班（运维 · L1）· round-07

> 主题：手册与实况。3 发 MCP + watch CLI 旁支。p03- 前缀。

## 场景实录

- **S1 start 篇三路线**：L1 视角照走不迷路（sys_guide start 全文在回执）。
- **S2 盯站→feed_read**：1 URL → 3 items newest first；note 把「8 条全量、3 条在途、无日期 2 条」的账算给调用方。**合**。
- **S3 watch CLI**：add ✓ → `watch run` **exit 2（1 checked · 0 changed · 1 error）** → remove ✓。当轮量具只留了计数行，错误行原文没抓到（量具薄点）。**P3 probe B 补测**：错误行全文 = `invalid_request: URL targets internal/private IP (127.0.0.1 …) · watch runs in its own process, so the MCP-side cred_defaults waiver in memory mode never reaches it: set DHOLE_PERSIST_CREDENTIALS=1 …`——正是 R04-F2 修的 watch 语境出路句在真实 CLI 里工作。**合**。

## 摩擦日志
（无产品候选；量具侧记录：watch 错误行当轮未落盘，probe 补证。）

## 主观评分
- 5 / 5 / 5 / 总体 5。

## 附录
证据：sessions/persona-03/ + p03-run.log + sessions/verify-p3-probe.log（probe B）。
