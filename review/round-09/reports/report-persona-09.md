# 画像报告 · P09 库尔班3 · 税务师 L1 · round-09

> 主题：检索与排序质量。7 发。p09- 前缀。

## 场景实录

- **空查询** → `invalid_request: web_search needs query=…`（带码前缀 + find_similar 出路）；**超长 2000 字**：照搜（6 行）不拒不炸；**location='usa'**（probe）→ 全形教学拒。
- **page=99** → `Invalid page: 99 (0-10)`——具名但 **error 裸消息无码前缀** → **R09-003 候选**。
- **自救重试**：改正形 3 high 真通。
- 评分 5/4/5/5 总体 4.8。

## 附录
证据：sessions/persona-09/ + p09-run.log。
