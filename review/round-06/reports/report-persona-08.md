# 画像报告 · P08 荆无涯（爬虫 · L3 猎手）· round-06

> 主题：css_selector 深水 + html 原始性。4 发。

## 场景实录

- **S1 属性选择器** `a[href^='/work/']` → **正好 11 行作品链接（59 字符）**——但 `content_ok: false`（R06-002 的第二现场：命中碎片被散文尺误判）。
- **S2 html 档** → 原始标记原样（<!doctype html> 开头，字节级保真）。
- **S3 伪类** `.card::first-line` → 第一拒且**把因果说完**：「…(ExpressionError: Pseudo-elements are not supported.) - **it would have been ignored and the whole page returned**」——这句反证把 R06-001 的静默行为坐实了：引擎自己知道 0 匹配会回全页。

## 摩擦日志
（S3 的「would have been ignored and the whole page returned」是 R06-001 最锋利的旁证——同一个引擎，伪类处把后果说了，0 匹配处没说）

## 主观评分
- 4 / 4 / 4 / 4 / 4 / 总体 4.0（S1 的 content_ok=false 拖可信）。

## 附录
证据：sessions/persona-08/ + p08-run.log。
