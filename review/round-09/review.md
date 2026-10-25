# 对抗审查 · round-09（P3 裁决）· 2026-10-26

立场：先试图推翻每条候选（量具截断教训在册——本次所有判定都读了 receipt 全文/源码/probe）。证据：sessions/ 全回执 + p91 probe + search.py/params.py/recall.py/reranker.py 源码对照。

## 逐条裁决

### R09-001 · min_relevance 裁掉的行数无账 · **REJECT（审查推翻首报）**

- **试图推翻**：账是不是已经在场？——**在**：P06 S5 回执的 `fetch_hint` 全文带「**9 engine result(s) dropped: 9 below min_relevance=0.99 (raw scores here spanned 0.3713-0.9927)**」（search.py:3683-3688 的 `_drop_note` 机制，成功路径也走）。首报的「19 行无账」是把**两轮不同引擎池**的行数直接相减（22 vs 3）得出的假算术——轮间 blocked 引擎集本就浮动；且首报只读了 summary 与行字段、fetch_hint 只打印了前 300 字。
- **结论**：REJECT。教训入册（第三起同根）：**判「无账」前必须读全 fetch_hint/summary/error/next_action 四个账面字段**——本轮三起假候选（含 round-08 两起）全部是「没读全」。
- 附注：`requested/delivered` 字段只对 max_results 的账，这一点描述如实，不构成缺口。

### R09-002 · recall limit 越界静默收下 · **CONFIRM(B)** → fix-R09-F2

- **试图推翻**：①是不是拒绝更合适？——schema 说 1-20；web_search 的同族（max_results=51）走的是 **clamp-and-name**（"max_results=51 is outside the supported 1-50 range; returning at most 50"）；②clamp 是不是有名？——**没有**：recall.py:206 `limit = max(1, min(20, int(limit or 8)))` 静默改写，receipt 无 clamped 字段（limit=21 与 limit=-5 实测均 err=None 无注记）。而行为指南那句绝对句（「clamped-and-named or refused, never silently rewritten」）是 round-07 手册对账时逐字在册的承诺——recall 违反自家手册。
- **结论**：CONFIRM(B)。修法：clamp 抽成带返回注记的形状，envelope 带 `clamped: ["limit -> N (1-20)"]`（未越界不加）。

### R09-003 · search 参数门 error 字段前缀不一致 · **CONFIRM(B)** → fix-R09-F3

- **试图推翻**：diagnosis.code 是否已足？——同一工具的同一类拒绝（第一拒）两种 error 形状并存：query 缺失 = `invalid_request: web_search needs query=…`（前缀在 error 里，summary 呈「invalid request · invalid_request: …」双形式——这是全程的既有惯例，round-05/07/08 的 read/act/request 拒绝全都带前缀）；而 page/freshness/mode/location 的 SecurityError 系（search.py 1287/1298/1523/1813）error **裸消息**，码只在 diagnosis.code。按 `error.startswith("invalid_request:")` 解析的调用方在 search 参数门上会失灵。
- **结论**：CONFIRM(B)-light。修法：四处（page/freshness/mode/location）raise 消息补 `invalid_request: ` 前缀，与全程惯例对齐；diagnosis 码不变。

### R09-004 · location=XX 被收下（描述说 refused） · **REJECT（probe 推翻）**

- 试图推翻：**probe A 实测** `location="usa"` → `Invalid location: 'usa' - an ISO country code ('US') or country-language ('us-en'). There is no 'worldwide' value…`——门是真的、教的形状全。XX 是 ISO 3166-1 alpha-2 的**用户保留段**（XA-XZ），按形状收下与描述的「ISO country code」不矛盾。门形校验而非成员校验是在册实现意图。
- **结论**：REJECT。

### R09-005 · E2 变体八行并排 · **E 样本顺延（非新缺陷）**

- P04 recall 实测 /article 8 个查询串变体各占一行（article、?t=html、?r=1、?r=2、?t=text、?r3v=1、?w3=…-2/-3）——合并键=精确 URL 是在册契约（R02-F5 的 variants 合并只对同 URL 的重抓代际），E2 家族**第六轮样本**入 backlog 计数。

### R09-006 · 默认聚合只回了 yandex 一家 · **环境注（非缺陷）**

- P03 S1 的 6 行全来自 yandex，其余引擎该轮 0 供——外部引擎可用性波动；`families_answered_of_asked`/`engine_detail` 账目字段（R01 家产）在场。P04 同 query 不同轮的引擎集差异（yahoo,yandex,seznam,marginalia vs yandex,seznam,yahoo）同理。

## 裁决汇总

| # | 裁决 | 修法束 |
|---|---|---|
| R09-001 | **REJECT**（fetch_hint 带门槛账全文——首报假算术 + 未读全字段） | — |
| R09-002 | CONFIRM(B) | fix-R09-F1 |
| R09-003 | CONFIRM(B) | fix-R09-F2 |
| R09-004 | REJECT（probe 实证门在） | — |
| R09-005 | E 样本（E2 六现，backlog 顺延） | — |
| R09-006 | 环境注 | — |
