# round-04 复验记录（P5）· 2026-10-26

- 被验代码：branch `review/round-04` 工作树（P4 修复未提交，提交在 P6 落地）。
- **真重启**：kill 9204（pid 31192）→ 修复后代码重启；靶站 8399 持续；旁证 9205/9206 已随脚本退出。
- 全量 pytest：**7751 passed, 23 skipped, 0 failed**（pytest-full.log，126.68s；round-03 结点 7746 + 本轮新测试 4 = 7750——多出的 1 是 round-03 期间分支上多落的一枚，对账见下）。诚实记录：首次 `-x` 全量跑到中段曾报 1 failed（网络邻域疑似 flaky），其后**连续两轮完整重跑全绿**，FAILED 名单未复现，记为观察非修复。
- ruff：基线 16 条旧告警，本轮 **0 新增**。
- 复验方式：两个 B 类修复各一发真重启后的线上重放（sessions/verify-r4.log）。

## 逐项复验

| # | 缺陷 | 回放步骤 | 原始证据（关键返回） | 判定 |
|---|---|---|---|---|
| W1 | R04-001 iframe 超时不指路 | 真重启后 `web_act /frame` 裸 selector fill | 步级错误：`matched no element… · this page hosts 1 <iframe> element(s) and the selector only sees the TOP document - if the element lives inside one, address it with "frame": …`——33s 税的下一步有路了 | **FIXED**（verify-r4.log W1） |
| W2 | R04-002 watch 出路句 | 真实 CLI `dhole watch add+run`（无 flag） | 错误行带 `set DHOLE_PERSIST_CREDENTIALS=1 in the watch process's own environment too (export it where \`dhole watch run\` runs - the MCP side having it is not enough)…`；带 flag 的通路 P03 已验（baseline 200 exit 0） | **FIXED**（verify-r4.log W2） |
| W3 | 前三轮家产回归 | P1 期间真实回访 | 9 项必测回归 6 项本轮真测 PASS、3 项 round-03 已验未触发（记录在 report-round 回归表） | **PASS** |

## 测试对账

7746（round-02 结点）+ round-03 新测试 1（page_type 描述）= 7747 ✓；round-04 新测试 4（iframe 提示 ×3 + watch 出路句 ×1）= 7751 ✓。分支演进中 test_an_explicit_timeout… 等文件无重复计数（对账平）。

## 缓存控制

W1 web_act 不走缓存（设计）；W2 真实 CLI 进程。复验全程未清全局缓存。
