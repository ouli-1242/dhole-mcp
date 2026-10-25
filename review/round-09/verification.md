# round-09 复验记录（P5）· 2026-10-26

- 被验代码：branch `review/round-09` 工作树（P4 修复未提交，提交在 P6 落地）。
- **真重启**：taskkill 旧 9209（pid 20952）→ 全新进程 pid **24828**（dhole-9209.log 启动横幅）→ V1/V2 重放命中该进程；靶站 8399 持续。
- 全量 pytest：**7768 passed, 23 skipped ×2 连续**（7757 + round-08 8 + 本轮 3 = 7768 对账平）。前一轮全量曾 2 failed（DNS-recheck xdist flake——round-06/07 在册、round-08 曾 5 连跑的那对），本轮两连绿；诚实记录在册。
- ruff：**16 条基线**（0 新增；recall.py:228 为改行后位移的既有条目）。
- 复验方式：唯一 B 类修复一发真重启后的线上重放（sessions/verify-r9.log）。

## 逐项复验

| # | 项 | 证据 | 判定 |
|---|---|---|---|
| V1 | R09-002 recall limit=21 → clamp 具名 | 回执带 `clamped: ['limit -> 20 (1-20)']`（对照 web_search/web_research 的具名形状） | **FIXED**（verify-r9.log V1） |
| V2 | 未越界安静 | limit 缺省时回执无 `clamped` 键 | **FIXED**（verify-r9.log V2） |

## 本轮「检索与排序质量」总账

检索面 74 发对账：**1 处真缺口修复（recall clamp 静默 → 具名）+ 3 起假候选全部被自己的回执/源码推翻**（min_relevance 的账在 fetch_hint 全文里、location 门 probe 实证在、前缀双形状是 test 立的规矩）——「判无账前读全四个账面字段」的教训第三度入册。检索面正面账（过滤语义/零结果不装满/排序账目/第一拒教学/交叉账对齐）全部成立。
