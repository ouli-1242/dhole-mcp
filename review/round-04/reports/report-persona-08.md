# 画像报告 · P08 商陆（审计 · L3 猎手）· round-04

> 主题：缓存 provenance 对账 + /changing 必变 + census。7 发。p08- 前缀。

## 场景实录

- **S1/S2 同 URL 三读**：fresh → `· http ·`；再读 → `· cache · cached copy, fetched 0.0 min ago`；ttl=0 → `· http ·`——三发 provenance 逐发对得上，审计要的「这是哪一刻的哪一层」每次都在。
- **S3 /changing 必变 vs 缓存**：ttl=0 真抓「第 4 次请求」；缓存回放**还是「第 4 次」**——回执自白 replayed，缓存回放不是新请求，语义严丝合缝。
- **S4 census**：sys_state 正常（metadata 键在模型里）。

## 摩擦日志
（挑不出刺——这轮的 provenance 组合拳全中）

## 放弃点
无。

## 主观评分
- 第一印象 4 / 速度 5 / 可信 5 / 报错可读 5 / 等待 5 / 总体 4.8。

## 改进清单
1. 【保持】provenance 三件套（fetcher 标 / cached copy 短语 / ttl=0 尊重）。

## 附录
证据：sessions/persona-08/ + p08-run.log。
