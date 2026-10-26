# P08 沈未 · 文献研究者（L2）—— 枚举与范围

session `9f048e776f53446299949466f2c3c945`

| 场景 | 误用 | 回执 | 判定 |
|---|---|---|---|
| S1 mode 枚举错 | `deep` | `Invalid mode: 'deep' (auto|neural|find_similar)` + next 教三个值 | PASS（封套 E 见 R18-002） |
| S2 research 空 query + 负 max_sources | `"   "` / -2 | 空串按缺参：点名 `query` + Supported | PASS |
| S3 recall 空 query + 负 limit | `""` / -5 | 同上 | PASS |
| S4 recall 巨大 limit | 100000 | 返回 1 行（relevance 过滤后），无崩 | PASS |
| S5 freshness 错格式 | `yesterday` | `Invalid freshness: 'yesterday' (day|week|month|year)` | PASS（封套 E） |
| S6 负 page | -1 | `Invalid page: -1 (0-10)` | PASS（封套 E） |
| S7 自愈 recall | `机械键盘` limit 3 | 1 行 + terms_found | PASS |

**结论**：枚举/范围错误全部具名并给值集；web_search 这三条错误缺 `content_ok:false` 与 `invalid_request:` 前缀（R18-002，E）。
