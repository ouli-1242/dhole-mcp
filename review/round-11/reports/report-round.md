# 轮报告 · round-11（冷面深水 · doc_parse/discover/request/feed_read）· 2026-10-26

> 分支 review/round-11 · 基线 2ab7b5e · 服务 9211 + 靶站 8399 · **P1 真实调用 63 发**（10 画像 53 + p80 旁支 9 + probe 1），13/13 工具。

## 候选与裁决（P3）

| # | 现象 | 来源 | 裁决 |
|---|---|---|---|
| R11-001 | feed_read max_items 负数静默按「全部」（描述只许 0=all） | P10 | **CONFIRM(B)** → fix-R11-F1（拒绝；0 语义不动） |
| R11-002 | 伪 PDF 两副面孔 | P01 | REJECT（两码各司其职：pdf_open_failed / not_a_pdf） |
| R11-003 | POST 写闸卡死 | P03 | REJECT（修法句在 next_action——probe 实证 confirm=true 全通） |
| R11-004 | since='1d' 报错 | P04 | REJECT（第一拒教两日期格式；feed 是精确日期语义） |
| R11-005 | sys_guide 未知 topic 无 error | P07 | REJECT（内容教学非错误门） |
| R11-006 | 404 页 discover 的 mapped 计数含种子 | P02 | 观察项（无假承诺，措辞可再准） |

## 冷面四颗正面账

- **doc_parse**：GBK 保真/伪 PDF 两具名码/路径不存在教 cwd= 出路。
- **web_discover**：地图→read 可点、断行不谎称、第一拒点名陌生参数。
- **web_request**：写闸 confirm 形状齐（next_action 教）→ POST 200 → session 交叉「欢迎回来」；HEAD 头披露零 Cookie 泄漏。
- **feed_read**：三源混发逐行分账（404/connection_refused 具名）、基本账目整源口径、since 教日期格式。
- **第一拒全集抽样**：12 发 11 颗带码前缀+出路，sys_guide 内容教学（设计）。

## 量具与环境注

1. P03 的 confirm 在 next_action 不在 error（脚本条件写错——probe 补正）。
2. P02/P10 各一次参数名猜错（max_depth/limit）——都被第一拒点名，如实记。
3. sys_guide「出路=N」是量具启发式误报（教学在正文）。

## 证据索引
台账 sessions/calls.jsonl（63 发）+ pNN-run.log ×11 + probe（persona-91）。
