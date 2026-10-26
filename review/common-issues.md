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

## round-04 新增/复发（P3 裁决后）

| 区 | 现象 | 状态 | 来源 |
|---|---|---|---|
| iframe 失败话术 | selector 超时不提页面有 iframe/`frame:` 寻址——三步各烧 10s | **已修**（R04-001，fix-R04-F1：失败现场用子 frame 数这一般事实，步级错误追加指引） | P02 |
| watch 出路句前题 | `DHOLE_PERSIST_CREDENTIALS=1` 没说要在 **watch 进程**的 env 里（出路方向已验证真通：带 flag watch baseline 200 exit 0） | **已修**（R04-002，fix-R04-F2：句补半个前题） | P03 |
| 持久化行为澄清 | cred_defaults 落盘跨重启成立（重启后报 robots_disallowed 恰是私网门放行的证明——拦它的是另一扇门）；混 set（auth 无 host）整单 fail-closed 零落盘 | 行为正确（journal 在册） | P03 |
| E7 | 跨进程缓存回放缺存入时 env 语境 | backlog（三轮三现） | P01 |
| E2 邻接 / E10 | 查询串变体不合并（精确 URL 契约）/ 404 行数字噪声三现 | backlog | P07/P06 |

## round-05 新增/复发（P3 裁决后）

| 区 | 现象 | 状态 | 来源 |
|---|---|---|---|
| preflight 错误族 | 死端口报 timeout (TCP preflight) 而非 refused——OS 层 2s 延迟为本机防火墙环境所致，非稳定复现 | 观察项（不立案；需能稳定复现 refused-vs-timeout 的环境） | P01 |
| archive 档账目 | 404→archive 被问→availability API 429 逐字申报；all_tiers_failed 逐层具名 | 良好行为确认（R02 家产在线） | P03/P01 |
| 边界值申报 | max_content_chars=-1 钳制申报；offset 越界 [No more content.] 哨兵；since 无日期条目放行且报数 | 良好行为确认 | P06/P07 |
| E10 | 404 行数字噪声 | backlog（四轮样本） | P04/P10 |

## round-06 新增/复发（P3 裁决后）

| 区 | 现象 | 状态 | 来源 |
|---|---|---|---|
| css_selector 0 匹配 | 静默回全页、看起来像成功（伪类第一拒自证的旁证） | **已修**（R06-001，fix-R06-F1：0 匹配分支说「整页被返回」；回落行为不变） | P01 |
| 命中碎片被散文尺误判 | 点名的 11 行卡片标题被判 nav_only→content_ok=false | **已修**（R06-002，fix-R06-F2：_substance_verdict 的 css_selector 豁免——extractor 侧早有同款） | P01/P08 |
| E2 家族 | 查询串变体三行并排（五轮样本：R02/R03/R04/R05/R06） | backlog（**五现**，升级线早已过——排期项） | P06/P09/P10 |
| focus 引擎名 / sheet 小计 | 回执不露用了哪把引擎；doc_parse 无每 sheet 小计 | backlog E 观察登记 | P02/P04 |

## round-07 新增/复发（P3 裁决后）

| 区 | 现象 | 状态 | 来源 |
|---|---|---|---|
| 拒绝句落后于手册 | 寄存器拒绝只说 no earlier action，不教「一条链 + pin 保页 + 重读同值」出路（手册先长出 pin 章，错误句没跟上） | **已修**（R07-001，fix-R07-F1：no-store 分支补出路句；available 分支不加；行为零变化） | P02 |
| 共享缓存躲门 | P09「先撞门」腿吃了 7 分钟前的 cache replay，私网门未被真撞（回执如实申报 cached copy，非伪造成功） | 环境/量具注（受控场景 cache_ttl=0 已在册——round-04 教训家族） | P09 |
| E 家族 | 无新样本（E2 本轮未触发） | — | — |

## round-08 新增/复发（P3 裁决后）

| 区 | 现象 | 状态 | 来源 |
|---|---|---|---|
| frame 地址落空指错对象 | 相对 URL 前缀炸 CSS 解析错（无 frame 字样）；错名等到超时被 selector-miss 句顶替 | **已修**（R08-001/005，fix-R08-F1：CSS 支预数顶层文档，解析失败/0 匹配就地具名 + 在场清单 + absolute 限定） | P04/P09 |
| 重铸寄存器静默换值 | 同名 as= 二铸覆盖首铸，fill 实填调用方没想要的值 | **已修**（R08-003，fix-R08-F2：重铸行带 register_overwritten 注记；last-wins 语义保留） | P06 |
| 上传寿命静默 | 跨调用提交落 `received (0 bytes)` 无说明（单发组合全通反证非损坏） | **已修**（R08-002，fix-R08-F3：upload 行带 lifetime 句 + tools.md 补句） | P05 |
| **量具截断假候选** | R08-004/R08-006 两起 REJECT 同根：判「summary 无一字提及」时只读了前 400/500 字——失败小计与 pin 具名拒绝**都在场且真发** | **审查推翻首报**（教训入册：判「无解释」必须读 summary 全文） | P09/P07 |
| E 家族 | 无新样本 | — | — |

## round-09 新增/复发（P3 裁决后）

| 区 | 现象 | 状态 | 来源 |
|---|---|---|---|
| recall limit 静默 clamp | limit=21/-5 被 max(1,min(20,..)) 静默改写——违反手册「clamped-and-named or refused, never silently rewritten」 | **已修**（R09-002，fix-R09-F1：越界回执带 clamped 注记；未越界安静） | P10 |
| **量具截断假候选（第三起族）** | R09-001 的账在 fetch_hint 全文（假算术跨轮相减）；R09-003 双形状是 test 立的规矩；R09-004 location 门 probe 实证 | **三起全 REJECT**（教训入册：判「无账」前必须读全 summary/error/next_action/fetch_hint 四账面） | P06/P08/P09 |
| E2 家族 | /article 8 个查询串变体并排（六轮样本：R02-R06+R09） | backlog（六现，排期项） | P04 |
| 引擎可用性轮间浮动 | 同 query 两轮引擎集不同（marginalia 时在时不在） | 环境注（engine_detail/families_answered_of_asked 账目在场） | P03/P04 |

## round-10 新增/复发（P3 裁决后）

| 区 | 现象 | 状态 | 来源 |
|---|---|---|---|
| crawl 代际对 read 不可见 | crawl 抓的壳页（nav-only）不进 read 缓存——store 侧质量闸 + 指纹键合（html/markdown 各一代） | **设计如此**（REJECT：_store_ttl 注释 + behavior guide 键合契约 + probe 复核） | P10 |
| attempt_cap < 显式 timeout | 5000→4000/20000→19000——分层扣减后真实所得 | **字段合同如实**（REJECT：attempt_cap_ms 描述"what is left of the call when this tier runs"） | P01/P09 |
| recall 0 行 | 查询词与页正文不匹配（臆测词） | **量具注**（probe：正文词命中 6 行——召回正常） | P04 |
| E 家族 | /changing 变体族再现（E2 六现既有） | — | P04 |

## round-11 新增/复发（P3 裁决后）

| 区 | 现象 | 状态 | 来源 |
|---|---|---|---|
| feed max_items 负数静默全量 | -3 被 `<=0` 分支同 0 一起放行——描述只许「default 20; 0 = all」 | **已修**（R11-001，fix-R11-F1：负数具名拒绝；0 语义不动） | P10 |
| 修法句住在 next_action | POST 写闸的 confirm=true 教在 next_action——只读 error 会漏（R09-003 族） | 环境/量具注（probe 实证 confirm=true 全通） | P03 |
| doc_parse 双码 | %PDF 在结构坏=pdf_open_failed；无魔数=not_a_pdf | 两码各司其职（REJECT，非缺陷） | P01 |
| sys_guide 未知 topic | 内容教学列全部 topic——非错误门 | 设计（REJECT） | P07 |
| E 家族 | 无新样本 | — | — |

## round-13 新增/复发（重装链轮 · 2 CONFIRM）

| 区 | 现象 | 状态 | 来源 |
|---|---|---|---|
| 收窄判决 × collapse | 显式 css_selector 取回点名碎片（10/400 字）被判 `extraction_collapsed`、content_ok=false，恢复建议也指错路 | **已修**（R13-001，fix-R13-F1：判决尺在 selector 在场时豁免；恢复尺不动） | p21 A3 / p22 C7 |
| 缓存 × 参数注记 | 同 URL+selector 第二次读命中缓存，R06-001 的「matched 0 elements」句消失、整页照回 | **已修**（R13-002，fix-R13-F2：envelope 存同源 selector 注记、回放带出；旧行缺席不说） | p22 #02/#03 |
| tab/iframe 寿命 | 页签/iframe 状态不跨调用；pin 是留住机制且需具名 session_id（无名字时产品主动教） | 设计（D）+ E 记录（tab/iframe 回执可补寿命句） | p09 / p21 B / p14 |
| 表单页 read | 表单/菜单页 read 无正文（15-63B 碎片），act snapshot 是正路 | D（诚实标签在）+ E 记录 | p01/p03/p10 |
| submit 闸 confirm | next_action 字面像顶层 `{"confirm": true}`，正形是动作内嵌套 | E 记录（R13-003；动作行 error 已给嵌套示例） | p10/p05 |
| dialog 文案 | 错误第二形状 `{"answer": ..., "prompt_text"?: ...}` 未说死 answer 只收 accept/dismiss | E 记录（R13-004） | p03 / p21 C1 |
| 靶站 bytes | upload 回执 63 bytes vs 磁盘 65（靶站 multipart `-2` 启发式） | 环境（C），非缺陷 | p16 |

## round-13 必测回归点（下一轮若继续 · 新画像真实回访）

1. 显式 css_selector 收窄：content_ok 不受整页塌陷尺影响；无 selector 的真塌陷仍报（单测+线上对照）。
2. 缓存回放带参数注记：同 URL+selector 两读，0 匹配句 live/hit 都在；旧行缺席不编。
3. 提交闸 confirm：上传/checkout/captcha 三处 action 级 confirm 真提交；顶层 confirm 被拒且动作行给嵌套示例。
4. dialog prompt 正形（answer=accept + prompt_text）与误序 auto-dismissed note。
5. tab/pin 寿命：同调用页签台账；pin+session_id 跨调用命中；无 session_id 的 pin 教学句。
6. pin 命中 / auth_wall 分家 / feed 三连+混发 / recall clamp / frame 错名 五族家产合并回访。

## round-12 观察（链条协奏轮 · 0 CONFIRM）

- **auth_wall 判词首次实锤**：web_act 对 /login 判 `auth_wall_detected`（内容判词），
  动作链照跑（actions 行 fill ok=true），判词与动作分家（R01-028 设计）；next_action
  教两扇真门（cred_defaults / 换源）。r12-004 REJECT 转观察。
- **recall 空白 query 按缺席**：`Missing required argument(s): ['query']`——
  server.py:12855 通用门，3 处 test 立规；措辞可议，形状在册。r12-005 REJECT 转观察。
- **量具三坑（脚本侧，非缺陷）**：discover 行=pages 本身（无 links 键）；doc_parse
  content 是字符串列表（web_read 才是 dict.text）；sys_state 账在 message 键。
- **补条纪律**：补条脚本不得复用主跑 persona 目录（p05b 覆盖了 p05 的 S1-S4 回执；
  台账与运行日志保全）——round-13 起补条用独立 persona 号。
- **真重启后 cred_defaults 清零**：进程内默认不落盘，私网门重新武装——复验脚本必须
  先置 allow_private（本轮首轮 verify 三发 FAIL 即此，行为正确）。

## round-12 必测回归点（round-13 新画像真实回访）

1. pin 命中合同：同 url+session+pin 第二发站原页（pin hit 句）+ fill 值跨调用活（r08+p05c）。
2. css_selector 0 匹配：summary 内 `was NOT applied: it matched 0 elements`（R06-001 家产）。
3. 链上中环第一拒：4 裸拒带码+出路 / 3 clamp 落 summary·clamped / 3 unsupported 带名单。
4. auth_wall 判词与动作分家：判词 content_ok=false 同时 fill 行 ok=true。
5. feed 三连账（负数具名/0=all/正数切片 note）+ 混发逐行分账（r11+r12 合并）。
6. 前十一轮家产合并回访。

## round-11 必测回归点（round-12 新画像真实回访）

1. feed max_items 负数具名拒绝；0=all 保持；正数切片+note（fix-R11-F1）。
2. web_request 写闸 confirm 形状（next_action 教法）+ session 交叉（欢迎回来）。
3. doc_parse 双码（pdf_open_failed / not_a_pdf）+ GBK 保真。
4. feed 混发逐行分账（404/connection_refused 具名）+ since 教日期格式。
5. 第一拒全集 12 发抽样：11 颗带码前缀+出路，sys_guide 内容教学。
6. 前十轮家产合并回访。

## round-10 必测回归点（round-11 新画像真实回访）

1. 显式 timeout= 合同：learned 句让位不出现；attempt_cap_ms=分层扣减实得。
2. 边界第一拒：timeout=0/999999、cache_ttl 类型错、max_pages=0/50 具名 clamp。
3. TTL 四态：首灌/代内 replay/ttl=0 回源/ttl=1 过期真回源。
4. replay 诚实形状：无 attempt 伪造、年岁递增（round-02 W3 教训保持）。
5. research 紧预算慢源逐行具名；wait/wait_selector 步级帽申报。
6. 前九轮家产合并回访。

## round-09 必测回归点（round-10 新画像真实回访）

1. recall limit 越界的 clamped 注记；未越界安静（fix-R09-F1）。
2. search 过滤语义四件套：site= 0 越域 / exclude_sites= 0 漏剔 / engines= 来源单一 / 引号短语。
3. 零结果与降档话术：不存在站点 `from none`；乱码查询分级降档不装 high。
4. 第一拒教学谱：max_results/mode/freshness/page/location 具名；research max_sources=0 具名 clamp。
5. min_relevance 门槛的 fetch_hint 门槛账（"N below min_relevance=…"）在场。
6. E2 家族回访（六现线）+ 前八轮家产合并回访。

## round-08 必测回归点（round-09 新画像真实回访）

1. frame 落空具名：相对前缀 → not a usable address + absolute；错名 → matched 0 <iframe> + 在场清单（fix-R08-F1）。
2. 重铸同名寄存器的 register_overwritten 注记；首铸安静（fix-R08-F2）。
3. upload 行 lifetime 句 + 单发组合 received <文件名> 真通（fix-R08-F3 + p12 通路）。
4. 组合态家产：弹窗五形态账实符/页签四动作+最后一枚拒关/双 session 隔离/跨 frame 寄存器三件套（p02/p03/p10）。
5. pin+session 人在中途正形（p15 抄写正确）+ R04-F1 误打提示 + R07-F1 寄存器出路句（两回归）。
6. 前七轮家产清单（common-issues 各轮必测回归点合并回访）。

## round-07 必测回归点（round-08 新画像真实回访）

1. 寄存器拒绝补句（one call / this chain / read as= / pin=true 重读同值）全文在场；有可用寄存器时仍列 available（fix-R07-F1）。
2. 手册四篇抽查：start 三路线照做通、configuration env 清单=代码注册表（现 19 名）、behavior 交互示例照抄可跑。
3. 名单句完整谱：force_fetcher/focus_mode/extraction_type/sys_state show tokens——名单内真收、名单外真拒，多一个少一个都是候选。
4. 绝对句×2：cloud metadata 具名拒绝（豁免在力下 fetch 层）；archive 不可关（错误行带 archive.org asked/429）。
5. 前六轮家产清单（common-issues 各轮必测回归点合并回访）。

## round-06 必测回归点（round-07 新画像真实回访）

1. 0 匹配 selector 的 summary note（fix-R06-F1）。
2. 命中碎片的 content_ok=true + substance 留空 + basis（fix-R06-F2）。
3. 伪类/非法参数第一拒教学形状（保持）。
4. doc_parse 真 PDF 的图片披露 + not_a_pdf 具名（保持）。
5. check diff/provenance/headers 家产（保持）。
6. 前五轮家产清单（common-issues 各轮必测回归点合并回访）。

## round-05 必测回归点（round-06 新画像真实回访）

1. 预算分账：小预算调用拒发 stealthy 的毫秒账（P01）。
2. all_tiers_failed 逐层具名（P01/P03）。
3. archive 档真触发 + 失败原因申报（429/无快照）（P03）。
4. 私网谱三档话术 + 怪值形状句（P08）。
5. 非法参数第一拒教全名单（P05）。
6. 边界值钳制申报 + offset 哨兵（P06）。
7. feed 混坏行级分账 + since 无日期报数（P07）。
8. 前四轮家产（iframe 提示/watch 出路句/resume/显式 timeout/401 杠杆/challenge/drag/recall 合并/page_type）。

## round-04 必测回归点（round-05 新画像真实回访）

1. iframe 页裸 selector 超时 → 步级错误带 frame: 指引（fix-R04-F1）。
2. watch 私网错误行带「flag 在 watch 进程 env」前题；带 flag 的 watch 真跑通（fix-R04-F2 + P03 通路）。
3. cred_defaults 落盘跨重启（旁证生命周期）；unset 即时生效（P09）。
4. allow_private 点名清单边界：名单内过、名单外拒（P09）。
5. check 附 diff（changed/first_divergence_at/diff_window）+ provenance 三读（P06/P08）。
6. upload 确认闸三跳 + 「received <文件名>」落点（P02）。
7. robots 受控三态（旁证 env 关，只指本地）（P01）。
8. 前三轮家产：resume 照抄/显式 timeout/401 杠杆/challenge 同链/drag 全形/feed 点名/recall 合并/page_type 语义。

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

## round-14 新增/复发（回访与新鲜度轮 · 0 CONFIRM）

| 区 | 现象 | 状态 | 来源 |
|---|---|---|---|
| offset 续片窗口形状 | 首报 `473B of 7.0KB` 「不点名切片」→ 重读在途测试 `test_an_offset_window_summary_names_the_slice.py`：`<本窗> of <总量> markdown` 正是契约，尾窗 truncated=False 也说切片 | **REJECT（首报自推翻）**；教训：判形状前先读在途测试/契约，别凭脑内形状 | p02 S2 / p10 |
| recall 同文变体 + 词形巧合 | `/article`、`?t=html`、`?cb=…` 三行并排；「对话框 删除」召回注意力综述（snippet 含「删除线」） | E2/E6 已在册复发（R02-007 只合并同 URL；query 变体属「search 侧仍开放」） | p01 / p07 |
| doc_parse summary 不点名 pages | `pages_returned=2`、`pages_total=3`、TOC 2/3 页，summary 只写 `1.3KB markdown · parse` | 字段层准确，交付话术缺口（E） | p06 |
| discover path_include 种子行 | `path_include="/work"` → 7 行 = 6 个 `/work/*` + 种子 `/collections?items=6`；测试只断言续页、文档未写根行例外 | D/E（根行保留按设计；文档补一句，行为不改） | p11 S2 |
| discover 手册 `max_depth` 漂移 | `references/tools.md:328` / `troubleshooting.md:72` 仍教 `max_depth`，schema 只收 `max_pages`；summary 仍说 `depth <= 2` | E（文档漂移；文件正被并发 agent 编辑，本轮**不碰**，待其落地后同步） | p07 / p11 |
| L1 工程词 | cached / fetched_at / truncated / replay 直接上脸 | E 既有（长期） | p03 / p05 / p09 |
| sys_guide 原文截断 | driver 对非 JSON 回执只存 600 字 | harness 量具（第五族：截断假候选） | p04 |
| **正向实锤（在途新特性全成立）** | 相对 since（3h/3d/2w → 绝对 ISO 回执）· cache_ttl=0 回源/正数回放同 fetched_at · check 两相 baseline→changed · 批量读逐行 cached · offset 窗口 | 无缺陷（R14-010 PASS） | p01/02/04/05/08/09/10/12 |

## round-14 必测回归点（round-15 新画像真实回访）

1. 相对 since 三档（3h/3d/2w）：回执 `since` 是绝对 ISO、窗口外条目计数如实、undated 放行报数。
2. `cache_ttl=0` 真回源；正数回放 `cached=True` 且 `fetched_at` 同值；回放不伪造 attempt 句。
3. `extraction_type=check` 两相：首问 `1 baseline recorded`；再问 `changed=True`；未变说 unchanged。
4. offset 续片 summary 一律 `<本窗> of <总量> markdown`（尾窗也说是切片；`[No more content.]` 不算窗口）。
5. discover：`max_pages`/`path_include` 谓词 + 种子行；手册 `max_depth` 漂移是否已被并发 agent 修掉。
6. r13 家产三件：css 收窄不判 collapse / 缓存 selector 0 匹配注记 live+hit / feed max_items=-3 具名拒绝。
7. 前十三轮家产合并回访。

## round-15 新增/复发（并行与隔离轮 · 1 CONFIRM）

| 区 | 现象 | 状态 | 来源 |
|---|---|---|---|
| 空框 property 读崩 | `read {"property":"value"}` 命中空字符串时 `empty_hint` 未绑定 → UnboundLocalError 直接上脸（未填的框、重导航后的空框都能触发） | **已修**（R15-001，fix-R15-F1：property 分支补「that is its current value」说明；与 attribute 分支同形） | p09 S2 |
| live/replay `fetched_at` 差 1s | live 在 `_with_agent_hints` 末尾盖 now，缓存行存更早的 set_cached 时刻，跨秒即差 1s；字段描述写「For live fetches the two coincide」 | E（R15-002；方向安全：回放不更“新”；修复需贯穿三条路，风险>收益） | p05 S3/S4 · p10 S1/S5 |
| recall 尾斜杠变体 | `/` 与 `/` 两行并排、is_stale 一真一假 | E2 复发（R02-007 只合并完全相同 URL；精确 URL 契约在册） | p10 S4 |
| discover 谓词根行 + max_depth | 谓词地图保留起点行（筛空时 summary 如实报 7 个候选被筛掉）；summary 仍说 `depth <= 2`、手册仍教 `max_depth` | D/E 复发（R14-004/005；docs 并发编辑中，本轮不碰） | p07 S1/S2 |
| sys_guide topic 误用 | `topic=web_read` 不存在 → 非 JSON 原文教学列全部 topic | A（r11 已裁决非错误门） | p01 S1 |
| recall 查错词 | 「规格 供应 工作」在 /work 正文里不存在 → 0 行且 note 如实说「nothing on this disk matches」 | A/量具（r10 同族） | p04/p07 |
| 跨轮共享缓存 | p04 首读 `cached=True`、fetched_at 早于本轮——cb 与 r14 脚本重名，磁盘缓存跨进程共享 | 量具注（教训：**每轮 cb 带轮号**） | p04 S1 |
| **隔离面正向实锤** | 双 selector 交错互不串味（10/387/10B，缓存键隔离）· 双 session 填名互不覆盖 + pin 命中 · 登录 23B vs 匿名 14B · check 两相与 read 缓存分家 · crawl 不播种 read 缓存 | 无缺陷（R15-009 PASS） | p01/02/04/05/07/08/09 |

## round-15 必测回归点（round-16 新画像真实回访）

1. 空表单框 property 读：ok=False + “current value” + 无 `empty_hint`/无 Python 异常；非空 property 读仍回活值。
2. 双 pin session 隔离：A/B 各持其值，回 A 仍读回 A 的值且 pin hit。
3. 同 URL 双 selector：内容不同、缓存键隔离、同 selector 复读同值。
4. live→replay `fetched_at`：回放不晚于 live；记录实际 delta（E17 观察）。
5. 登录 session vs 匿名同页：正文不同且不共享缓存键。
6. r14 三件：相对 since 绝对 ISO / offset 窗口 / check 两相；r13 三件（收窄/注记/负数）。
7. recall URL 尾斜杠变体（E2）+ discover path_include 根行与 max_depth 文档（E 复发）。
8. 前十四轮家产合并回访。
