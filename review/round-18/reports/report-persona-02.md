# P02 满仓 · 数据工程师（L3）—— 数值边界

session `c5bb36643bca4cb29c67b83f930d3ae2`

| 场景 | 误用 | 回执 | 判定 |
|---|---|---|---|
| S1 max_content_chars 负数 | -50 | `clamped -50->500 (supported range 500-200000)` | PASS |
| S2 零 | 0 | `clamped 0->500` | PASS |
| S3 浮点 | 3.5 | `must be an integer, got float`（提示句写 "Pass a number"，见 R18-006 邻注） | PASS |
| S4 十亿 | 1e9 | `clamped 1000000000->200000` | PASS |
| S5 crawl max_pages 负数 | -3 | `max_pages clamped -3->1 (supported range 1-40)`，1 页 | PASS |
| S6 discover max_pages 字符串 | "5" | 接受并按 5 页跑（数字字符串宽松读，params 顶注） | A/设计 |
| S7 feed max_items 浮点 | 2.5 | `must be an integer, got float` | PASS |
| S8 收尾正常读 | 800 | 387B | PASS |

**结论**：资源帽要么 clamp **且在 summary 具名**，要么拒绝；无静默改值。
浮点与数字字符串的不对称见 R18-006（A）。
