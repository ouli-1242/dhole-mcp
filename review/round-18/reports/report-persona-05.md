# P05 闻笛 · 安全审计（L3）—— 秘密是否进回执

session `b8657511d52745028103c0b86137fffa` / `4ed37790ff474fc6898e0ac8eb7aaca4`

| 场景 | 输入 | 回执检查 | 判定 |
|---|---|---|---|
| S1 auth 缺 host | password `S3cret-闻笛` | 拒绝并要求 `"host"` 同行；回执无秘密 | PASS |
| S2 错形状再试 | 同上 | 同样拒绝；无秘密 | PASS |
| S3 show | — | 只列 `allow_private`；`message` 明说值永不展示 | PASS |
| S4 Authorization 明文头（GET） | `Bearer sk-live-DEADBEEF` | 先被读动词闸拒；无 bearer | 量具 |
| 补条存 auth@127.0.0.1 | 正确 host 形状 | 存入成功；回执无密码 | PASS |
| 补条 show | — | `allow_private, auth@127.0.0.1`；无密码 | PASS |
| 补条 web_read 带凭据 | /account | 14B，无密码 | PASS |
| 补条 POST + Authorization 头 | bearer | `headers= names 'authorization', and a credential is not something a call carries` + 指 cred_defaults；无明文 | PASS |
| 补条 unset | — | `forgot auth` | PASS |
| S6 sys_state | — | 只有 allow_private；无密码、无 bearer | PASS |

**结论**：秘密进不去回执，也进不去 sys_state；明文 Authorization 头被当作调用级凭据拒绝并指回正确的门。安全面 PASS。
