# 跨轮缺陷 taxonomy 与高发区

> 每轮更新；是下一轮画像与场景设计的输入。种子 = manual-2026-10-25 摸底轮 + loop/ref/env-facts.md。

## 高发区（按观察频次）

| 区 | 现象 | 状态 | 来源 |
|---|---|---|---|
| web_request × 会话 | POST /login 的 Set-Cookie 不落任何后续可用的 jar；web_request 不收 session_id | **已修**（3cf035e 内，manual 轮 P2）| manual report / probe_cookie_jar.py |
| web_research 预算 | timeout 分摊可超 web_read 120s 上游校验 → 整源被拒而非钳制 | **已修**（3cf035e 内，manual 轮 P3）| manual report |
| 协议版本 | mcp 2.2.0 客户端（要 2026-07-28）连不上 dhole（最高 2025-11-25）；原生 MCP SDK 宿主全灭 | **待裁决**（B/D/上游）| env-facts §7.1 |
| 代理档位 | httpx 系读注册表系统代理、primp 系不读 → 同进程不同出口 | **待验证** | env-facts §7.2 |
| 引擎环境 | brave circuit open、shenma/ddg blocked（CN 线）→ 降级池如实上脸（families_answered 3 of 9） | 环境（C），非缺陷 | manual report |
| 批量读行键 | 键名是 results 不是 pages——调用方脚本易写错 | D（schema 如实），观察 | manual report |

## round-01 新增/复发（P3 裁决后）

| 区 | 现象 | 状态 | 来源 |
|---|---|---|---|
| feed_read 缺省预算 | wire 分发缺参塞 20 被当毫秒 → 全部不带 timeout 的调用顶着 1s 预算 | **已修**（R01-008，fix-R01-F1）| P10 hnrss 两发全挂 |
| resume 自荐形状 | next_action 教的 resume_token 调用缺 url，照抄撞门 | **已修**（R01-003，fix-R01-F2）| P02 |
| skill 文案计数 | start/SKILL 写死 eleven/ten，实际 13 颗 | **已修**（R01-009，fix-R01-F3）| P03 |
| 挑战页可发现性 | fill 猜错引用语法静默字面入站；confirm 层次不明 | **已修**（R01-006，fix-R01-F4；机制 read as=+fill{=} 本就可用）| P07 |
| drag 形状门 | 陌生键先被 _act_target 半句拦截，第一拒不教全形 | **已修**（R01-010，fix-R01-F5）| P08 |
| crawl search 空手 | 清空后 summary 借 max_pages 措辞 | **已修**（R01-002，fix-R01-F5）| P02/P05 |
| feed 顶层错误可见性 | rows errored 不点名 URL | **已修**（R01-001，fix-R01-F5）| P01 |
| harness env 传递 | PowerShell 双引号串内 $env: 赋值被外层展开成空 | **已修**（ensure_up.ps1 顶层赋值；产品无罪）| R01-005 |
| 超时预算非硬杀 | 溢出过 grace 时自白「did not stop when told」 | wontfix(evidence)：设计取舍，自白+budget_advice 是设计内出路 | P09（R01-007）|
| 同 URL 变体 / offset 语义 / 静默浏览器 / 钳制精度 / 低分召回 | E 类 | backlog E2–E6 | 多画像 |

## round-02 新增/复发（P3 裁决后）

| 区 | 现象 | 状态 | 来源 |
|---|---|---|---|
| archive 快照形状 | availability API 报有快照，抓回的是 archive 自己的 capture 索引页，仍盖 content_ok=true | **已修**（R02-002，fix-R02-F2）| P02 EUR-Lex PDF |
| 401/403 杠杆 | diagnosis.recover_with 空白卷而 next_action 教 cred_defaults（机器孪生与散文打架）；工具描述「hard block」超承诺 | **已修**（R02-003，fix-R02-F3；401 不走 archive 属设计，描述修至一致）| P02 WSJ |
| learned ceiling | 对见过的域无视 default_ms，显式 timeout 同样被压（注释自认 "caller's budget is the contract" 与实现不符）；教学句逐发重复 | **已修**（R02-004，fix-R02-F4：显式 timeout 经 ContextVar 让 learned 让位；句子收紧带真杠杆）| P04/P08 |
| watch × 豁免进程边界 | watch run 在自己进程里建 fetcher，MCP 内存豁免够不着，错误行只复述安全门原话不指路 | **已修**（R02-001，fix-R02-F1：错误行带结构事实+持久化出路）| P01 |
| recall 同 URL 变体 | 两行 stale 一真一假并排（R01 P04 跨轮第二现）| **已修**（R02-007，fix-R02-F5）| P10 |
| 硬墙早停 | http 被墙 → stealthy 烧满预算，无「换源」早停 | wontfix(design)：per-host 失败记忆风险 > 代价；显式预算已是杠杆（R02-004）| P07 NEJM 90s |
| E1/E3/E8 | 三项跨轮三现 | backlog **升级标注**（R02-006，待排期）| P04/P05 |
| 缓存回执行为澄清 | 缓存命中的回执**没有** learned-ceiling attempt 句是正确行为（没走 HTTP 档）——P5 复验曾误判为缺陷 | 行为正确（journal 在册，防复发误判）| W3 复验 |

## round-03 新增/复发（P3 裁决后）

| 区 | 现象 | 状态 | 来源 |
|---|---|---|---|
| page_type 词义 | 字段描述没说「article=散文抽取成功」——L1 把抽取形状读成页面体裁（视频频道页标 article） | **已修**（R03-004，fix-R03-F1：描述点名，词表与判定不动）| P10 |
| 私网豁免作用域 | 首报「缓存回放不过门」→ P3 推翻：豁免是**进程级** identity store（在册），门与缓存同语义 | 无缺陷（行为澄清：进程级=会话间共享、CLI 进程不共享——R02-001 的故事） | P02 |
| pending 信号位置 | 首报「顶层看不见确认闸」→ P3 推翻：顶层 summary 后缀+next_action 全句在场；**量具教训**：driver 100 字截断打印会藏掉信号 | 无缺陷（观测量具教训记 journal） | P08 |
| recall 显著性 | 「我存的」无特权——语义契约下公平排序 | backlog **E12**（产品演进项） | P04 |
| E8 since 档位词 | 「3d」四轮不收（错误句自带示例） | backlog 四现（升级标注维持） | P07 |
| E3/E5/E10 | offset 整页口径 / 钳制句报剩余 / 404 行数字噪声——活体再现 | backlog（已建档，样本在册） | P03/P06 |

## round-03 必测回归点（round-04 新画像真实回访）

1. page_type 描述语义（article=散文抽取）——crawl 任意列表页行（fix-R03-F1）。
2. R02-007 recall 变体合并（跨会话二访单行 variants=N）。
3. R02-004 显式 timeout 不被 learned 压（带 timeout= 的重复调用）。
4. R01-003 resume 照抄跨会话（next_resume_token + next_action 可照抄句）。
5. R02-001 watch 私网错误行指路（真跨进程）。
6. R02-003 401 recover_with=cred_defaults（外网墙或 fixture 对照）。
7. R01-006/R01-010 challenge 同链取值 + drag 第一拒全形。
8. R01-001/R01-008 feed 顶点点名坏源 + 缺省 20s 预算。
9. per-call ignore_robots 三态（受控旁证进程，只指本地）。

## round-02 必测回归点（round-03 新画像真实回访）

1. archive 档对 capture 索引页守卫：构造/重放索引页快照，content_ok 必须为 False 且 note 点名（fix-R02-F2）。
2. 401/403 的 recover_with 与 next_action 同源点名 cred_defaults（fix-R02-F3）。
3. 显式 timeout= 不被 learned ceiling 压（带 timeout 的重复调用，attempt 预算保持调用方值）（fix-R02-F4）。
4. watch add 私网 URL → run 错误行带 watch 进程语境出路（fix-R02-F1）。
5. recall 同 URL 两轮抓取后单行 variants=N 且 stale 按最新（fix-R02-F5）。
6. per-call ignore_robots=true 三态（round-02 P10b 受控验证首次通过——保持）。

## round-01 必测回归点（新画像真实回访）

1. web_request POST /login + session_id → web_read /account 登录态（manual P2 的修）。
2. web_research 大 timeout 分摊不再整源被拒（manual P3 的修）。
3. 降级池如实报告（families_answered_of_asked / engine_detail）。
4. 私网门具名拒绝 + cred_defaults 豁免路线（env-facts §3）。
5. 提交闸全链路（web_act submit 无 confirm 不发）。

## 新探查点（round-01 首攻）

- web_recall 跨会话召回的 stale 标注准确性
- web_discover→web_crawl 的地图/断点续爬（resume_token）
- feed_read since= 增量语义（含 undated 条目计数）
- focus / 结构化 / OCR 档（本轮广谱基线尚未触过的档）
- doc_parse 非 UTF-8（GBK CSV）与多 sheet
