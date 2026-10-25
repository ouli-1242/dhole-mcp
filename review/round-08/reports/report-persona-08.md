# 画像报告 · P08 桑无咎2（医疗器械注册 · L1）· round-08

> 主题：组合与长链。5 发。p08- 前缀。长表单逐字段照手册。

## 场景实录

- **S1 照手册读 start 篇** ✓。
- **S2 结算长表单**：qty=abc 页面收下（fill 层不判业务——如实）；正形 item=电子血压计 + qty=3 + click confirm → submitted POST ✓（闸家产 PASS）。
- **S3 坏 selector（"# Qty" 带空格）**：`Unexpected token "#" while parsing css selector`——CSS 语法错误的第一拒（Playwright 原文直传，具名可诊断）。

## 摩擦日志
（无候选。L1 照手册不迷路。）

## 主观评分
- 5 / 5 / 5 / 总体 5。

## 附录
证据：sessions/persona-08/ + p08-run.log。
