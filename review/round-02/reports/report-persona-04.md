# 画像报告 · P04 覃川（数据工程师 · L3 猎手 #2）· round-02

> 替手写爬虫探路：nested 结构化、批量、offset 对账、resume 照抄。8 发（p04-*）。

## 场景实录

- **S1 nested 首撞**：`.work-card a` 是我瞎猜的 → content_ok=false +「matched 0 elements on this page」具名。「拒得清楚，但 HTTP 档没有『帮我看看这页有什么选择器』的路」（快照是浏览器档的）。
- **S2b resume 照抄（回归#4 ✓）**：/collections max_pages=2 停下 → next_action 自荐 **`web_crawl(url='http://127.0.0.1:8399/collections', resume_token='b8544897cb1a:0')`** → **逐字照抄 → 「plan resumed at 1-10 of 10: 10 ok, 0 with error」**，10 条队列全送达不重抓。R01-003 修复在真实编排里站住了。
- **S3 批量 12/13**：两发全 200、行行 content_ok；行键第一格还是 `results`（E1 跨轮复发，未修如实记录）；13 条没撞上限（cap 在 50）。「12 个页面一发拿齐。」
- **S4 offset 对账 + 新候选（R02-004）**：chunk1/chunk2 内容接续精确（第 17→18 段），next_offset 链条对；但 summary 自白「**one HTTP attempt is bound by the learned ceiling for this host (it answers pages fast, so the tier gives it 5000ms, not your 30000ms)**」——调用方默认预算 30s 被 per-host 学习值压到 5s，句子把它当卖点写。**显式预算vs学习值的优先级**要看代码裁决（若显式 30000 也被压，就是 R02-041 当年修的那颗的回归）。E3（summary 说整页 7.0KB 不是本片 800）跨轮复发。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因 | 严重度 |
|---|---|---|---|---|---|
| 00:26 | S1 选择器瞎猜 | 「总得让我看看页里有什么类名」 | 一次 | HTTP 档无结构速览（E 类） | 皱眉 |
| 00:28 | S4 learned ceiling | 「我的 30s 怎么变 5s 了还说得好听」 | 0 | 学习值压默认预算，披露了但语义可疑（候选 R02-004） | 骂人边缘 |
| 00:29 | S3 行键 | 「又要点进 results」 | 0 | E1（跨轮） | 皱眉 |

## 放弃点

无（L3 全部追到底；nested 换对选择器的事留待 P08 的快照路线）。

## 主观评分

- 第一印象 4 / 找答案速度 5 / 答案可信度 4（offset 对账精确；learned ceiling 扣分）/ 报错可读性 5 / 等待感受 5 / 总体 4.4。

## 改进清单

1. 【我想骂人→希望】learned ceiling 别压调用方的预算（显式或默认都算）：快是它的运气，不是我的合同。
2. 【希望】HTTP 档给一个「这页有哪些 id/class 可以选」的轻量速览（或报错里带前 3 个候选）。
3. 【保持】resume 自荐照抄能跑；批量 50 上限内无惊吓。

## 附录

证据：sessions/persona-04/01-06 + p04b 01-02（resume 照抄全过）。
