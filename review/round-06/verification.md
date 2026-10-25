# round-06 复验记录（P5）· 2026-10-26

- 被验代码：branch `review/round-06` 工作树（P4 修复未提交，提交在 P6 落地）。
- **真重启**：kill 9206（pid 15124）→ 修复后代码重启；靶站 8399 持续。
- 全量 pytest：**7755 passed, 23 skipped, 0 failed**（pytest-full.log，127.73s；round-05 结点 7751 + 本轮新测试 4 = 7755 对账平）。诚实记录：修复后首轮全量曾报 2 failed（DNS recheck 网络型 xdist flake 为主），单测隔离重跑通过、**连续两轮完整重跑全绿**，记观察非修复。
- ruff：**16 条基线**（本轮顺带清掉一枚基线漂移的 F401 未用 import，见 fix-R06-F2 附注）。
- 复验方式：两个 B 类修复各一发真重启后的线上重放（sessions/verify-r6.log）。

## 逐项复验

| # | 缺陷 | 回放步骤 | 原始证据（关键返回） | 判定 |
|---|---|---|---|---|
| W1 | R06-001 0 匹配 selector 静默全页 | 真重启后 `web_read /collections?v=1 css_selector='.no-such-class-xyz' cache_ttl=0` | summary 全文在场：`css_selector '.no-such-class-xyz' was NOT applied: it matched 0 elements on this document - the whole document was returned (narrowing never empties the answer)`——回落行为不变，话说出去了 | **FIXED**（verify-r6.log W1） |
| W2 | R06-002 命中碎片被散文尺误判 | `web_read /collections?v=2 css_selector='.card a' cache_ttl=0` | `content_ok: True`（修复前 false）、substance 留空、basis：`css_selector narrowed this answer to the requested fragment - the whole-page prose and collapse rulers do not apply` | **FIXED**（verify-r6.log W2） |
| W3 | 前五轮家产 | P1 期间真测 | R03-F1 语义、R01-009、R02 家产（check diff/not_a_pdf/provenance）PASS；其余未触发（report-round 如实分列） | **PASS** |

## 缓存控制

W1/W2 全部 `cache_ttl=0` 真抓。复验全程未清全局缓存。
