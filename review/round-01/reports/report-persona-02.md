# 画像报告 · P02 老周（后端工程师 · L3 缺陷猎手）· round-01

> 深夜闲逛，给靶站建图爬子树、捅参数边界、试续爬。18 发（p02-*)，sessions/persona-02/。

## 场景实录

### S1 建图爬取（web_discover ×2 + cred_defaults ×1 + web_crawl ×1）
- 首发 discover 撞私网门（服务刚重启过，豁免清零）→ 按报错 cred_defaults 放行 → 重试。
- 「no sitemap.xml found (robots.txt had no Sitemap directive and /sitemap.xml returned nothing)」——**没有就说没有，还写了查过哪两处**。好。
- web_crawl search='详情' → 「filtered to 0 URL(s); crawled 0 page(s) … stopped: max_pages reached」。0 条是事实，但**明明是过滤后队列为空，措辞却像爬满了**。记摩擦。

### S2 边界四连（web_read ×4）
- 未知参数 format=markdown → 「Unsupported argument(s) for web_read: ['format']. Supported: [全列表]」✓
- options 袋子 → 「This tool has no options object - its keys are top-level parameters … (this call's max_content_chars can move out as-is)」——**把我的参数怎么搬都写了**。✓
- max_content_chars=999999 → 不拒，**钳制并披露**：「clamped 999999->200000 (supported range 500-200000)」✓
- offset=500 + max_content_chars=800 → 回 content 795 字、next_offset=1295（后证一致）✓

### S3 链路（web_crawl + sys_state + web_recall）
- search='挑战' 又是 0 条（同 S1 措辞问题）。
- sys_state：cache 47 条（hits 4 / misses 8）、sessions 里躺着**一个 stealthy browser 会话**（crawl 悄悄开的，回执里没吭声）。「谁开的浏览器？翻账本才知道」——记摩擦。
- web_recall「挑战 抄写」→ **空**（缓存里确实没有匹配页，空得合理）。

### S4/S5 续爬语义（web_crawl ×2）
- /collections max_pages=3 → 3 页后停，next_action：「9 URL(s) were discovered but never sent … **pass resume_token='e4590e39e63a:0'** to web_crawl」。
- 照抄（不带 url）→ **「Missing required argument(s) for web_crawl: ['url']」**——自荐的调用形状过不了门。加 url 重发 → 「plan resumed at 1-9 of 9: 9 ok, 0 with error」，**9 条队列全部送达且不重抓**，占位符回打被正确替换。✓（那 {{SECRET_…}} 其实是 secretbox 占位符，能回打是设计；但 next_action 少写了必须带 url。）

### S6–S8 补测（path_include / recall 换词 / offset 对照）
- path_include=['/work'] max_pages=8 → 8 页全中 ✓（next_action 的另一条出路是真的）。
- recall「抓取礼仪 默认」→ 命中 /article 两行 + GitHub 一行；**同一 URL 出现两行**（两个缓存变体没去重）。
- offset 对照：offset=0/800 → content 779 字；offset=1295/800 → 774 字；**分页账目全对**。误读的根源：summary 说「7.0KB markdown」、total_extracted_chars=7157，说的都是**整页文档**，不是本片。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因 | 严重度 |
|---|---|---|---|---|---|
| 21:48 | S1 撞门 | 「重启一次就要重新报备？」 | 12s | cred_defaults 进程内存态（D） | 皱眉 |
| 21:48 | S1/S3 crawl 0 条 | 「没货就说没货，说 max_pages reached 干嘛」 | 0 | 过滤空与预算停共用措辞（猜测：同一 summary 模板） | 骂人边缘 |
| 21:49 | S3 隐身浏览器 | 「谁开的浏览器？」 | 0 | crawl 升级 fetcher 开会话，回执未提（E） | 皱眉 |
| 21:50 | S5 resume 照抄 | 「它让我传 token 却没说要带 url」 | 8s+8s | next_action 措辞 vs 门校验不一致（候选 R01-003） | 骂人边缘 |
| 21:52 | S8 offset 首眼 | 「要 800 给 7000？」 | 3min（反复对账） | summary/total_extracted_chars 描述文档而非本片（E 命名） | 骂人边缘 |

## 放弃点

无（L3 皮厚，全部追到底）。

## 主观评分

- 第一印象 5：每个拒绝都带全名单和出路，自愈零文档。
- 找答案速度 4：crawl 过滤 0 条时多绕两步。
- 答案可信度 5：分页/计数/恢复逐条对账全对。
- 报错可读性 5：「options 袋子」那句是教科书级。
- 等待感受 5：本地靶站全部亚秒。
- 总体满意 4.6。

## 能力边界遭遇

{{SECRET_…}} 占位符第一眼像泄漏、第二眼像设计（能回打、不泄原值）——边界处理得当，但**手册若不写会被每个 L3 疑心一遍**。

## 改进清单（按感知强度）

1. 【我想骂人→希望】next_action 自荐的调用形状必须**照抄就能跑通**（resume_token 行补「带上原 url」或门放行无 url 的 resume）。
2. 【希望】offset 模式的 summary 与 total_extracted_chars 说清「文档 vs 本片」（如「chunk 779B of 7.0KB」）。
3. 【希望】crawl 过滤空时直说「no URL matched search=…」，别借用 max_pages 措辞。
4. 【希望】crawl 开浏览器时在回执点一句（fetcher_used/escalation_path 已有的话，让它更显眼）。
5. 【希望】web_recall 同 URL 去重或标注「second variant」。

## 附录

关键证据：persona-02/05（options 拒绝）、08（offset+cap 首发误读）、12（resume_token next_action）、13（照抄撞门）、16/17（分页对账）、18（resume+url 成功）。
