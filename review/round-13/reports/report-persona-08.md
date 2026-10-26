# P08 温故 · 水质检验员 L1 —— 链条⑧feed since 增量→混发分账→parse xlsx→read

## 画像与卡面

水质检验员，L1。情境：每天看同一个订阅源有没有新水样通告，再把一份 Excel 化验单读成文字。
目标：只看 10-24 之后的新条目；两源混发时坏源不要拖垮好源；xlsx 单表读得出「1号井」。
验收：since 账可读；混发逐行分账；parse→read 接上；L1 零迷路。

## 读数（5 发：cred + feed×2 + parse + read）

| 发 | 工具 | 读数 |
|---|---|---|
| S1 | feed_read /feed.xml since=2026-10-24 max_items=5 | 5 条返回、newest first；行内 `since: 2026-10-24T00:00:00+00:00`、`items_older_than_since: 3`、`items_without_date: 2`——3 条更老的被挡在 since 外，2 条无日期按文档保留 |
| S2 | feed_read 好源 + /feed-404.xml 混发 max_items=2 | 2 行：好源 2 条；坏源 `http_error_404: server returned error status`，逐行分账 |
| S3 | doc_parse water_quality.xlsx | 200 · 83B · parse；content 字符串列表含 **1号井**（单表） |
| S4 | web_read /article | 200 · 387B |

## 合 · 不合

- **合**：since 的账不是一句「过滤了」——它把「挡掉几条、无日期几条」都数出来；无日期条目
  按文档保留，没有静默丢。混发时坏源不拖垮整包，逐行 error 具名。xlsx 单表 parse 直接可读。
- **不合**：无。S1 的 5 条里 3 条是 0/1/2 天前，正好在 since 之后；读数与行内计数一致。

## 摩擦清单

1. L1 用户看不懂 `items_older_than_since` 这种字段名——但他只读 summary 也能得到「5 条新的」；
   字段名对机器友好，对人不算友好（既有形状）。
2. feed 无缓存（summary 明说），每看一次都真实抓取——来源不可达时只能靠逐行 error 判断。

## 判定

- feed since 增量 + 混发逐行分账 PASS（r12 必测 5 前半 + r11 必测 4 合并回访）。
- parse→read 链 PASS（xlsx 单表；GBK 保真本轮未重测，r11 家产在册）。

## 主观评分

5/5 —— 数字自洽、坏源不连坐，L1 也能照着 summary 说话。
