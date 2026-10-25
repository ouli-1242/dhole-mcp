# 轮报告 · round-10（预算与时间 · timeout/ceiling/cache-TTL 全工具对账）· 2026-10-26

> 分支 review/round-10 · 基线 5ad954f · 服务 9210 + 靶站 8399 · **P1 真实调用 57 发**（10 画像 48 + p80 旁支 9）+ probe 2 发（p91/p92），13/13 工具。
> 方法论：先引手册/描述原句 → 真机回放 → 记「合/不合」；判定前读全四账面字段（三轮教训执行）。

## 候选与裁决（P3）—— **0 CONFIRM（第二个零修复轮）**

| # | 现象 | 裁决 | 推翻证据 |
|---|---|---|---|
| R10-001 | crawl 抓过的页对 web_read 不可见（read replay 9.3 min 旧账，html 指纹 probe 也 miss） | **REJECT（设计如此）** | 双闸齐明：①store 侧质量闸不存壳页（/ 是 nav-only——_store_ttl 在册注释"壳页…一律不进，否则整个 TTL 都在端出坏页"）；②cache 键=URL+请求指纹（behavior guide 在册），html/markdown 各为一代 |
| R10-002 | attempt_cap_ms=4000 < 显式 timeout=5000（差 1s） | **REJECT（字段合同如实）** | attempt_cap_ms 模型描述："what is left of the call when this tier runs"——分层扣减后的真实所得，P09 的 20000→19000 同款 |
| R10-003 | recall '轮换' 0 行（疑似召回缺口） | **REJECT（量具查错词）** | probe：正文无 '轮换'；用正文词 '每次请求' → 6 changing 行命中 |

## 预算与时间正面账（本轮主问题的成立清单）

- **显式 timeout= 是合同**：learned 句让位不出现；缺省时 learned 5000ms 具名申报（P01/P03）。
- **边界第一拒具名**：timeout=0（must be positive）/999999（exceeds maximum 120000）/cache_ttl 类型错（教形）/timeout=-100（P02/P09）。
- **具名 clamp 五连**：research timeout→300000ms、crawl max_pages 50->40 与 0->1、act wait 120000->30000、（round-09 的 recall clamped 同族）——全程"钳位要出口"纪律成立。
- **TTL 代际账**：首灌/代内 replay/ttl=0 强制回源/ttl=1 过期真回源，fetched_at 与年岁逐发如实（P04）。
- **replay 诚实形状**：三连 cache 命中零 attempt 伪造、年岁递增（P07——round-02 W3 教验保持）。
- **预算分摊**：research 紧预算下慢源被掐逐行具名（1 of 2 / 0 of 1）（P05）。
- **crawl stopped 三现场**：max_pages/max_depth/池尽原因各说各话（P08）。
- **批量每页账**：6 页逐行 status/content_ok，nav 页如实 false（P06）。

## E 类

无新样本（/changing 变体族再现属 E2 六现既有）。

## 量具与环境注（诚实账）

1. sys_state 的 cache 是 dict（脚本先猜 list——receipt 更正）。
2. P04/P10 的 recall 0 行均为查询词与页内容不匹配（p92 probe 用正文词命中 6 行）——查询词采样前先读正文。
3. attempt_cap 差值（5000→4000）初看像缺陷，读 attempt_cap_ms 字段描述后定性为分层扣减的如实申报。

## 证据索引

- 台账：sessions/calls.jsonl（57 发）+ pNN-run.log ×11 + probe（persona-91/92）。
- 全回执：sessions/persona-NN/。
