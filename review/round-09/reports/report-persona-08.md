# 画像报告 · P08 屠维3 · 精算师 L2 · round-09

> 主题：检索与排序质量。7 发。p08- 前缀。

## 场景实录

- **分级账目**：逐行 fetch_relevance/relevance_score/relevance_raw/engines_consensus 全暴露（'2 of 9 (degraded_pool)' 诚实降池）。
- **mode='psychic'** → `Invalid mode: 'psychic' (auto|neural|find_similar)` 具名；**max_results=51** → 具名 clamp（"outside the supported 1-50 range; returning at most 50"）；**page=1 翻页** OK。
- 摩擦：R09-003 旁证（error 字段裸消息 vs read 族带前缀）。评分 5/5/5/5/5 总体 5。

## 附录
证据：sessions/persona-08/ + p08-run.log。
