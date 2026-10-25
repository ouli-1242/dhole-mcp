# 画像报告 · P04 林老师（高校研究员 · L3）· round-01

> 晚上写综述：attention survey 线索 → 两源交叉 → PDF 指定页 → arxiv 订阅与召回。7 发（p04-*）。

## 场景实录

### S1 学术检索 + freshness
- 「transformer attention mechanism survey」+ freshness=year → 2.3s，6 行（sogou,baidu,yahoo），rerank=neural；**date_filter 逐字段交代**：requested/sent/exact=false/engines_filtered 列表。「哪几家做了过滤，一眼看穿。」
- 结果里 Springer、多份 PDF、知乎调研——可用。

### S2 研究包（回归#2）
- web_research max_sources=2 timeout=150000 → **8.3s，2 of 2 源成包**（IBM think + PLOS）。150s 大预算分摊后没有任何源被拒——manual 轮修的钳制在线 ✓。

### S3 arxiv PDF 指定页
- pages=1-2 timeout=90000 → 2.7s，7,606 字，page_type=pdf，fetcher=http；**「timeout clamped 89->30 s per attempt (this tier caps one attempt…)」**——钳制披露诚实，但「89」这个数（应该是我的 90 去头尾）看着像内部值外漏，读着别扭。

### S4 订阅与召回
- feed_read 经 r.jina.ai 代理 arxiv RSS → 行级「not a feed: Start tag expected…」并建议「Pass the feed URL itself」——我的错，代理返回的是文本。**换直连 arxiv.org/rss/cs.CL → 正常**（max_items=4 生效）。
- web_recall「attention mechanism survey」→ IBM（新鲜）、博客园（is_stale=true）、arxiv PDF（**同一 URL 两行：一行 stale 一行不 stale**）、Wikipedia——跨会话召回可用，stale 标注在；重复行问题再次出现且两行 stale 标注互相打架。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因 | 严重度 |
|---|---|---|---|---|---|
| 22:01 | S4 代理 RSS | 「代理页不是 feed」 | 8s（换直连） | 我自己 URL 选错；报错给了出路 | 皱眉（自找） |
| 22:02 | S4 recall 重复 | 「这条怎么两行，一行说新一行说旧」 | 0 | recall 未按 URL 去重/合并变体（E，与 P03 同根因候选） | 骂人边缘 |
| 22:01 | S3 timeout「89」 | 「89 是哪来的」 | 0 | 钳制消息用内部精度（E 命名） | 无感 |

## 放弃点

无。

## 主观评分

- 第一印象 5：date_filter 与 engine 明细对学术检索太重要了。
- 找答案速度 5：search→research→PDF 一条龙 15s 内。
- 答案可信度 4：成包源可点名；recall stale 打架扣一分。
- 报错可读性 4：not-a-feed 行级错误自带正解。
- 等待感受 5：重活 8.3s。
- 总体满意 4.6。

## 改进清单（按感知强度）

1. 【希望】recall 按 URL 合并多变体（保留最新 fetched_at，标注 variants=N）。
2. 【希望】timeout 钳制消息展示「请求值→生效值」用调用方的单位（90s→30s/attempt）。
3. 【希望】feed 行级错误继续保留（这次的「Pass the feed URL itself」救了我）。

## 附录

关键证据：persona-04/01（date_filter 明细）、02（research 2/2）、03（PDF 钳制）、05（recall stale 打架）。
