# 画像报告 · P03 Sarah（自媒体内容作者 · L1）· round-01

> 晚上找素材写公众号：猫为什么踩奶 → 写一半变卦改写呼噜声。9 发（p03-*）。

## 场景实录

### S1 查资料 + 点进去（web_search + web_read）
- search「猫为什么踩奶」2.5s → 5 行（shenma,sogou,baidu，rerank=neural）。知乎同文出现两行（原链 + ?utm_id=0 跟踪参数变体）——「这两条不是一篇文章吗」。
- 点第一条 sohu → **拒**：robots_disallowed，no request made。「啊？搜索给我的链接读不了？」
- **关键对照**（S5）：本地靶站同刻 metadata.robots=「complied (fetched under: bypassed (DHOLE_IGNORE_ROBOTS=1, process-wide))」→ 环境标志**在**；sohu 仍被拒，next_action 点名「switch source, or re-call with ignore_robots=true」。→ 进程级豁免对**点名 dhole 的 robots 条目**不生效（或优先级被压过）——P3 裁决 B/D。

### S2 卡住求助（sys_guide start）
- 0.04s 回 route map（3730 字，7 节）——「先看路标再看深手册」的路数对 L1 友好。
- 但开头一句「**dhole is eleven tools**」——实际 13 颗。手册计数漂移（候选缺陷）。

### S3 变卦重搜 + 读（web_search + web_read）
- search「猫呼噜声很大 原因」2.7s → 5 行（baidu,bing_global,yandex）。
- 读 moguvet → 4598 字，page_type=article ✓。「这篇能引。」

### S4+S6 挑来源日期（web_read 复读）
- sohu 复读同样拒（行为稳定，非抖动）。
- moguvet 复读命中缓存（1.7min 前），metadata 里 **没有 date/published 字段**——「文里没写日期的话，我就标'检索日'好了」。放弃深挖（真人让步）。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因 | 严重度 |
|---|---|---|---|---|---|
| 21:54 | S1 sohu 拒 | 「搜索结果居然读不了」 | 30s（换了源） | robots 点名条目 × 进程豁免的优先级（B/D 待裁） | 骂人边缘 |
| 21:54 | S1 重复行 | 「这两条不是一篇吗」 | 0 | URL 归一化去重缺失（E） | 皱眉 |
| 21:56 | S2 手册计数 | 「说好的 11 颗是几个意思」 | 0 | start.md 未跟 13 颗同步（B/E 待裁） | 皱眉 |
| 21:58 | S6 无日期 | 「还得自己翻正文找」 | 0 | metadata 不挑日期（该页无日期可挑，D/E） | 无感 |

## 放弃点

S6 日期：**主动放弃**（「算了，标检索日」）——不深究，符合 L1 耐心预算。

## 主观评分

- 第一印象 3.5：开门就吃一张 robots 拒条，虽然话是好人话。
- 找答案速度 4：两次搜索到可引正文，全程 <1min。
- 答案可信度 4：「do NOT cite it」这句反而加分。
- 报错可读性 4：拒条说清了「没发请求、换源或 ignore_robots=true」。
- 等待感受 5：搜索 2.5s、正文 0.5s。
- 总体满意 4.0。

## 能力边界遭遇

robots 门以「为 dhole 点名」的方式挡住真实站点——工具诚实（没编内容），出路给了两条，L1 也能看懂。但「搜索结果读不了」这个组合拳打在第一击。

## 改进清单（按感知强度）

1. 【我想骂人→希望】搜索结果里直接标「此站 robots 不放行」之类徽标，别让我点进去才挨拒。
2. 【希望】同文多行（跟踪参数）去重。
3. 【希望】sys_guide 的工具计数与 tools/list 同源。
4. 【希望】article 类页面 metadata 尽量挑出 date。

## 附录

关键证据：persona-03/01（重复行）、02（sohu 拒）、07（fixture robots 对照）、08（sohu 复读拒）、09（moguvet 无日期字段）、03（sys_guide eleven）。
