# 轮报告 · round-09（找得到吗 · 检索与排序质量轮）· 2026-10-26

> 分支 review/round-09 · 基线 190be29 · 服务 9209 + 靶站 8399 · **P1 真实调用 74 发**（10 画像 65 + p80 旁支 9），13/13 工具（web_request 以具名「method=GET is a read, not a request」门计入）。
> 方法论：先引手册/描述原句 → 真机回放 → 记「合/不合」；判定全部读 receipt 全文（round-08 截断教训执行）。

## 候选与裁决（P3）

| # | 现象 | 来源 | 裁决 |
|---|---|---|---|
| R09-001 | min_relevance 裁掉 19/22 行，wire 上无账（stats 有 dropped_raw 先例，归一化门槛只进日志） | P06 | **CONFIRM(B)** → fix-R09-F1 |
| R09-002 | recall limit=21/-5 静默 clamp（recall.py:206）——违反自家手册「clamped-and-named or refused, never silently rewritten」；对照 search max_results=51 具名 clamp | P10 | **CONFIRM(B)** → fix-R09-F2 |
| R09-003 | search 参数门 error 字段两种形状并存（query 缺失带 `invalid_request:` 前缀，page/freshness/mode/location 裸消息） | P09/P08 | **CONFIRM(B)** → fix-R09-F3 |
| R09-004 | location=XX 收下（描述说 refused） | probe | **REJECT**——probe 实证 'usa' 被全形教学拒，门是形状校验；XX 是 ISO 用户保留段 |
| R09-005 | E2 变体八行并排 | P04 | E 样本第六轮，backlog 顺延 |
| R09-006 | 默认聚合单引擎供行 | P03 | 环境注（账目字段在场） |

## 检索面正面账（本轮主问题的成立清单）

- **结果行三对账**：search 行标题 vs 落页 metadata.title 逐字一致（P01）。
- **过滤语义**：site= 0 越域 / exclude_sites= 0 漏剔 / 引号短语 / engines= 收窄后来源单一不冒充（P02）。
- **零结果不装满**：`0 results … from none; rerank=merge`（P02）；乱码查询分级降档说清弱相关（P06）。
- **排序账目**：sort=newest 的「no delivered row states a publish day」诚实注；分级逐行 relevance_score/relevance_raw/engines_consensus 全暴露；degraded_pool 如实（P04/P06/P08）。
- **边界与第一拒**：max_results=51 具名 clamp / mode 非法具名 / page=99 具名 / freshness 非法具名 / research max_sources=0 具名 clamp（P08/P09/P05）。
- **坏查询自救**：空查询第一拒带 find_similar 出路；改正形真通（P09）。
- **交叉账**：search→read→recall 对齐；批量 urls=11 家产保持；extraction_collapsed 逐行具名（P07/P10）。

## 量具与环境注（诚实账）

1. 批量 web_read 的行集键名是 `results`（脚本先猜 pages/rows 落空——receipt 全读后更正）。
2. P10 的 /changing 变体召回 0 行是查询词与页内容不匹配（E2 样本已由 P04 捕获），非存储缺失。
3. 外部引擎可用性轮间波动如实记（engine_detail/families_answered_of_asked 在场）。
4. p91 probe 补 location 门证据（REJECT 的实锤）。

## 证据索引

- 台账：sessions/calls.jsonl（74 发）+ pNN-run.log ×11 + probe（persona-91）。
- 全回执：sessions/persona-NN/。
