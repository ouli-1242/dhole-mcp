# 画像报告 · P09 库尔班2（DBA · L3 猎手）· round-08

> 主题：组合与长链。5 发。p09- 前缀。深水错误注入。

## 场景实录

- **S1 链中一 action 失败**：fill ok → click #nope **fail（超时，行内具名）** → **select 'JP' 照跑 ok**——失败不中断链；而顶层 error=''，summary/next_action **无一字提及链中有失败**，唯一痕迹在 metadata.actions 行。**→ 候选 R08-004**（失败动作的账只在行内，阅读面静默）。
- **S2 frame 错名（"#nosuch"）**：错误是 selector-miss 句 + iframe 出路（「the selector only sees the TOP document」）——**但真问题是 frame 引用本身没解析到**，提示把矛头指错了对象。**→ 候选 R08-005**（错 frame= 应具名「这个 frame 没找到」而非教 selector 出路）。
- **S3 pin 页引用越界**：read h=关于 ok；fill #note（/about 无此元素）→ 具名超时 miss（正确——页面确实没有）。
- **S4 空 actions** → `actions is required: a non-empty list…` 具名 ✓。

## 摩擦日志
- R08-005（成立）；首报 R08-004 被 P3 推翻（诚实更正）：summary 全文带「1 of 3 actions did not take effect (click '#nope': …)」，是我量具只打印了前 400 字符——两起假候选同根的截断教训之一。

## 主观评分
- 3 / 4 / 5 / 4 / 总体 4（扣在两处错误现场的指向）。

## 附录
证据：sessions/persona-09/ + p09-run.log。
