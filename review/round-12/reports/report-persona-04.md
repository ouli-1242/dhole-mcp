# P04 明溪 · 口腔医生 L1 —— 链条④feed 增量→parse 附件→read 原文

## 读数（6 发）

| 发 | 工具 | 读数 |
|---|---|---|
| S1 | feed max_items=3 | "1 URLs asked · 1 rows answered · 1 rows carried 3 items, newest first" ✓ |
| S2 | feed max_items=0 | 8 条（=all，r11 V2 回归 PASS） |
| S3 | feed max_items=-3 | `invalid_request: max_items must be ≥ 0 (0 = all), got -3`——**R11-001 修复真机回归 PASS**（refused before any source was contacted） |
| S4 | doc_parse 复诊须知 CSV | 200 · 65B ✓ |
| S5 | web_read /article | 200 · 387B · cache replay（38.8min 代，replay 诚实句在场） |

## 合 · 不合

- **合**：feed 三连账（3/8/具名拒）逐发可读；r11 修复的具名拒绝经过真服务器往返
  （不是 test 独跑）；parse→read 接得上。
- 无不合。

## 判定

链条④全绿。round-11 必测回归点 1（feed 负数/0=all）在链条中自然回访 PASS。
