# round-01 对抗性审查（P3）· 2026-10-25

> 审查员帽对 report-round.md 的 14 条候选逐条攻击：真实性 / 复现（存疑真去补测）/ 归因 / 严重度 / 交叉。
> 补测证据（全部真实调用，22:41 重启后的进程）：本文件「补测」栏。

## 裁决总表

| # | 候选 | verdict | 一句话理由 |
|---|---|---|---|
| R01-001 | feed 行级错误顶层不点名 URL | **CONFIRM（E）** | summary 模板只给计数；修（cheap） |
| R01-002 | crawl search 0 条措辞误导 | **CONFIRM（E）** | search 过滤在 summary 组装**之后**，清空队列后 summary 仍带「crawled 0 page(s) … stopped: max_pages reached」旧账；next_action 那句反而是好的。修（cheap） |
| R01-003 | resume 自荐形状缺 url | **CONFIRM（B）** | crawl.py:830 的 next_action 教 `resume_token='…' to web_crawl` 不带 url，门却要求 url（2557 行的另一处变体带了）。修 |
| R01-004 | 同 URL 多变体不去重 | **CONFIRM（E）** | search 归一化不含跟踪参数、recall 按 URL 直查。跨面修有风险 → backlog |
| R01-005 | IGNORE_ROBOTS 与行为矛盾 | **REJECT（产品）/ CONFIRM（harness）** | 见下「补测 1」。robots.check 对 env 短路（robots.py:497-500）；矛盾源于 ensure_up.ps1 的 env 从未生效。修 harness |
| R01-006 | 挑战页同链取值无解 | **REJECT（能力缺口）/ CONFIRM（可发现性 ×2）** | 见下「补测 2」：`read as=名` + `fill {=名}` 机制存在且端到端通关；真缺口是 ①fill 把 `{CODE}` 这类非寄存器花括号**静默按字面**填入（P07 的 challenge.log 实锤）②pending_approval 报错里的 `{"confirm": true}` 不说放哪层。修两处文案级 |
| R01-007 | 超时预算非硬杀 | **REJECT（设计取舍，记录并接受）** | quality.py:2909 双分支是**有意自白**（R01-042 先例：spill 两读法指向不同动作）；硬杀中断在途下载有损内容完整性，超出最小影响面。接受理由：自白已把「哪种溢出」说清 + budget_advice 给出路 |
| R01-008 | feed 默认预算 1s | **CONFIRM（B）** | 见下「补测 3」。server.py:12978 `timeout=args.get("timeout", 20)` 在缺参时塞 20，方法层当**毫秒**换算 → 1s。根因修 |
| R01-009 | 手册工具计数漂移 | **CONFIRM（B）** | skills/dhole-web/references/start.md:3「eleven tools」+ SKILL.md:8「ten everyday … All eleven」，实际 13 颗。修 |
| R01-010 | drag 首拒不教全形 | **CONFIRM（B，drag 半）/ REJECT（snapshot 半）** | 补测 4：`{from,to}` 首拒报「needs exactly one of selector/index」（不提 to），第二拒才给全形；snapshot 半：P08 **从未调用过** snapshot（回执无此步，报告误记），wire 描述明教编号列表。修 drag 校验顺序 |
| R01-011 | offset summary 说文档不说片 | CONFIRM（E） | 真实但跨形状改动 → backlog |
| R01-012 | crawl 静默开浏览器 | CONFIRM（E） | escalation_path 有数据不显眼 → backlog |
| R01-013 | 钳制消息「89」 | CONFIRM（E） | 剩余秒数被当「请求值」展示；纯文案 → backlog |
| R01-014 | recall 低分出榜 | CONFIRM（E） | 阈值语义问题 → backlog |

## 补测（P3 真实调用）

**补测 1（R01-005）**：ensure_up.ps1 重启后的进程，`web_read http://127.0.0.1:8399/private/secret`（先 cred_defaults allow_private=true）→ `robots_disallowed`。而 robots.py:497 `if ignore or env_disabled(): return Verdict(True, DISABLED, …)` 代码短路无疑。旁证：pwsh 实测 `"$env:FOO_TEST='1'; echo hi"` 展开为 `='1'; echo hi`——ensure_up.ps1 双引号字符串里的 `$env:DHOLE_IGNORE_ROBOTS='1'` 在**外层**被展开成空串，子进程从未收到该变量。P03 的「bypassed (process-wide)」缓存自白是**旧进程**（21:24 带 env 启动、21:47 死亡）铸进磁盘缓存的 metadata，如实描述它自己的抓取条件——两个说法各自诚实，进程不同。
→ 修 harness（ensure_up.ps1 顶层赋值 env），产品无罪。robots 缓存 metadata 的陈旧性可读性记 backlog。

**补测 2（R01-006）**：一发 web_act 链 `[{"read":{"selector":"#challenge","as":"code"}},{"fill":{"selector":"#answer","text":"{=code}"}},{"click":{"selector":"#challenge-btn","confirm":true}}]` → read 抓到 `VADKC`、fill ok、click `submitted:true, submission:POST`、回执页面「挑战通过/抄写正确/**同一条链里读到的就是填进去的那一枚**」、challenge.log `answer==expected, ok:true`。机制存在（actions.py:301 `_REF_RE` + :1995 寄存器）、写在 web_act wire 描述里（server.py:12251）、靶站注释明示这就是场景本意（fixture_site.py:642-653）。P07 的失败路径：猜 `{CODE}`（challenge.log：answer=`{CODE}` 被字面填入，无提示）+ confirm 放顶层（报错 `{"confirm": true}` 不说层次）。
→ 修两处可发现性；能力缺口不存在。

**补测 3（R01-008）**：重放 `feed_read urls=[hnrss.org/frontpage]`（不带 timeout）→ 8.1s、`first try timed out at 1s, retried at 2s`。进程内直调 `fetch_feed(…, timeout=20)` → tls_error（另一族错误，证明 20s 时根本走不到 1s 超时）；`_feed_timeout_s(None)==20`。根因链：wire 分发层缺参塞 `20` → 方法层毫秒换算 → `max(1, 0)=1` 秒。**全量回归注**：manual 轮 S8「hnrss 2/2 成」当时能过，是因为 manual 轮的调用带过 timeout 或时运（网络快过 1s）——本轮两画像（P10）两发全挂。修：分发层传 None，默认回归 20s。

**补测 4（R01-010）**：P08 会话回执 06（drag `{from,to}` 首拒）→ `invalid_request: action 0 'drag' needs exactly one of 'selector' (CSS) or 'index' …`——不提 `to`、不提全形；10（`{selector}` 二拒）→ `must be {selector|index, to} …` 全形。actions.py:962-968 的 to-检查发生在 `_act_target(src)` **之后**。修：drag 先校验整体形状再寻址源端。

## 最终确认队列（B 类，复现稳定，有原始证据）

1. **R01-008** feed_read 缺省预算 1s（B，P1——慢源全灭）
2. **R01-003** resume_token 自荐形状缺 url（B，P2——照抄即错）
3. **R01-009** skill 文案计数漂移（B，P2——手册失信）
4. **R01-006** fill 花括号静默字面 + confirm 层次不明（B，P2——可发现性）
5. **R01-010** drag 首拒不教全形（B，P3）
6. **R01-002** crawl search 0 条 summary 旧账（E→顺手修）
7. **R01-001** feed 顶层 summary 不点名出错 URL（E→顺手修）
8. **R01-005-harness** ensure_up.ps1 env 失效（harness，不进产品队列）

## 记录并接受（写明理由）

- **R01-007** 预算非硬杀：设计取舍（见总表）。
- **R01-004 / R01-011 / R01-012 / R01-013 / R01-014**：E 类 backlog（感知强度、修复风险记 backlog.md）。
- P03 sohu / P07 /private 的 robots 拒：环境（harness env），非产品缺陷；产品行为（env 未设时遵守 robots）正确。
