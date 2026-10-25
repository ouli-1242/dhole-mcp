# 画像报告 · P10 桑无咎（架构师 · L3 猎手）· round-07

> 主题：手册与实况。8 发 + p80/p81 旁支 6 发（补齐 13/13 工具）。p10-/p80-/p81- 前缀。绝对句攻击。

## 场景实录

- **S1 『never opens cloud metadata』**：三拼法存豁免（true / ["169.254.169.254"] / "169.254.169.254"）全收、unset 全净——**但 fetch 层攻击当轮未发**（覆盖缺口，量具侧）。**P3 probe A 补测**：allow_private=true 在力下 web_read `http://169.254.169.254/latest/meta-data/` → `invalid_request: URL targets internal cloud metadata endpoint (169.254.169.254)`——**绝对句在 fetch 层守得住，且具名**。
- **S2 『No parameter disables it』（archive 档）**：web_read 404 页 → summary 带「**the archive.org fallback was asked and the availability API answered HTTP 429** - the second opinion got nothing, so the failure above is the origin's own」+ next_action 反分页指引 + ssrf_recheck 行自证「cloud metadata are still refused」。**合**。
- **旁支（p80/p81）**：web_discover / web_research / doc_parse / web_request 各一发真用，13/13 工具覆盖达成。

## 摩擦日志
（无产品候选；两绝对句一实证一补测后实证。）

## 主观评分
- 5 / 5 / 5 / 总体 5。

## 附录
证据：sessions/persona-10/ persona-80/ persona-81/ + p10-run.log + sessions/verify-p3-probe.log（probe A）。
