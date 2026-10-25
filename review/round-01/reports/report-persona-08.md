# 画像报告 · P08 Devin（外包开发 · L2）· round-01

> 替客户做集成：登录态、上传提交、悬停拖拽、验证码。11 发（p08-*）。

## 场景实录

- **S1 登录链（回归#1）PASS**：web_request POST /login（confirm + session_id=devin-1）→ 200，回执 session_id 与 session_cookie_names:["sid"]；同 id web_read /account → 「欢迎回来 这是仅登录用户可见的内容。退出登录」。cookie 落 jar、登录态可续——manual 轮修复在真实编排里站住了。
- **S2 上传链（回归#5）PASS**：upload 1 file(s) → click #send → pending_approval（submitting is an irreversible step - ask the user to confirm）→ confirm 重跑 → 站点回执「received sample.docx (63552 bytes)」。
- **S3 悬停拖拽（3 发三拒才学会形状）**：drag {from,to} 拒 → drag {selector} 拒 → **正形 {selector, to}**（drag_to 真跑了）——但我猜的 #drag-knob/#drop-zone 页上不存在，10s 超时，status 停在 IDLE。选择器猜了三轮没摸到真 ID；snapshot 回执里也只见页面文本、未见「编号元素列表」。
- **S4 验证码**：/captcha → bot_wall_detected（「page is an anti-bot/CAPTCHA challenge, not content」）——点破不装 ✓；/captcha.png 直连 → 400 缺 token（页面 img 才带 token）。「本来就不是给脚本看的。」

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因 | 严重度 |
|---|---|---|---|---|---|
| 22:17 | S3 hover 猜错 | 「又猜选择器」 | 13s | 无轻量「结构速览」；snapshot 回执没给编号列表 | 骂人边缘 |
| 22:17 | S3 drag 形状三连拒 | 「from/to、selector、selector+to，第三次才对」 | 8s×3 | 拒条逐次才吐完整形状（第一句就该给全） | 骂人边缘 |
| 22:19 | S3 超时 | 「ID 是什么总得告诉我」 | 20s+ | 选择器无发现机制闭环 | 骂人边缘 |
| 22:18 | S4 png 400 | 「token 在页面里」 | 0 | 直连缺参（D） | 无感 |

## 放弃点

S3：**放弃拖拽端到端**（形状学会、ID 无门）；S4：放弃（能力边界合理）。

## 主观评分

- 第一印象 5 / 找答案速度 3（S3 三连拒）/ 答案可信度 5 / 报错可读性 4（形状逐步吐 vs 一次给全）/ 等待感受 4 / 总体 4.2。

## 改进清单（按感知强度）

1. 【我想骂人→希望】动作形状拒绝**第一次就给完整正形**（drag 三拒三训）。
2. 【希望】snapshot 回执带「编号交互元素列表」（手册承诺了 step 1 numbered；实测回执只有页面文本——P3 对账）。
3. 【保持】登录链、提交闸两条真实编排全绿。

## 附录

证据：persona-08/01/02（登录链）、03/04（上传闸+回执）、05/06/09/10/11（drag 形状三连拒+正形超时）、07（bot_wall）、08（png 400）。
