# 画像报告 · P05 王倩（产品经理 · L2）· round-01

> 会议间隙盯竞品：增量、摸清列表页、盯变化页。7 发（p05-*）。

## 场景实录

- **S1 增量**：feed since=2026-10-20 → 7 条（5 条带日期 + 2 条无日期），计数如实。
- **S2 建图找详情**：discover /collections →「mapped 12 URL(s) … no content extracted」——**不抓正文是设计，说清楚了**；crawl search='卡片' → **0 条**（第三次内容词过滤 0 命中：P02 的'详情''挑战'加这次——「search 过滤像只认 URL/标题」）；去掉过滤 plain crawl → 5 页（article:1, fallback:4）。「不会用就别用，功能归功能。」
- **S3 变更监控**：check 首发 → 无 changed 字段，note 说「no comparable baseline … this call fetched and recorded one; ask again」；复检 → **changed=true 附 unified diff**（基线 HTML vs 新 HTML 逐行）。✓ 完整闭环。
- **S4 含糊需求**：读 /collections → fetcher_used=**stealthy**（这次走浏览器，6.3s），269 字 content_ok=true。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因 | 严重度 |
|---|---|---|---|---|---|
| 22:02 | S2 search 0 条 | 「'卡片'这个词正文里明明有」 | 10s | crawl search 疑似不匹配正文（候选，P3 裁决） | 骂人边缘 |
| 22:03 | S3 首检无 changed | 「哦，先记基线」 | 10s | 设计（note 讲清楚了） | 无感 |
| 22:04 | S4 浏览器慢 | 「这页怎么 6 秒」 | 6.3s | fetcher 升级 stealthy（未在摘要一句话点名为什么） | 皱眉 |

## 放弃点

无（search 过滤学会绕开：去掉过滤）。

## 主观评分

- 第一印象 4 / 找答案速度 4 / 答案可信度 5 / 报错可读性 4 / 等待感受 4 / 总体 4.2。
- check 的 diff 是亮点：产品经理要的「变了什么」直接给。

## 改进清单

1. 【我想骂人→希望】crawl search 明确说「匹配 URL/标题（不含正文）」或在 0 条时提示「试 path_include/去掉 search」。
2. 【希望】fetcher 升级时摘要一句话说为什么（本页为何走浏览器）。
3. 【希望】check 首检回执把 note 提为 summary 同级可见（这次藏在 note 里，第一眼以为空响应）。

## 附录

证据：persona-05/02（discover 设计说明）、03（search 0 条）、05（基线 note）、06（changed=true+diff）、07（stealthy）。
