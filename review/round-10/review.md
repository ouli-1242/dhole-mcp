# 对抗审查 · round-10（P3 裁决）· 2026-10-26

立场：三条线索全部先当缺陷办——读码、读 receipt 全文、跑 probe，试图坐实；坐不实的如实 REJECT。本轮是**第二个零修复轮**（round-05 后首见），证据密度按 §8 自查：四账面字段全读 + 源码对照 + 两次 probe 实证，非漏报。

## 逐条裁决

### R10-001 · crawl 代际对 read 不可见 · **REJECT（设计如此）**

- **试图坐实**：①crawl 是否写 cache？——写：crawl 经 `server.web_read(cache_ttl=…)` 抓页，走统一 finalize 落缓存。②为何 read 9.3 min 旧账？——**两道在册闸**：store 侧质量闸（`_store_ttl` 注释：「壳页 / 挑战页 / 错误 / 空正文一律不进，否则整个 TTL 都在端出坏页——命中路径不还原 error，content_ok 会绿，agent 会信」）——crawl 的入口页 / 是 nav-only 壳，**理应不进**；键侧指纹（behavior guide：「keyed by URL plus the request fingerprint」）——crawl 取 html、read 默认 markdown，各为一代。③probe 复核：html 指纹同参重读 → 仍回源（壳页确实不在缓存里），与①自洽。
- **结论**：设计如此——存壳页才是缺陷（R02-032 的教训反向）。P10 的场景假设（crawl 暖 read 缓存）对壳页不成立。

### R10-002 · attempt_cap_ms=4000 < 显式 timeout=5000 · **REJECT（字段合同如实）**

- **试图坐实**：差 1s 是否静默损耗？——`attempt_cap_ms` 的模型描述原文：「Milliseconds ONE HTTP attempt was actually given on this call, … **what is left of the call when this tier runs** …」——字段申报的就是分层扣减后这一层真实拿到的数（探针/其他层先耗 1s）。P09 的 20000→19000 同款，两例一致。round-02 R02-031 的 1000ms reserve 谱系在册。
- **结论**：字段答的就是"实际给了多少"，不是"被砍了多少"——合同如实。

### R10-003 · recall '轮换' 0 行 · **REJECT（量具查错词）**

- **试图坐实**：召回缺内容？——**probe p92**：/changing 正文无 '轮换'（首灌脚本臆测的词）；用正文实际词 '每次请求' 召回 → **6 行 changing 命中**（含 ?src=a/b/c 变体——E2 形状再现）。
- **结论**：查询词与正文不匹配，量具错；召回本身工作正常。

## 裁决汇总

| # | 裁决 |
|---|---|
| R10-001 | REJECT（设计如此：store 侧壳页闸 + 指纹键合，两道在册闸） |
| R10-002 | REJECT（attempt_cap_ms 字段合同如实） |
| R10-003 | REJECT（量具查错词，probe 实证召回正常） |
| CONFIRM | **0**——第二个零修复轮；§8 自查：非连续（round-09 有 1 B），证据密度在册（四账面全读 + 源码 + 双 probe） |
