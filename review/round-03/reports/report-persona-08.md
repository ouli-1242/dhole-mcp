# 画像报告 · P08 谷雨（前端工程师 · L2）· round-03

> 主题：web_act 全动作面回归（登录链 / checkout 确认闸 / 挑战页同链 / drag）。6 发。p08- 前缀。

## 场景实录

- **S1 登录态全链**：fill ×2 + click(#login-btn, confirm) → ok=true，200 落点；liveness probe（一次直连探活）自白在场。
- **S2 checkout 该问就问（确认闸）**：click(#place-order) 无 confirm → 步级 `pending_approval: submitting is an irreversible step - ask the user to confirm this submission, then re-run the same actions with confirm: true on the click/submit action itself ({"click…`——**层次说得对（动作本身上 confirm）**（R01 确认措辞回归 PASS）。首报以为顶层没信号（候选 R03-003）；P3 查证**顶层在**：summary 尾巴「· 1 action(s) waiting for user approval (click '#place-order')」+ next_action 整句暂停指引——是本轮 driver 的 100 字截断打印把它藏住了。工具无罪，量具有过（记 journal）。
- **S3 挑战页同链取值（R01-006 回归）**：read #challenge as=code → fill #answer {=code} → click(#challenge-btn, confirm) → 落点页「挑战通过 / 抄写正确 / 同一条链里读到的就是填进去的那一枚」。
- **S4a drag 陌生键第一拒（R01-010 回归）**：`{from:…, to:…}` → 第一拒全形：「action 0 'drag' must be {selector|index, to} - the element to drag plus where to drop it; 'to' takes the same target shapes (…) - unexpected key(s): ['from']」。
- **S4b drag 正形**：#card-a → #slot-b → 步 ok=true，页面事件日志「DROPPED … mousedown@card-a | mouseup@slot-b」。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因 | 严重度 |
|---|---|---|---|---|---|
| S2 | 「顶层怎么不说被闸了」→「说了，是我截断了」 | 0 | 顶层信号完整（summary 后缀+next_action）；driver 打印截断误导（P3 推翻 R03-003） | 无感 |
| S4b | 「页面没正文所以 ok=False」 | 自问 | content_ok 语义（无散文页= False）；动作结果在 metadata | 无感（记观察） |

## 放弃点

无（演示成功）。

## 主观评分

- 第一印象 4 / 速度 4 / 可信 4 / 报错可读 5 / 等待 4 / 总体 4.3。

## 改进清单

1. 【保持】pending 暂停的头号信号形状（summary 后缀 + next_action 全句）。
2. 【保持】确认措辞点动作层次（R01 家产）。
3. 【保持】drag 第一拒全形。

## 附录

证据：sessions/persona-08/ + p08-run.log。
