# 画像报告 · P07 越千帆2（技术招聘 · L2）· round-08

> 主题：组合与长链。5 发 + p14 补全 5 发 + p15 补正 3 发。p07-/p14-/p15- 前缀。人在中途双挑战。

## 场景实录

- **S1 text 挑战 pin=true**：第一发读 token 3BYC9（pin 挂起）；第二发 fill+click → 闸拦（pending_approval）——流程对，差 confirm 补全。
- **S2 captcha 图形案**：**裸 screenshot {} → invalid_request 第一拒教形**（'screenshot' takes a CSS selector string…——量具修正后用 {"screenshot": {"selector": "#captcha"}}）；fill ZZZZ + click → 闸拦。
- **p14 补全**：captcha 错答 confirm 提交 → **403 具名（验证码错误页）**——错答账目清楚；**text 挑战跨调用（pin 但无 session_id）→ 「抄写不符」**：challenge.log 铁证（answer=V995R vs form token 18c631…→expected RQTT7）——**pin 没接上，因为 pin 驻留按会话存，两次调用没带同一 session_id**。
- **p15 补正**：pin=true + session_id="s7" 两发 → 第二发站上同一页 → **「抄写正确」**。人在中途正形全通。

## 摩擦日志
- **首报候选 R08-006 被 P3 推翻（诚实更正）**：p14 的「无解释」不成立——两发 summary 都带具名拒绝「pin=true could not be honored: an anonymous auto session is shared… Name a session_id= …」，tools.md:40 的承诺逐字兑现；是我量具只读了 summary 前 500 字符。教训同 round-03 的截断教训。
- 量具教训 ×2：裸 screenshot 形、pin 无 session——都是脚本侧，receipts 全落盘。

## 主观评分
- 4 / 3 / 5 / 总体 4（错误现场不解释 pin 没接上，扣在诊断成本）。

## 附录
证据：sessions/persona-07/ persona-14/ persona-15/ + p07-run.log p07b-run.log p07c-run.log + evals/.runtime/challenge.log。
