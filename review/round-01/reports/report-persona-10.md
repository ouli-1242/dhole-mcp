# 画像报告 · P10 Helen（市场增长 · L2）· round-01

> 投放周报：搜索只信 baidu+sogou、只要本周；又想顺手看全部引擎；召回上周读过的页。6 发（p10-*）。

## 场景实录

- **S1 收窄搜索（回归#3）**：search `engines=[baidu,sogou] freshness=week` → 5 行全部来自 baidu/sogou（engines_used 如实），日期档 10-18～10-22 都在窗口内；date_filter 字段自白「sogou 没有发日期过滤，窗口是 ASKED 不是 rows satisfy，自己看日期」——**不装精确**。回归#3（降级池如实）通过。
- **S2 既要还要**：一口气点名 11 个引擎 → 没有报错、也没有默默吞掉：`engines_used=[sogou,baidu,yandex]`、`engine_preempted=[yahoo,bing,seznam,bing_global]`（多样性 quorum 主动取消，不是失败）、`engine_blocked` 4 个带 Circuit/冷却倒计时（so360 441s…），`families_answered_of_asked: "3 of 8 (7 could answer)"`。next_action 把「为什么只有 3 个」「怎么等池子回满」全说清了。「比我要的少，但每一发动机都交代了」。
- **S3 召回**：web_recall「内容营销 增长」→ 只回 1 条（靶站 /longaside，relevance 0.0642，terms_found 只有「内容」，2/3 词缺失），dropped_low_relevance=6。作为「上周读过的页」召回完全不相干——共享库里没有她的历史，这条是别的画像读的靶站长文。「这不算召回我的东西。」（低分命中仍出榜，见摩擦）
- **S3 批量读**：example.com + mozilla.org 双 200；mozilla 正文里混着 `### ` 空标题 + 原样缩进导航行（抽取噪声，轻微）。
- **S3 订阅链**：feed_read hnrss.org → **1s 超时 → 重试 2s 又超时**。回执诚实（retryable/recover_with），但 note 自称「timed out again, so waiting longer is not the fix here」——hnrss 是出了名的慢而活着，**等更久恰恰就是解法**（→ 候选 R01-008）。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因 | 严重度 |
|---|---|---|---|---|---|
| 22:24 | S3 召回 1 条低分 | 「这不算我的东西」 | 1.2s | 无她本人的读史；0.06 仍过出榜阈值 | 皱眉 |
| 22:25 | S3 hnrss 超时×2 | 「源头明明是好的」 | 8.1s | 内置梯子 1s→2s 太紧；note 误诊「等更久没用」（候选 R01-008） | 骂人边缘 |
| 22:25 | mozilla 噪声行 | 「中间夹的这是什么」 | 0 | 抽取器对空标题/缩进导航不敏感 | 皱眉 |

## 放弃点

无（feed 失败后她改用搜索结果凑周报，链路没断）。

## 主观评分

- 第一印象 4 / 找答案速度 4 / 答案可信度 4（日期档不装精确加分）/ 报错可读性 5（11 引擎的去向逐个点名）/ 等待感受 4 / 总体 4.2。

## 改进清单

1. 【我想骂人→希望】feed 慢源别用 1s/2s 的梯子宣判死刑；「等更久没用」这句话只在真断网时才说。
2. 【希望】recall 低分命中（<0.1 且 terms 命中过半以下）宁可说「没找到你的历史」也别凑一条。
3. 【希望】正文抽取把空标题行和缩进导航残片清掉。

## 附录

证据：persona-10/01（收窄+日期档自白）、02（11 引擎去向全清）、04（低分召回）、06（hnrss 超时+误诊 note）。
