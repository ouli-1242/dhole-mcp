# P10 覃九鼎 · 拍卖师 L2 —— 链条⑩checkout 提交闸→confirm 真提交→state

## 画像与卡面

拍卖师，L2。情境：在结算页把数量改成 2 并提交；这是不可逆动作，必须先看到闸、再确认。
目标：闸前动作行逐条 ok；确认后真的落单；state 收尾对账。验收：pending 分家；confirm 落账。
补条背景：主跑 S3 把 `confirm` 放在**顶层**，被闸再次拦下；p18 用 action 级正形补真提交。

## 读数（主跑 5 发 + p18 补条 4 发）

| 发 | 工具·形状 | 结果 |
|---|---|---|
| S1 | web_read /checkout | 200 · **15B · no_content**（「no prose (menu fragments only)」）——表单页没有正文 |
| S2 | act：fill `#qty`=3 → `submit #place-order`（无 confirm） | fill ok；submit **ok=false, pending_approval=true**；动作行教 `confirm: true` 写在 click/submit 上；页面未提交 |
| S3 | 同链 + **顶层** `confirm: true` | submit **仍被闸**（pending_approval）；动作行原文：`re-run the same actions with confirm: true on the click/submit action itself ({"click": {"selector": "...", "confirm": true}}), not at the top level`；read h1 仍=确认订单 |
| S4 | sys_state | 1 session、0 cookie、Nothing erased |
| p18-S1 | web_read /checkout | 200 · 15B · cache（no prose） |
| p18-S2 | act：fill `#qty`=2 → `submit {"selector":"#place-order","confirm":true}` → read h1 | 两行 ok；h1=**订单已提交**；靶站 orders.log **+1 行**（item=机械键盘 qty=2, 2026-10-26T00:53:18Z） |

## 合 · 不合

- **合**：提交闸把「不可逆」讲清楚，闸前动作行和 content 判词分家（fill ok、submit pending），
  确认后真落单且有**服务端独立证据**（orders.log）。r12 必测 4（auth_wall 同族分家）在提交闸上回访。
- **不合 / 候选 C1**：顶层 `confirm` 是 next_action 字面最容易被读成的形状，我照做了一次，
  又被闸——两次「确认」都没发出去。真正能用的形状（action 内 confirm）写在**动作行 error**，
  next_action 只说 `re-run … with {"confirm": true} on that submit`，没有给出嵌套示例。
  这是「修复句只在深一层」的老家族（R09-003），P3 判 E/D。
- **不合 / 老摩擦**：/checkout 的 read 只有 15B，看单必须 act snapshot；表单页无正文。
- **账差（环境侧）**：p18 的 orders.log 是 7 行（原 6 行 +1），与回执 h1=订单已提交 互认。

## 摩擦清单

1. 顶层 confirm 被拒，重试仍被拒（C1：next_action 措辞把人往顶层带）。
2. 表单页 read 无正文（与 P01/P03 同根）。
3. 提交闸第一次出现时，动作行 ok=false 会让 content_ok=false；用户要读到行内才明白「只是没交，不是失败」。

## 判定

- 提交闸 + 真提交 PASS（p18 服务端旁证：orders.log +1）。
- C1（next_action 顶层 confirm 措辞）转 P3；R09-003 家产同族。

## 主观评分

3.5/5 —— 闸是对的、真提交也是对的；但「怎么确认」让我走了两遍错路才在行内找到正形。
