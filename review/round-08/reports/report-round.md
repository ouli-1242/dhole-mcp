# 轮报告 · round-08（组合与长链 · web_act 深水轮）· 2026-10-26

> 分支 review/round-08 · 基线 ba4e008 · 服务 9208 + 靶站 8399 · **P1 真实调用 93 发**（10 画像 57 + 补全/补正 p11-p15 14 + 旁支 p80/p81 22），13/13 工具。
> 方法论：每场景先引手册/描述原句 → 真机回放 → 记「合/不合」；深水注入由 L3 承包。

## 候选与裁决去向（P3 裁决）

| # | 现象 | 来源 | 一句话 |
|---|---|---|---|
| R08-001 | frame= 的「URL 前缀」指法解析即炸（当 CSS 解析）——R04-F1 提示句教的第三种指法走不通 | P04 | 教了的路走不通 |
| R08-002 | 跨调用上传：第二发的提交静默落 `received (0 bytes)`，无「上一发装的文件没跟过来」说明（单发组合全通——p12 反证） | P05 | 设计如此，咬人处静默 |
| R08-003 | 重名寄存器（两次 as=同名）静默 last-wins，fill 实填了错误值 | P06 | 写路径上的无声换值 |
| R08-004 | ~~链中失败阅读面静默~~ **REJECT**——summary 全文带「1 of 3 actions did not take effect (click '#nope': …)」，机制早已存在且真发；首报量具只打印前 400 字符 | P09 | 审查推翻首报 |
| R08-005 | frame= 错名 → 错误是 selector-miss 句 + iframe 出路，矛头指错对象（真问题是 frame 引用没解析到） | P09 | 错误现场指错对象 |
| R08-006 | ~~R07-F1 补句没说 session_id~~ **REJECT**——summary 的具名拒绝一直在场（量具没读全）；tools.md:40 契约逐字兑现 | P07 | 审查推翻首报 |

## 成立的组合态（本轮主问题「叠在一起还成立吗」的正面账）

- 同链寄存器 + confirm 闸 →「抄写正确」（p01/p06/p11/p13）。
- 弹窗五形态（alert/dismiss/accept/prompt_text/auto-dismiss）账实全符（p02）。
- 页签四动作 + 最后一枚拒关 + 切不存在带 inventory（p02/p10）。
- session_id 跨调用购物车 → 欢迎回来（p03）。
- 上传单发三跳 → received <文件名> (26 bytes)（p12）。
- 跨 frame 寄存器三件套（fill→property read→fill）→ SAVED:R10-ok（p10）。
- 双 session 隔离、armed_unused 交账、空 actions/坏 selector/tab 越界全部具名（p10/p09/p08）。
- R04-F1 误打提示、R07-F1 寄存器出路句两回归 PASS（p04/p06）。

## 量具与环境注（诚实账）

1. **脚本侧 bug 一串，全部如实记录**：p05 把 /frame/inner 的 #save 记到 /dropdown 页；p07b pin 没带 session_id（由 p15 补正，challenge.log 铁证）；p80/p81 参数形（urls/start_url/url→url、doc_parse 用 file_path）；p80 首跑 sys_state sessions 是 list。补全脚本（pNNb/c）沿用 round 惯例入档。
2. p80 B2 web_research 2 源 1 败（行内各说原因）——如实。
3. p81 B8 web_request POST 被具名写闸拒（method=POST is a write）——行为正确，覆盖算数。
4. P0 预检在册：靶点地图（/dialog 三弹窗、/frame+inner、/upload、/dropdown、/hoverdrag、/challenge/text、/captcha、/login→/account、/checkout）。

## E 类

无新家族样本（E2 未触发）。

## 证据索引

- 台账：sessions/calls.jsonl（93 发明细）；全回执 sessions/persona-NN/；run logs p01..p10+p01b/p05b/p06b/p07b/p07c/p80/p81。
- 铁证旁证：evals/.runtime/challenge.log（token/answer/expected 逐发）。
