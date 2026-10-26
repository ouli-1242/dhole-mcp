# P06 乌恒 · 船闸管理员 L2 —— 链条⑥request 写闸→captcha 同链→pin 命中

## 画像与卡面

船闸管理员，L2。情境：先要 POST 一条过闸记录（写操作），再过一道文字验证码。
目标：写操作被闸住时知道怎么确认；验证码同一条链里读到、填回、交掉；页签留住后回读。
验收：写闸教学；pin 命中实证；（计划里的「三试上限」见下）。

## 读数（主跑 5 发 + p17 补条 2 发）

| 发 | 工具·形状 | 结果 |
|---|---|---|
| S1 | web_request POST /login（无 confirm） | invalid_request：`method=POST is a write…`；next_action：`re-send the same call with confirm=true. HEAD probes need no confirm.` |
| S2 | 同 cal + `confirm=true` | 200 · 34B |
| S3 | act：pin=true + read `#challenge` as code → fill `{=code}` → click `#challenge-btn`（无 confirm）→ read `#status` | 读题 **2VR7A**、fill ok；click 被闸（pending_approval，行内教 action 级 confirm）；`read '#status'` 行报 Locator timeout（页面本无 #status，A 类形状） |
| S4 | act：pin=true → read `#challenge` | **pin hit 句在场**，同题 **2VR7A**——页面实例原样留住（r12 必测 1 PASS） |
| p17-S1 | act：read as code → fill → click `{"selector":"#challenge-btn","confirm":true}` → read h1 | 读题 RPKE6、fill、确认后 click ok；h1=**抄写正确**；服务端 challenge.log 同 token `ok=true` |

## 合 · 不合

- **合**：写闸教学清楚（没发出去、要用户确认、HEAD 免 confirm）；pin 命中跨调用保持原页
  （同题、半填状态都在）；确认后的抄写挑战真提交，服务端日志独立佐证。
- **不合**：
  1. **「三试上限」未观测到**：卡面写了要验三试上限，靶站 /challenge/verify 无次数限制；
     本轮未出现任何「上限」句。按实测修正计划项（不写「通过」也不编造缺陷）。
  2. S3 的 `read '#status'` 在验证码页不存在——页面提交后是整页 h1 结果页。这是我脚本的
     selector 假想（A 类），产品如实报了 10s 超时。

## 摩擦清单

1. 验证码点击也在提交闸里（要 action 级 confirm），第一次会以为是普通 click。
2. pin=true 必须显式带；不带就换新页（同 r12 合同）。对 L2 可接受，但需要记住。
3. 「三试上限」在靶站不存在——计划项按实测修正。

## 判定

- 写闸 + pin + captcha 真提交 PASS（r13 卡面验收 1/1 核心；三试上限未观测，如实记录）。
- r12 必测 1（pin 命中）PASS；r11 必测 2（写闸 confirm 形状）PASS。

## 主观评分

4.5/5 —— 闸与 pin 都讲人话；扣分在我自己读错状态选择器那 10 秒。
