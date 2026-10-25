# 对抗审查 · round-07（P3 裁决）· 2026-10-26

立场：先试图推翻 P2 的每条候选，再试图推翻「手册对账 PASS」本身。证据：sessions/ 全回执 + verify-p3-probe.log。

## 候选裁决

### R07-001 · 寄存器拒绝缺 pin 出路 · **CONFIRM(B)** → fix-R07-F1

- **试图推翻**：①手册有没有许诺跨调用寄存器？——没有：tools.md 原文 text 挑战同链解、pin 解决 IMAGE 案（人在中途）；行为零问题。②是不是 P02 读漏？——是，但拒绝现场只说「no earlier action stored that name (no {"read": ...} action runs before this one)」，对「以为 pin 保寄存器」的用户没有一条出路；手册 737 字符 behavior 篇也不讲 pin。错误消息是该轮主题（文档诚实）的直接考题：**拒绝句落后于手册已长出的概念**。③available: [...] 分支有没有同样问题？——没有：有可用寄存器时用户能看到名字，缺的只是「根本没读过」现场的半个前题。
- **结论**：话术层 CONFIRM(B)；修法=拒绝句补「registers live for this one call: mint … as=… before the fill; a pinned page (pin=true) holds the page open, so a re-read in the next call's chain returns the same token」，available 分支不加。行为零变化。

### 手册对账 PASS 的对抗复核

- **19=19**：不信 P1 脚本，P6 用代码 env_registry.py 重新双向 diff——registry 19 名、manual 19 名、双零差。PASS 成立。
- **urls=11**：回执 `11 URLs asked · 11 answered`；web_read 描述原文只说 check 档 caps——没有「最多 10」的 web_read 断言可推翻。PASS 成立（描述如实）。
- **绝对句「never opens cloud metadata」**：P1 只在豁免层打（三拼法存/删），fetch 层没问——不算实证。**probe A 补测**：allow_private=true 在力下 fetch 169.254.169.254 → `invalid_request: URL targets internal cloud metadata endpoint (169.254.169.254)`。PASS 成立（且具名）。
- **绝对句「No parameter disables it」**：P10 404 行 summary 带原文「the archive.org fallback was asked and the availability API answered HTTP 429」——archive 真被问且不可关。PASS 成立。
- **P09 自救路**：撞门腿被 cache 中和——若据此报「私网门失效」即为误报（缓存回执如实申报 fetched 7.0 min ago，不是伪造成功）；自救腿与写闸真测过。PASS 成立，环境注在册。
- **P03 watch exit 2**：若据此报「watch 坏了」即为误报——exit 2 + 1 error 正是 R04-F2 语义（watch 进程 env 无 flag → 具名拒绝 + 出路句）；probe B 抓到错误行全文。PASS 成立。

## 裁决汇总

| 项 | 裁决 |
|---|---|
| R07-001 | CONFIRM(B) → fix-R07-F1 |
| 手册对账 8 项 | PASS（对抗复核后维持） |
| REJECT | 0（无需立档——无首报被推翻） |
| E 新样本 | 0 |
