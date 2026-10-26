# 全局追加日志（断点续跑）

## 2026-10-25T21:14 会话启动（goal round 1）
- 读宪章 loop/PROMPT.md + loop/ref/*（tools/templates/env-facts）。
- 实测：dhole v17.2 editable 全能力就绪；tools/list 13 颗；review/ 仅 manual-2026-10-25。
- git：WIP(50 文件) 存档 commit 3cf035e → wip/inflight-20261025-2120 与 review/round-01 同指；main 未动（e917220）。
- decisions.md 落 5 条；common-issues/backlog 种子化。
- 下一步：起服务（9201 + 靶站 8399）→ harness 自测 → 封存 10 画像 → P1。

## 2026-10-25T23:5x round-01 收官（P6 完成）
- P2：10 画像报告齐 + report-round.md（101 发、13/13 工具、回归 5/5 PASS）。
- P3：14 候选 → 8 CONFIRM（7 产品 + 1 harness）+ 1 wontfix（007 设计取舍）+ 5 backlog；4 项真机补测，3 处审查推翻自己（R01-005 产品→harness、R01-006 能力→可发现性、R01-010 snapshot 半）。
- P4：7 处产品修复 + harness，全先测后改（7 新测试文件 + 3 回归锁更新，论证在测试内）；全量 pytest 7728 绿、ruff 0 新增。
- P5：真重启复验 8 项全 FIXED（V1–V8 原始回执在 verification.md）。
- P6：提交 aeb7059（src+tests+review 全套）；index/backlog/common-issues 更新；fixture-evidence 归档。
- 下一步：round-02 P0（主题：边缘与刁难；端口 9202；从 aeb7059 切出）。

## 2026-10-26T01:1x round-02 收官（P6 完成）
- P1：10 画像（P01 运维/P02 法务/P03 学生/P04 数据工程/P05 外贸/P06 建材/P07 医师/P08 自动化测试/P09 财务/P10 研究生；L1×2/L2×7/L3×1）64 发，13/13 工具，多工具链 5 画像；P10b 受控旁证（9203 env 关闭）首验 per-call ignore_robots 三态。
- P2/P3：7 候选 → 5 CONFIRM（含 2 半 CONFIRM 半 REJECT）+ 1 wontfix（R02-005 设计）+ 1 backlog 升级（R02-006：E1/E3/E8 三现线）；E2 升格确认（R02-007）。
- P4：5 处修复全先测后改（5 新测试文件 18 测试；wire 描述改动走 dhole wire lock --write 重锁 + 4 处常量同步）；ruff 0 新增。
- P5：真重启复验 W1–W5 全 FIXED，原始回执落盘 sessions/verify/；全量 pytest **7746 绿**。**教训在册**：W3 曾两度误判——①缓存命中的回执本来就没有 attempt 句（没走 HTTP 档，正确行为，不是缺陷）；②learned 句首发缺席=冷 EMA（无样本不念，正确）。复验遇「预期 note 不在」先查路径是否真走了对应档，再判缺陷。
- P6：提交（round-02 全套）；index/backlog/common-issues/journal 更新；calls.jsonl 89 行对账说明在 verification.md。
- 下一步：round-03 P0（主题：长任务/跨会话/多工具链；端口 9203；从 round-02 顶端切出）。

## 2026-10-26T02:2x round-03 收官（P6 完成）
- P0/P1：9203 新起 + 10 卡封存（10 职业/L3×2/L1×3）→ 60 发、13/13 工具、跨会话（新 session 二访 + resume token 跨会话 + watch 真跨进程）+ 跨进程重启的 recall 存量被真实用到。
- P2/P3：6 候选 → **1 CONFIRM(B)**（R03-004 page_type 描述）+ **2 REJECT（审查推翻首报）**：R03-001 豁免是进程级（在册），R03-003 顶层信号在（**量具教训：driver 的 100 字截断打印会藏掉信号，判「顶层缺信号」前必须看全文**）；R03-002 降 backlog E12；R03-005/006 记 E 活体。
- P4：唯一修复 test-first（1 新测试文件）；ruff 0 新增；**事故在册**：一次 git stash/pop 撞上 fixture 进程锁住的 .runtime 日志（Windows unlink fail），恢复=checkout stash 的单文件 + drop——fixture 持日志时不得 stash。
- P5：真重启（9203 kill→起）复验 W1（page_type 取值零变化）+ 前两轮 11 项回归全 PASS；全量 pytest 7747 绿。
- P6：提交（round-03 全套）；index/backlog(E12)/common-issues/journal 更新。
- 下一步：round-04 P0（新主题自定；端口 9204；从 round-03 顶端切出；先读 common-issues 的 9 项必测回归点）。

## 2026-10-26T03:1x round-04 收官（P6 完成）
- P0/P1：主题「状态与写路径」；9204 + 旁证 9205（robots 三态）/9206（持久化）；10 新职业（L3×2/L1×3）；55 发 + 补条 3 发 = 13/13 工具。
- P2/P3：4 候选 → 2 CONFIRM(B) + 2 E 记录；**三条大行为澄清**：①持久化跨重启成立（重启后报 robots_disallowed 恰是私网门放行的证明）；②混 set fail-closed 整单拒零落盘（P03 首试误判成「持久化坏了」——脚本没读返回值）；③R02-001 出路真通（带 flag watch baseline 200 exit 0）。
- P4：2 处修复 test-first（2 新测试文件 4 测试）：iframe 超时步级提示（失败现场用子 frame 数，一般机制）、watch 出路句补 watch 进程 env 前题。
- P5：真重启复验 W1/W2；全量 pytest **7751 绿**（首轮 -x 曾 1 failed 疑 flaky，两轮完整重跑全绿——诚实记录）；ruff 0 新增。
- P6：提交（round-04 全套）；index/backlog(E7 三现/E10 三现)/common-issues/journal 更新。
- 下一步：round-05 P0（端口 9205 段；从 round-04 顶端切出；common-issues 的 8 项必测回归点）。

## 2026-10-26T04:0x round-05 收官（P6 完成）
- P0/P1：主题「慢与坏（故障路径）」；9205；10 新职业（L3×3/L1×3）；53 发 13/13 工具。
- P2/P3：2 候选 → **0 B（首个零确认轮）**——故障路径在前四轮加固下全站住了；R05-001 preflight 错误族=环境所致不立案（connect_ex 10061 但 OS 拖 2s）；报告侧证据密度排除偷懒嫌疑（§8 只一轮零确认，不作胜利宣言）。
- P5：pytest 7751 绿（零改动零新测试，对账平）、ruff 0 新增、真重启 13 tools。
- P6：提交（round-05 全套）；index/common-issues/journal 更新；观察项入册。
- 下一步：round-06 P0（端口 9206 段；从 round-05 顶端切出）。

## 2026-10-26T05:0x round-06 收官（P6 完成）
- P0/P1：主题「读什么像什么（抽取与交付质量）」；9206；10 新职业（L3×2/L1×3）；50 发 13/13 工具。
- P2/P3：4 候选 → **2 CONFIRM(B)**——抽取面的两处「沉默」：①css_selector 0 匹配静默回全页（伪类第一拒的 would-have-been 句是它自己的旁证）；②命中碎片被整页散文尺误判 nav_only。根因都在册原则的一半：extractor 侧早有 narrowed 豁免，实质侧没有。
- P4：2 修复 test-first（2 新测试文件 4 测试）；复用已有 _CSS_SELECTOR ContextVar（不加新变量）；顺带清一枚基线漂移的 F401。
- P5：真重启复验 W1/W2；pytest **7755 绿 ×2**（首轮 DNS-recheck xdist flake 2 failed，隔离+两轮重跑全绿——诚实记录）；ruff 16 基线。
- P6：提交（round-06 全套）；index/common-issues/journal 更新。
- 下一步：round-07 P0（端口 9207 段；从 round-06 顶端切出）。

### 观测量具教训（跨轮复用）
- driver 的 summary 100 字截断会藏信号（round-03 R03-003）→ 判「顶层缺信号」前必须看全文。
- 旁证进程撞主进程磁盘缓存会让「受控三态」失真（round-04 P01 态3）→ 受控测试一律 cache_ttl=0。
- 脚本不读返回值会把 fail-closed 拒绝误判成功能损坏（round-04 P03）→ 诊断脚本必须打印全回执。

## 2026-10-26T06:0x round-07 收官（P6 完成）
- P0/P1：主题「手册与实况（文档诚实轮）」；9207；10 新职业（L3×3/P02·P07·P10，L1×3/P03·P06·P09）；57 发 13/13 工具（p80/p81 旁支补 discover/research/doc_parse/request）。
- P2/P3：1 候选 → **1 CONFIRM(B)**（R07-001 寄存器拒绝缺 pin 出路——手册对，拒绝句没跟上，话术层修）；手册对账 8 项 PASS：19=19（代码 env_registry 双向零差，不信 P1 脚本重 derive）、urls=11 描述如实、绝对句×2 fetch 层实证；**0 REJECT 需立档**（无首报被推翻）；**两处 P1 证据缺口由 P3 probe 当轮补测**（probe A cloud metadata fetch 层 / probe B watch 错误行原文）——量具薄点不靠脑补入档；量具教训+1：p09 撞门腿被共享缓存中和（受控场景 cache_ttl=0 家族）。
- P4：唯一修复 test-first（1 新测试文件 2 测试，先红后绿）；ruff 16 基线。
- P5：真重启（pid 29048，dhole-9207.log 唯一横幅可查）复验 W1 补句全文在场 + W2 手册对账；pytest **7757 绿 ×2**（首轮 DNS-recheck xdist flake 2 failed，隔离+两轮重跑全绿——诚实记录）。
- P6：提交（round-07 全套）；index/common-issues/journal 更新；issues.json 单条。
- 下一步：round-08 P0（端口 9208；从 round-07 顶端切出；先读 common-issues 的 round-07 必测回归点 + 各轮家产合并回访）。

## 2026-10-26T08:4x round-08 收官（P6 完成）
- P0/P1：主题「组合与长链（web_act 深水）」；9208；10 新职业（L3×3/L1×3）；93 发 13/13 工具（画像 57 + 补全 p11-p15 14 + 旁支 p80/p81 22）；靶点地图 P0 预检在册（/dialog 三弹窗、/frame+inner、/upload、/dropdown、/challenge/text、/captcha、/login→/account、/checkout）。
- P2/P3：6 候选 → **4 CONFIRM(B) + 2 REJECT（审查推翻首报，同根=量具截断）**：R08-004 失败小计、R08-006 pin 具名拒绝**都在 summary 全文里且真发**——两起假候选都是判「无解释」时只读前 400/500 字。**量具教训重磅入册**：判「summary 无一字提及」前必须读全文（round-03 截断教训的进阶版：这次是我自己的 print 截的）。
- P4：3 修复 test-first（3 新测试文件 8 测试 + 1 个既有 fake 补 count）：frame 落空就地具名（含 absolute 限定与在场清单）/重铸寄存器出声/upload 行教寿命（+tools.md 一句）。ruff 16 基线（新文件 F401 即清）。
- P5：真重启（kill 29048 式失败在册——**Windows 下 Git Bash kill 对该进程无效，须 taskkill //F**；新 pid 20768）复验 V1a/V1b/V2/V3 全 PASS；pytest **7765**（7757+8 对账平）——xdist 全量 5 连跑同 2 个 DNS-recheck 测试 failed（每次隔离绿，round-06/07 已在册的 flake 本轮转顽固），顺序标准跑另证（结果见 verification.md）；ruff 16。
- P6：提交（round-08 全套）；index/common-issues/journal 更新；issues.json 6 条（4 fixed + 2 rejected）。
- 下一步：round-09 P0（端口 9209；从 round-08 顶端切出；先读 common-issues 的 round-08 必测回归点）。

## 2026-10-26T10:3x round-09 收官（P6 完成）
- P0/P1：主题「找得到吗（检索与排序质量）」；9209；10 新职业（L3×3：图书馆员/舆情/专利代理）；74 发 13/13 工具（web_request 以具名 GET 门计入）；p91 probe 补 location 门证据。
- P2/P3：4 候选 → **1 CONFIRM(B) + 3 REJECT（审查用回执/probe/源码推翻首报三起）**：R09-001 min_relevance 的账在 fetch_hint 全文（首报跨轮假算术+只读 300 字）；R09-003 前缀双形状是 test 立的规矩（_param_refusal docstring 在册）；R09-004 location 门形状校验 probe 实证。**量具教训第三度入册且升级**：判「无账」前必须读全 summary/error/next_action/fetch_hint 四账面字段——三起假候选全是「没读全」。
- P4：唯一修复 test-first（1 新测试文件 3 测试，先红后绿）：recall limit 越界 clamp 具名（clamped 注记，形状同 web_research）；未越界安静。
- P5：真重启（taskkill pid 20952 → 24828）复验 V1/V2 PASS；pytest **7768 绿×2**（7757+8+3 对账平；DNS-recheck flake 本轮两连绿未发）；ruff 16 基线。
- P6：提交（round-09 全套）；index/common-issues(E2 六现)/journal 更新。
- 下一步：round-10 P0（端口 9210；从 round-09 顶端切出；先读 common-issues 的 round-09 必测回归点）。

## 2026-10-26T11:3x round-10 收官（P6 完成）
- P0/P1：主题「预算与时间」；9210；10 新职业（L3×3：外汇交易员/电梯检验师/同声传译）；57 发 13/13 工具 + probe ×2（p91 html 指纹/p92 正文词）。
- P2/P3：**0 CONFIRM（第二个零修复轮，非连续——round-09 有 1 B）**——3 线索全 REJECT：crawl 代际=store 侧壳页闸+指纹键合（两道在册合同）；attempt_cap 差值=字段合同如实（"what is left of the call"）；recall 0 行=量具查错词（probe 用正文词命中 6 行）。§8 自查：四账面字段全读 + 源码对照 + 双 probe，非漏报。
- P4：零修复。P5：真重启（pid 14648）13 tools PASS；pytest 7768 绿（对账平）；ruff 16。
- P6：提交（round-10 全套）；index/common-issues/journal 更新。
- 下一步：round-11 P0（端口 9211；从 round-10 顶端切出；先读 common-issues 的 round-10 必测回归点）。

## 2026-10-26T13:1x round-11 收官（P6 完成）
- P0/P1：主题「冷面深水」；9211；10 新职业（L3×3：字幕组长/珠宝鉴定师/飞行签派员）；63 发 13/13 工具 + probe（POST confirm 补全/not_a_pdf 魔数）。
- P2/P3：4 候选 → **1 CONFIRM(B) + 4 REJECT**：R11-001 feed max_items 负数静默全量（描述只许 0=all——同 R09-002 家族合同）已修；R11-002 doc_parse 两码各司其职；R11-003 POST confirm 教在 next_action（量具没读全——教训家族第四起）；R11-004 since 教日期格式；R11-005 sys_guide 内容教学非错误门。
- P4：唯一修复 test-first（1 新测试文件 3 测试，先红后绿）：feed max_items 负数具名拒绝（0 语义不动）。
- P5：真重启（pid 18048）复验 V1/V2 PASS；pytest **7771 绿×2**（7768+3 对账平；DNS-recheck flake 来去如前，隔离绿+两连全量绿在册）；ruff 16。
- P6：提交（round-11 全套）；index/common-issues/journal 更新。
- 下一步：round-12 P0（端口 9212；从 round-11 顶端切出；先读 common-issues 的 round-11 必测回归点）。

### round-12 收官（2026-10-26）

- 主题「链条协奏」：10 条 2-4 颗工具的真实任务链，68 发 13/13 工具。
- **0 CONFIRM**（继 r05、r10 后第三个零修复轮，非连续）：5 候选全 REJECT——
  R12-001/002 又是量具截断/未实测真身（四账面字段铁律再拦两次）；R12-003 clamp
  落点看错字段；R12-004/005 转观察项（auth_wall 分家 / 空白按缺席）。
- 新实锤：pin 命中合同全链（p05c 铸值跨调用回读）；auth_wall 判词与动作分家；
  真重启后 cred_defaults 清零（私网门重新武装，复验须先置）。
- pytest 7771 绿（数平，0 fix 无新增）；ruff 16 基线。真重启 pid 28016→3164。
- 教训：补条脚本复用 persona 目录覆盖主跑回执（台账保全）——补条用独立号。
- 下一步：round-13 P0（端口 9213；从 round-12 顶端切出；先读 common-issues 的
  round-12 必测回归点；卡片继续按「链」设计）。

### round-13 收官（2026-10-26）

- 主题「重装链」：10 条链各嵌一颗重交互页（dropdown/hoverdrag/dialog/iframe/upload/
  写闸+captcha+pin/crawl+research/feed+parse/tab/checkout 提交闸），**95 发 13/13 工具**
  （主跑 57 + P1 补条 17 + P3 探针 21；交接点 48 发，本轮接手补 47 发）。
- **2 CONFIRM(B) + 6 REJECT**：
  - R13-001 显式 css_selector 收窄被判 extraction_collapsed（p21 A3/p22 C7）——修：
    判决尺 selector 豁免（R06-F2 同族漏项，恢复尺不动）。
  - R13-002 缓存回放丢 selector 0 匹配注记（p22 live/hit 对照）——修：envelope 存
    同源注记、回放带出（R01-021 同族漏项）。
  - REJECT：confirm 顶层措辞（E）、dialog 文案（E）、tab 寿命（D+E）、表单页无正文（D+E）、
    iframe 保存不跨调用（D+E）、captcha #status 超时（A）、顶层 frame（A）、靶站 bytes（C）。
- P4：2 个 test-first 修（2 新测试文件 4 测试，先 3 红→全绿）；相关族 520 绿。
- P5：真重启 pid 16464→**17028**；V1-V4 全 PASS（V3 阴性线上不可搭——/longaside 已由
  plain converter 接住，阴性由单测承担，如实记录）；pytest **7775 passed/23 skipped**
  （7771+4 平账）；ruff 16 基线。
- P6：四件资产 + backlog E13-E16 + decisions 更新后提交。
- **循环停止**：用户明确指示「做完 r13 就停止，不要继续循环」——P7/round-14 不启动。
  common-issues 的 round-13 必测回归点仍保留为「下一轮若继续」的输入。

### round-14 收官（2026-10-26 · 循环重启）

- **前提变化**：用户重启循环，并告知**另一位 agent 正在并发改代码/注释**——不打扰、不提交、
  不回滚其未提交改动。P0 不执行 WIP commit/切基线，直接 `git switch -c review/round-14`
  （工作树零改动），被测对象 = 当前工作树（editable，含对方在途特性）。P6 只暂存 review/
  本轮资产，不整文件吞其改动（backlog.md 含其状态更新，本轮不暂存）。
- 主题「回访与新鲜度」：10 新职业（L1×3/L2×5/L3×2），**78 发 13/13 工具**
  （主跑 63 + 补条 15 p11-p13）。主轴：cache_ttl 回放 / check 两相 / 相对 since /
  offset 续片 / recall / 会话连续性。
- **0 CONFIRM（第四个零修复轮，非连续）**——10 候选全 REJECT：
  - R14-001 **自推翻**：offset 续片 `473B of 7.0KB` 首报「不点名切片」，重读在途测试
    `test_an_offset_window_summary_names_the_slice.py` 发现逐字命中契约。教训与 r08/r09/r12
    的「量具截断」同族：判形状前先读在途测试/契约。
  - 5 条 E（recall 变体 E2/E6 复发、doc_parse summary 不说 pages、discover path_include
    种子行、discover `max_depth` 手册漂移、L1 工程词）；1 条 A（p07 按旧手册误用）；
    1 条 harness（driver 600 字截断）。
  - 正向实锤：相对 since 三档 / cache_ttl=0 回源 / check baseline→changed / offset 窗口 /
    批量读逐行 cached 全部成立（R14-010 PASS）。
- P4：无修复（0 CONFIRM）。
- P5：真重启 **pid 16212→23964**（fixture 8399 不动）；V1-V5 全 PASS（相对 since /
  缓存回放同 fetched_at / check 两相 / offset 窗口 / r13 三件回归）；全量 pytest
  **7844 passed, 23 skipped**（工作树含对方在途测试；r13 7775+69 对账平）；ruff 16 基线。
- 并发纪律：P3 的两条文档 E 正落在对方编辑路径（tools.md/troubleshooting.md），明确不碰；
  待其落地后下一轮同步手册。
- 下一步：round-15 P0（端口 9215；从本轮顶端切出；先读 common-issues 的 round-14 必测回归点；
  继续只读不写对方文件）。

### round-15 收官（2026-10-26）

- 主题「并行与隔离」：10 新职业（L1×3/L2×4/L3×3），**68 发 13/13 工具**；
  主线是双 session / 双 selector / 登录 vs 匿名 / 缓存冷热 / crawl 与 read 代际。
- **1 CONFIRM(B) + 9 REJECT**：
  - R15-001：`read {"property":"value"}` 命中空表单框 → `empty_hint` 未绑定，
    UnboundLocalError 直接上脸（p09 S2 真机实证）。已 test-first 修（1 新测试先红后绿，
    property 分支补「current value」说明，与 attribute 分支同形）。
  - R15-002 E：live 与缓存回放 `fetched_at` 跨秒差 1s（live 在响应末尾盖章、缓存存更早
    时刻），字段描述写「coincide」；方向安全，不修只记。
  - E2 复发（recall 尾斜杠变体）、R14-004/005 复发（discover 根行/max_depth 文档）、
    A×2（sys_guide topic、recall 查错词）、量具（跨轮共享缓存，cb 重名）。
- 隔离面正向实锤：双 selector 缓存键隔离、双 pin session 互不覆盖、登录 23B vs 匿名 14B、
  check 两相与 read 缓存分家、crawl 不播种 read 缓存。R15-009 PASS。
- P4：唯一修复 test-first（1 新测试，3 行修复）。
- P5：真重启 **pid 18560→27864**；V1-V5 全 PASS（V1 空框读可读说明 / V2 双 pin 隔离 /
  V3 回放不晚于 live / V4 since+窗口+check / V5 r13 三件）；pytest **7845 passed/23 skipped**
  （7844+1 平账）；ruff 16。
- 并发纪律：修复只落 actions.py 的 property 分支 3 行；该文件含对方 336 行在途改动，
  P6 用单 hunk 暂存；未碰其 models/server/quality/docs；backlog.md 仍不暂存。
- 下一步：round-16 P0（端口 9216；从本轮顶端切出；先读 common-issues 的 round-15 必测点）。
