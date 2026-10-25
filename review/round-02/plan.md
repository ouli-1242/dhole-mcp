# round-02 计划（第 2 轮主题：边缘与刁难）

> 10 卡已封存于执行前（2026-10-25T23:5x+08:00）。执行顺序 = 卡号。节流按宪章 §6.1。
> 服务：127.0.0.1:9202（round-02 专属端口；DHOLE_IGNORE_ROBOTS=1, DHOLE_USAGE_LOG=1）· 靶站 8399 共用。
> 覆盖目标：13 颗工具 ≥12；≥60% 场景落在 common-issues 高发区（含 round-01 修复点回访）；≥40% 新区。

## 回归点分配（common-issues.md round-01 更新后必测）

- 回归#1（R01-008 修后）feed_read 不带 timeout 的 20s 预算 + 慢源双倍重试 → P05-S1
- 回归#2（R01-006 修后）挑战页正形可发现性：不给提示，看 fill note / 报错能否自救 → P08-S1
- 回归#3（R01-010 修后）drag {from,to} 第一拒即全形 → P08-S2
- 回归#4（R01-003 修后）resume 自荐形状照抄能跑 → P04-S2
- 回归#5（E1 观察）批量读行键 results（未修，如实记录调用方踩坑）→ P04-S3
- 回归#6（R01-005 修后延伸）per-call ignore_robots=true 名义路径（env 修好但 per-call 档从未真实验证）→ P10-S2
- 回归#7（E2 观察）同 URL 变体不去重 / recall stale 打架（未修，跨轮复发标注）→ P10-S3
- 回归#8（R01-001 修后）feed 顶层点名出错 URL → P05-S3
- 回归#9（R01-009 修后）sys_guide 计数 13 且点名全部工具 → P03-S3

## 新探查区（≥40%）

watch run（变更监控 CLI 面）· nested_selector / structured 一次拿列表+详情 · archive.org 第三档 · iframe 动作（17.2 动作面）· headed=true 驻留 · 加密/损坏 PDF 诊断链 · 多语言站 · check urls= 批量 · max_pages=40 + deadline_ms 边界 · doc_parse 坏文件。

## 画像卡（封存）

### P01 何工 · 运维/SRE · L3（缺陷猎手 #1）
- 38 岁，值班 mindset，自己写监控脚本。
- 情境：夜间值班，顺手把 dhole 当探针。
- 真实目标：给靶站 /changing 建 watch 并触发一次变更；服务重启窗口里会话/凭据行为的对账；引擎熔断冷却的语义观察。
- 验收：watch 的 exit code 语义（0/1/2）与实际一致；对账说得清哪些是进程内存态。
- 耐心：高；每个语义含糊处记账。
- 场景：S1 `dhole watch run` 建单 + 改靶站 + 触发；S2 杀 9202 进程重启 → cred_defaults/会话/缓存状态对账；S3 web_crawl max_pages=40 + deadline_ms=30000 边界（谁先停、停因如实）；S4 熔断冷却期重试（so360/shenma 冷却窗内再发，如实说 circuit）。
- 主攻：watch / sys_state / crawl 边界。

### P02 杜蘅 · 法务/合规 · L2
- 35 岁，审合同条款。
- 真实目标：读一份在线 PDF 的指定页并引用原文；两份来源交叉同一术语；找旧版本条款对照。
- 验收：页码引用准确；archive 快照带 archived_at 且能分辨"快照不是现状"。
- 场景：S1 web_read PDF pages= 精确引用；S2 两源交叉 + fetch_hint 读法；S3 web_read 硬墙 → archive.org 第三档（archived_at 语义）；S4 check urls= 批量盯两份文本。
- 主攻：web_read(pages/archive/check)。

### P03 小北 · 17 岁学生创作者 · L1
- 高中生，做游戏攻略帖，AI 用得浅。
- 真实目标：查某游戏更新内容；被挡就想放弃；不知道用什么工具。
- 不告诉 AI：其实想要的是官方补丁说明页。
- 场景：S1 search 中文 → 挑一条读；S2 读被 robots 拒 → 看会不会照 next_action 换源（L1 耐心 10s）；S3 卡住求助 sys_guide(start)（记录卡多久 + 能不能读懂 13 颗的路标）；S4 变卦换关键词。
- 主攻：search / read / guide（回归#9）。

### P04 覃川 · 数据工程师 · L3（缺陷猎手 #2）
- 30 岁，写采集管道，看 dhole 能不能替手写爬虫。
- 真实目标：nested_selector 一次拿「列表页→每个条目详情」的结构化记录；批量 15 URL；offset 续取对账。
- 验收：nested 记录逐条可对、截断如实；批量 15 的上限行为可预期；offset 账目精确到字符。
- 场景：S1 web_read nested_selector + nested_fields 靶站 /collections；S2 crawl max_pages 停 → **照抄 resume 形状**（回归#4，不带任何提示）；S3 批量 urls= 12 条 + 边界 13/15 条（回归#5 键名 results）；S4 offset 三连取对账（回归 E3 语义如实记录）。
- 主攻：nested / 批量 / offset。

### P05 苏姗 · 外贸业务员 · L2
- 29 岁，跟单，中英日三语站点都看。
- 真实目标：三个供应商博客的增量一口气看完（feed 并发）；英文关键词找中文站；feed 里挂了一个死源。
- 场景：S1 feed_read 三源（含一个 404）——回归#1（不带 timeout）+ 回归#8（顶层点名死源）；S2 search 英文词 → 中文站结果可用性；S3 读一页日文站 → 抽取与乱码观感；S4 既然要「只要最近 3 条」又要「全部引擎」的刀难二连。
- 主攻：feed 并发 / search 跨语种。

### P06 老范 · 45 岁建材店老板 · L1（极低耐心）
- 手机上用 AI，从不看长文本。
- 真实目标：查一种水泥的标号含义；页面打不开就算了。
- 场景：S1 search 错字输入（「水尼标号」）；S2 读第一条（若慢 >20s 记弃）；S3 一次 4xx → 直接放弃（记录放弃点是否被工具的 next_action 拉回）；S4 让 AI 自己概括页面而不给指令。
- 主攻：L1 真实感 / 错误自救力。

### P07 邱原 · 三甲医院主治医师 · L2
- 41 岁，写综述，时间碎。
- 真实目标：web_research 一次出 4 源证据包；对「不能引用」的标注敏感；PDF 指定页核对。
- 验收：失败源说 why 且不拖垮整包；引用出处在包里能点名。
- 场景：S1 web_research max_sources=4 timeout=120000；S2 对包内低可信源挑刺（source_type 标注读法）；S3 arxiv PDF pages 交叉；S4 既要"近一年"又要"综述型"的二连刀难（freshness=year + 查询构造）。
- 主攻：research / 可信度标注。

### P08 周奕 · 自动化测试工程师 · L2（刁难组）
- 27 岁，写 e2e 的，看到「动作面」就想拿夹具试。
- 真实目标：挑战页**不给任何提示**走通（回归#2——修后的 fill note 是不是真的能自救）；hover/drag 用 snapshot 编号 + expect 校验；iframe 里的按钮。
- 场景：S1 /challenge/text 同链 read→fill→submit（观察第一错的报错教不教形状）；S2 /hoverdrag snapshot 编号列表 → index 寻址 + expect 名字校验 + drag 正形（回归#3 第一拒全形观察）；S3 iframe 内 fill+click（17.2 动作面）；S4 刁难：空 actions 数组 / 未知动作键 / actions+confirm 顶层三连。
- 主攻：act 全动作面 / 刁难。

### P09 韩青 · 财务会计 · L2
- 33 岁，报销季，处理各种格式的表。
- 真实目标：读本地 xlsx 多 sheet + GBK CSV 汇总要点；坏文件（加密 PDF / 乱码 csv）想知道"是文件坏了还是工具不行"。
- 场景：S1 doc_parse xlsx 多 sheet + GBK csv（对账行数）；S2 doc_parse 加密 PDF → encrypted_pdf 诊断链与出路；S3 doc_parse 一个非 PDF（后缀骗人）→ not_a_pdf；S4 web_read 批量 3 个静态发票样例页汇总。
- 主攻：doc_parse 诊断链 / 批量。

### P10 岑砚 · 研究生 · L2（跨会话长任务）
- 25 岁，断断续续做文献调研，隔天回来继续。
- 真实目标：召回昨天读过的页（跨会话）；被 robots 挡的名站用 per-call 档解决（回归#6）；变体重复自己会发现吗（回归#7）。
- 场景：S1 读 3 个真实页面（含一个 robots 挡的）→ 次序间插 recall；S2 sohu/知乎类 robots 拒 → **照 next_action 用 ignore_robots=true**（per-call 档首次真实验证；用户立场=有权限的合规判断）；S3 recall 召回 + 同 URL 变体观察（E2 如实）；S4 隔"天"（轮内长间隔模拟）回来 recall stale 标注语义。
- 主攻：recall 跨会话 / robots per-call 档。

## 覆盖预算

- 工具 13 颗 ≥12：search(03/05/06) read(多) act(08) request(08 刁难延伸) crawl(01/04) discover(04/01) feed(05) research(07) recall(10) doc_parse(02/09) guide(03) state(01) cred_defaults(01/04)。
- 多工具链 ≥3 颗：P01/P04/P05/P07/P08/P10（6 个）。
- 失败/被挡/参数错场景 ≥2：P03-S2、P06-S3、P08-S4、P09-S2/S3、P10-S2（≥5）。
- 新写场景比例：100%（本计划全部新写）。
