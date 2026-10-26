# P07 阿图 · 分析师（L2）—— steps 形状

session `0808c0ecb09f4904a3ce98afcbfb00cf`

| 场景 | 误用 | 回执 | 判定 |
|---|---|---|---|
| S1 actions 是字典 | `{"click": "#note"}` | `actions must be an array of objects, got dict` | PASS |
| S2 元素是字符串 | `["click #note"]` | `action 0 must be a dict with exactly one key [...]` | PASS |
| S3 selector 是数字 | `{"click": 123}` | 数字=快照行号（文档化）；`element index 123 is beyond this page's 3 interactive elements` | PASS |
| S4 OpenAI 风格 | `{"action": "click", "selector": ...}` | 同一句形状拒绝 | PASS |
| S5 不存在的 selector | `#nope` | selector miss 具名 + 驱动原文 | E（R18-003） |
| S6 空数组 | `[]` | `actions is required: a non-empty list ...` | PASS |
| S7 自愈 fill/click/read | — | 三步全 ok，读回 | PASS |

**结论**：六种形状错误五种具名；数字被读成 index 是文档化契约且失败句给出页内元素数，不算误导。驱动原文外泄见 R18-003。
