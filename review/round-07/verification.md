# round-07 复验记录（P5）· 2026-10-26

- 被验代码：branch `review/round-07` 工作树（P4 修复未提交，提交在 P6 落地）。
- **真重启**：kill 9207（pid 26140）→ 修复后代码重启；靶站 8399 持续。
- 全量 pytest：**7757 passed, 23 skipped, 0 failed**（pytest-full.log，142.27s；round-06 结点 7755 + 本轮新测试 2 = 7757 对账平）。诚实记录：修复后首轮全量曾报 2 failed（DNS-recheck xdist flake 邻域），**连续两轮完整重跑全绿**。
- ruff：**16 条基线**（0 新增）。
- 复验方式：唯一 B 类修复一发真重启后的线上重放（sessions/verify-r7.log）。
- P3 补测在案：probe A（cloud metadata fetch 层）/ probe B（watch 错误行原文）→ sessions/verify-p3-probe.log。

## 逐项复验

| # | 项 | 证据 | 判定 |
|---|---|---|---|
| W1 | R07-001 寄存器拒绝补 pin 出路 | 真重启后 pin=true 裸 fill 重放：拒绝句全文带「registers live for this one call: mint the name in THIS chain with an earlier read step's as=code before the fill; a pinned page (pin=true) holds the page open, so a re-read in the next call's chain returns the same token」 | **FIXED**（verify-r7.log W1） |
| W2 | 手册对账总判定 | configuration 篇 19 个 env 与代码 env_registry **19=19 双向零差**（P6 以主证据重 derive）；start 三路线照做全通；参数断言（clamp 申报/urls=11 收——10 帽是 check 档专属，描述如实/extraction_type 名单句/sys_state show 键）逐条合；绝对句两处实证：cloud metadata 拒绝（probe A，豁免在力下 fetch 层具名拒）、archive 不可关（404 行 summary 带「archive.org fallback was asked … HTTP 429」） | **手册诚实 PASS** |

## 本轮「手册与实况」总账

六轮修复后的手册经 10 画像逐条对账：**1 处话术缺口（已修）+ 1 处描述精度观察（urls= 的 10 帽是 check 专属，描述如实）**，其余全部对上——文档面与行为面自 R01 以来的六轮拉齐生效。
