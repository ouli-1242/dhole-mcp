# 画像报告 · P04 黎照2（移动端开发 · L2）· round-08

> 主题：组合与长链。5 发。p04- 前缀。iframe 表单组合（frame= 三指法 + 误打提示回归）。

## 场景实录

- **S1 frame=name（"app"）**：fill/click/read 全 ok → #saved=SAVED:来自外层的问候 ✓。
- **S2 frame=CSS（"#app"）**：全 ok → SAVED:css 指法 ✓。
- **S3 frame=URL 前缀（"/frame/inner"）**：**✗ 两步全败**：`Locator.fill: Unexpected token "/" while parsing css selector "/frame/inner"`——URL 前缀被当 CSS 解析。**→ 候选 R08-001**（R04-F1 提示句教的第三种指法走不通）。
- **S4 误打不带 frame（R04-F1 回归）**：10s 超时后错误带 iframe 出路句全文（this page hosts 1 <iframe>…）——**回归 PASS**（元素在顶层文档无处可寻时提示照发）。

## 摩擦日志
- R08-001：提示句三指法，实测只通两种——名与 CSS；URL 前缀是死路（解析层即炸）。

## 主观评分
- 4 / 4 / 5 / 总体 4.3（扣在第三指法）。

## 附录
证据：sessions/persona-04/ + p04-run.log。
