# 对抗审查 · round-11（P3 裁决）· 2026-10-26

立场：每条线索先当缺陷办（读码 + receipt 全文 + probe），坐不实的如实 REJECT。round-08..10 三轮「量具截断」教训执行：判定前读全四账面字段。

## 逐条裁决

### R11-001 · feed_read max_items 负数静默按「全部」处理 · **CONFIRM(B)** → fix-R11-F1

- **试图推翻**：①0 = all 是描述在册的（"default 20; 0 = all"）——负数是不是同族合法？——描述只许 0 与正数；负数 `-3` 实测静默返回全量 8 条（feed.py:482 `if max_items <= 0: return`），无 clamped、无注记、无拒绝。②行为指南绝对句：「An out-of-range number is either clamped-and-named, or refused — never silently rewritten」；R09-002（recall limit）同族已修成具名 clamp。负数没有有意义的正域目标（clamp 到几都是猜），**拒绝**更诚实。
- **结论**：CONFIRM(B)-light。修法：`max_items < 0` → 具名拒绝（`invalid_request: max_items must be ≥ 0 (0 = all), got -3`）；0 的在册语义（=all）与正数路径零变化。

### R11-002 · doc_parse 伪 PDF 两副面孔 · **REJECT（两码各司其职）**

- `%PDF` 魔数在但结构坏 → `pdf_open_failed: No /Root object!`；无魔数 → `not_a_pdf: body does not start with %PDF`（probe C 实证）。两个具名码各自准确——「is this really a PDF」句教给前者，魔数事实教给后者。非缺陷。

### R11-003 · web_request POST 写闸「卡死」（首报） · **REJECT（量具没读 next_action）**

- P03 两发 POST 都被拒是**脚本条件写错**（在 error 里找 "confirm"，而修法句住在 next_action：「Nothing was sent. Ask the user, then re-send the same call with **confirm=true**. HEAD probes need no confirm.」）。probe A 实证：confirm=true → 200 OK，且 session 会话复查 /account = 「欢迎回来」——request→session→act 交叉全通。合同齐、通路真。

### R11-004 · since='1d' 报错 · **REJECT（第一拒教形）**

- 拒绝句带两种合法格式（ISO-8601 / RFC-822）——feed 的 since 是**精确日期**语义（search 的相对 span 是另一扇门），教得对。量具误用了 search 的语法。

### R11-005 · sys_guide 未知 topic 无 error · **REJECT（教学住在正文）**

- topic='nope' → 非错误路径：_raw 正文列出全部 topic 及字数（「unknown topic 'nope'. Known topics…」）——这颗的门是内容教学不是拒绝门。量具的「出路=N」启发式只看了 error/next_action。

### R11-006 · 404 页 discover 报「mapped 1 URL(s)」 · **观察（不入档）**

- 种子 URL 自身计入 mapped 计数、行集为空（不谎称有链接）。措辞可再准，但无假承诺——记报告观察项，不立案。

## 裁决汇总

| # | 裁决 |
|---|---|
| R11-001 | CONFIRM(B) → fix-R11-F1 |
| R11-002 | REJECT（两码各司其职） |
| R11-003 | REJECT（量具没读 next_action——教训家族再现） |
| R11-004 | REJECT（第一拒教形） |
| R11-005 | REJECT（内容教学非错误门） |
| R11-006 | 观察项 |
