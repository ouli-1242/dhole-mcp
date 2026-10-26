# P03 白盒 · 测试工程师（L3）—— 契约外键与空形状

session `ef43b29cc2204a5fb6f08ec1907139b3`

| 场景 | 误用 | 回执 | 判定 |
|---|---|---|---|
| S1 未知字段 | `unknown_field` | 点名 + Supported 全表 | PASS |
| S2 url=null | `url: null` | `Either 'url' or 'urls' must be provided` + next_action | PASS |
| S3 空对象 | `{}` | 同上 | PASS |
| S4 危险键名 | `__proto__` / `constructor` | 两者都被点名为不支持参数 | PASS |
| S5 未知动作键 | `{"teleport": ...}` | `action 0 has unknown key 'teleport'; valid: [...]` | PASS |
| S6 空动作对象 | `[{}]` | `action 0 must be a dict with exactly one key [...]` | PASS |
| S7 收尾正常读 | — | 387B | PASS |

**结论**：契约外键一律具名拒绝，危险键名不进入对象合并；误用后同会话正常读成功。
