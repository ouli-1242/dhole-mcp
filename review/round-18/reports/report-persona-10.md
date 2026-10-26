# P10 艾米 · QA（L3）—— 系统工具形状错与污染检查

session `0caa1d76df98406b8a6c8820e0409f4a`

| 场景 | 误用 | 回执 | 判定 |
|---|---|---|---|
| S1 guide topic 拼错 | `web_readd` | 原文教学：`unknown topic 'web_readd'` + 已知 topic 全表（r11 A 家产） | PASS |
| S2 guide topic 类型错 | `["web_read"]` | 原文拒绝：`topic must be a string, got list` | PASS |
| S3 state show 未知键 | `["nonexistent"]` | `Nothing matches 'nonexistent': a token is one of cache, engine_state, sessions, cred_defaults, ...` | PASS |
| S4 cred set 是字符串 | `"allow_private"` | `set must be an object of key/value pairs, got str` | PASS |
| S5 cred unset 是字符串 | `"allow_private"` | `unset must be an array of strings, got a single string` | PASS |
| S6 形状错后私网页 | /private/secret | 仍 200、14B（allow_private 未被误伤） | PASS |
| S7 收尾 state | — | 1 session、1 cred_default（allow_private） | PASS |

**结论**：系统工具与 cred_defaults 的形状错误全部具名；错误没有污染 allow_private 或会话。
