# 轮报告 · round-07（手册与实况 · 文档诚实轮）· 2026-10-26

> 分支 review/round-07 · 基线 0702edf · 服务 9207 + 靶站 8399 · **P1 真实调用 57 发**（10 画像 51 发 + p80/p81 旁支 6 发，13/13 工具）+ P3 probe 3 发 + P5 复验 2 发。
> 方法论：每场景先引手册/描述原句 → 真机照做回放 → 记「合/不合」——不合即候选，P3 裁决改文档还是改行为。

## 候选与裁决（P3）

| # | 现象 | 来源 | 裁决 |
|---|---|---|---|
| R07-001 | 寄存器具名拒绝只说「no earlier action stored」，不教「一条链 + pin 保页 + 重读同值」出路（手册先长出 pin 章，错误句没跟上） | P02 | **CONFIRM(B)** → fix-R07-F1（话术层，行为零变化） |

**无 REJECT 需立档**——P02 的跨调用假设源自读漏（手册：text 挑战同链解、pin 解决 IMAGE 案的人在中途），行为本身正确；立为 B 的是拒绝现场的教学密度，不是行为。

## 手册对账总账（本轮主问题：六轮修复后文档面是否与行为同步）

| 对账项 | 结果 |
|---|---|
| start 篇三路线照做（P01/P03） | **合**（read/search/crawl/feed 各路线一次走通，诚实行随行） |
| configuration 篇 env 清单 vs 代码注册表（P04） | **合**：19=19，双向 diff 双零差（P6 以 env_registry.py 主证据重 derive） |
| web_read 参数断言（P05） | **合**：urls=11 全收（10 帽是 check 档专属，描述没谎）；clamp 申报；extraction_type 名单句 |
| 错误消息合法值名单谱（P07/P08） | **合**：force_fetcher/focus_mode/extraction_type/show tokens 名单内真收、名单外真拒 |
| 报错自救路（P09） | **合**：allow_private 自救 + confirm 写闸；撞门腿被共享缓存中和（环境注） |
| 绝对句 ×2（P10） | **合**：cloud metadata 拒绝（probe A 在 fetch 层、豁免在力下实证）；archive 不可关（404 行带 asked/429 原文） |
| 13 颗清单/无旧数残留（P06） | **合** |
| behavior 篇交互示例（P02） | **合**，除 R07-001 的拒绝句缺口 |

## 量具与环境注（诚实账）

1. **p04/p44**：env 清单首试正则抽 0 是量具 bug；全文抽取 19 才是真数。
2. **p09**：「先撞门」腿吃了 7 分钟前的 cache replay——共享缓存躲门（round-04 教训家族：受控场景 cache_ttl=0）。
3. **p03/p10**：watch 错误行原文、cloud metadata fetch 层攻击当轮证据薄/缺——均由 P3 probe（scripts/probe_p3.py → sessions/verify-p3-probe.log）当轮补测，不靠脑补入档。

## E 类

无新样本（E2 本轮未触发；无其他家族复发）。

## 证据索引

- 台账：sessions/calls.jsonl（P1 57 发：p01×6 p02×5 p03×3 p04×3 p05×4 p06×5 p07×6 p08×6 p09×5 p10×8 + p80×4 + p81×2；p90=复验 2 发、p91=probe A 3 发另计）
- 全回执：sessions/persona-NN/ + pNN-run.log ×10
- probe：sessions/verify-p3-probe.log；复验：sessions/verify-r7.log
- run logs 全文：sessions/p01..p10-run.log
