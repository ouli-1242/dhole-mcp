# P05 常镜秋 · 评估师 L2 —— 链条⑤act pin 挂页→回读复核→request 会话接力

## 读数（主跑 6 发 + 补 p05b 5 发 + 补 p05c 2 发）

| 发 | 工具 | 读数 |
|---|---|---|
| S1 | act /login snapshot (pin=true, p05a) | ok，"page pinned" 句在 summary（首轮被 130ch 截断看不见） |
| S2/S3 | act **无 url** | 具名拒：`url is required: the page to act on, one URL per call`——pin 从不替代 url（脚本误用，拒绝正确） |
| S4 | request POST /login confirm (p05a) | 200 · 34B ✓ |
| S5 | act /account read h1 (p05a) | h1=「欢迎回来」✓ |
| b-S6 | act /login fill+read (p05b, pin) | fill ok=true；read 缺 `property:"value"` 具名拒（form field 教学句）；**auth_wall 判词首发**：`auth_wall_detected: page is a login/sign-in wall, not content`，next_action 教两扇真门（cred_defaults / 另一源） |
| c-S11 | act fill + read property:value, as=who (p05c) | read='gupiaoren'，read_as=who，"page pinned" ✓ |
| c-S12 | act 同 url/session/pin 回读 | **回读='gupiaoren'**（页保持，未重导航）+ "pin hit" 句 ✓ |

## 合 · 不合

- **合**：r08 pin 合同完整回归——同 url+session+pin 第二发站在留下的页上（pin hit），
  填过的值活着；寄存器铸值/取用一致。auth_wall 判词与动作收据分家（判词说内容、
  actions 行说动作），双账不混。
- **脚本侧误用（非缺陷）**：S2/S3 想省 url——合同是"每发都要 url，pin 只管页"；S6 的
  read 想读 input 裸文本——合同是 form field 用 property:value。两次第一拒都具名带出路。

## 判定

链条⑤全绿（补条后）。pin 合同与会话接力双 PASS。
