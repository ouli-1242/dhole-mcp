# 对抗审查 · round-08（P3 裁决）· 2026-10-26

立场：先试图推翻每条候选（量具 bug？在册设计？手册其实写了？），再看成立者改文档还是改行为。证据：sessions/ 全回执 + evals/.runtime/challenge.log + 源码对照（_frame_locator / _validate_actions / browser._pin_take）。

## 逐条裁决

### R08-001 · frame= 的 URL 前缀指法走不通 · **CONFIRM(B)** → fix-R08-F1（与 R08-005 同束）

- **试图推翻**：实现里 URL 前缀支**存在**——`_frame_locator` 的 `(f.url or "").startswith(frame)`（actions.py:1475），代码注释还写了「快照的 frame 行交回的就是 URL，抄那一行的前缀最省事」。但前缀必须**含 scheme**（"http://127.0.0.1:8399/frame/inner"），照浏览器地址栏抄相对路径 "/frame/inner" 直接落进 CSS 分支炸出 `Unexpected token "/"`——错误里**没有 "frame" 字样**，矛头完全指向别处。提示句（R04-F1）只说 "a URL prefix"，没说绝对、没说从快照行抄。
- **结论**：教了的路有一条实测走不通，且走不通的方式是误导性错误。修法：①frame 地址三种拼法全不中且 CSS 校验失败/解析失败时，错误**点名 frame 地址**并教三拼法（绝对 URL 前缀——从快照 frame 行抄）；②提示句补「absolute」限定。行为面新增一个具名错误，其余零变化。

### R08-002 · 跨调用上传静默丢文件 · **CONFIRM(B)** → fix-R08-F4

- **试图推翻**：页面态不跨调用是在册契约（fill 的同侪；challenge token 同理），P05 属于误用？——但 behavior 篇 upload 段**没说**「文件只活在这一次调用的页面上」，回执的 `received (0 bytes)` 也不解释；p12 反证单发组合全通，说明这不是上传损坏而是寿命语义没人教。写路径的静默错误结果（看似成功、实为空文件）不该无声。
- **结论**：CONFIRM(B)-light。修法：upload 动作行带寿命句（files ride THIS page instance — submit within the same call）；behavior 篇 upload 段补一句。链路行为零变化。

### R08-003 · 重名寄存器静默 last-wins · **CONFIRM(B)** → fix-R08-F3

- **试图推翻**：last-wins 是否合法语义？——刷新型重读（read as=v → fill → 再 read as=v 验证）是合理模式，**拒绝重名会杀掉它**；所以语义保留。但第二次静默覆盖第一次、fill 实填了调用方没想要的值（p06：以为填 token 实填页标题）——写路径换值无信号。
- **结论**：CONFIRM(B)-light。修法：重铸同名时该 read 行带 `register_overwritten` 注记（名字 + 被覆盖者）。行为零变化。

### R08-004 · 链中失败在阅读面静默 · **REJECT（审查推翻首报）**

- **试图推翻**：账在 metadata.actions 行内是否已足够？——首报声称 summary/error 无一字提及。
- **推翻成立**：quality.py 的失败小计**早已存在且真发了**——p09#02 的 summary 全文带「**· 1 of 3 actions did not take effect (click '#nope': the selector matched no element…)**」（742 字符里的最后 150 个）；首报量具只打印了前 400 字符。error 字段留给 fetch 层也是既有分工。
- **结论**：REJECT。教训入册：**判「summary 无一字提及」前必须读 summary 全文**——本轮两起假候选（R08-004/R08-006）同根：400/500 字打印截断。「续跑语义」另记：失败不中断链在 receipt 行内可见（select ok 紧随 click fail），用户无受害实证，不动。

### R08-005 · frame 错名指错对象 · **CONFIRM(B)** → fix-R08-F1（与 R08-001 同束）

- **试图推翻**：selector-miss 句 + iframe 出路是否已算教了？——不够：frame="#nosuch" 时真问题是 **frame 引用没解析到任何 <iframe>**，而错误说「selector 只看得见顶层文档」。提示的存在让错误看起来「已经教过了」，实则对象错位（R01-010 同族：第一拒必须点名真因）。
- **结论**：CONFIRM(B)。修法：CSS 分支解析前先数顶层文档匹配的 <iframe>（locator(frame).count()==0 → 即刻具名拒绝：`frame '#nosuch' matched 0 <iframe> element(s) — present: [name=app src=/frame/inner]` + 三拼法）。

### R08-006 · R07-F1 补句缺 session_id 前题 · **REJECT（审查推翻首报）**

- **试图推翻**：behavior 篇其实写了——「keeps THIS page **on that session**…」；p14 是量具漏 session 在先？——量具确实错了（p15 补正后全通），但首报声称「照句做 → 抄写不符**且无解释**」。
- **推翻成立**：**解释一直在场**——p14 两发的 summary 都带具名拒绝「pin=true could not be honored: an anonymous auto session is shared between callers and is recycled when idle… Name a session_id= …」（tools.md:40 的承诺逐字兑现）；首报量具只读了前 500 字符没看到。tools.md 还写明「pin is refused with that reason in the summary, never silently」——契约齐、兑现真。R07-F1 句是对「pin 真成立时」的压缩指针，不构成缺口。
- **结论**：REJECT。教训入册：判「无解释」前先把 summary 读全（round-03 的 100 字截断教训同族）。

## 裁决汇总

| # | 裁决 | 修法束 |
|---|---|---|
| R08-001 | CONFIRM(B) | fix-R08-F1 |
| R08-005 | CONFIRM(B) | fix-R08-F1（同束） |
| R08-002 | CONFIRM(B) | fix-R08-F4 |
| R08-003 | CONFIRM(B) | fix-R08-F3 |
| R08-004 | **REJECT**（失败小计在 summary 全文里真发了——量具 400 字截断） | — |
| R08-006 | **REJECT**（summary 具名拒绝始终在场——量具没读全） | — |
| REJECT | 2（R08-004 失败小计在场 / R08-006 具名拒绝在场——**两起同根：量具打印截断**） | — |
| E 新样本 | 0 | — |

P1 的正面账（组合态成立清单）见 reports/report-round.md；量具侧 4 处脚本 bug 全部如实入档（含 challenge.log 铁证的 p14 现场）。
