# round-01 计划（第 1 轮主题：广谱基线）

> 10 卡已封存于执行前（2026-10-25T21:30+08:00）。执行顺序 = 卡号。节流按宪章 §6.1，
> 其中「画像之间 3–10min」按「含报告写作时间 ≥3min 墙钟」执行（decisions.md 已记）。
> 覆盖目标：13 颗工具 ≥12；≥60% 场景落在 common-issues 高发区（回归点 1–5 已分配）。

## 回归点分配（common-issues.md 必测）

- 回归#1 login POST→jar→account 登录态 → P08-S1
- 回归#2 research timeout 分摊钳制 → P04-S2
- 回归#3 降级池如实报告 → P10-S1（+P0 自测已见一次）
- 回归#4 私网门具名拒绝 + cred_defaults → P01-S2（真实摩擦首撞）/ P07-S1（点名主机路线）
- 回归#5 提交闸全链路 → P08-S2 / P07-S3

## 画像卡（封存）

### P01 陈曦 · 数据分析师 · L2
- 职业/年龄：电商公司数据组，29 岁。常用：ChatGPT、内部 BI、Excel。
- 情境：工位，季度复盘前夜，不算急但烦。
- 真实目标：把本地两张表读出要点；把竞品博客最近几天的新文章捞出来交叉看正文。
- 验收：表字段/行数说得清；新文章列表带日期；引用正文有出处。
- 耐心：愿意等 10s/步；第 3 次失败就换思路。变卦点：中途要改按天过滤。
- 不告诉 AI：CSV 是 GBK 编码；feed 要的是「3 天内」。
- 场景：S1 doc_parse xlsx+GBK CSV；S2 feed_read 靶站 /feed.xml since=3d + 真源 atom；
  S3 链 feed→web_read /article focus；S4 既要 10 天又要只看 3 条（参数层既要又要）。
- 主攻：feed_read / doc_parse / web_read(focus)。

### P02 老周 · 后端工程师 · L3（缺陷猎手）
- 32 岁，自己搭过 MCP 客户端。深夜在家，闲。
- 真实目标：给靶站建图、爬子树、试断点续爬；顺手捅参数边界。
- 验收：地图/爬取计数可读；错参数回执「说人话」；续爬 token 语义清楚。
- 耐心：高，但记仇——每个含糊报错都记账。
- 场景：S1 web_discover sitemap=True → web_crawl（如实报没有 sitemap）；
  S2 边界四连：未知参数名 / options 袋子 / max_content_chars=999999 / offset 续取；
  S3 链 discover→crawl(search=挑战)→sys_state→web_recall；S4 「上次爬到哪」resume 语义。
- 主攻：discover / crawl / 门禁报错形状。

### P03 Sarah · 自媒体内容作者 · L1
- 26 岁，手机+笔记本混用，对 MCP 概念零认知（宿主代管）。
- 真实目标：找「猫为什么踩奶」的靠谱解释写公众号；后来改主意要写「猫呼噜」。
- 验收：给到能直接引用的 2–3 段+来源链接。
- 耐心：低，10s 没动静就嘀咕；卡住会自己摸索（求助记时长）。
- 场景：S1 web_search 中文→挑一条 web_read；S2 web_read /article focus；
  S3 不知道用什么工具→sys_guide(start)（记录卡了多久才求助）；
  S4 变卦：换关键词重搜。
- 主攻：search / read / guide。

### P04 林老师 · 高校研究员 · L3
- 41 岁，组里技术担当。晚上写综述。
- 真实目标：transformer attention 近一年综述线索；两条来源交叉；读 arxiv PDF 指定页。
- 验收：研究包逐源可读、失败源说 why；PDF 页码准确；recall 能召回早前读过的。
- 耐心：高，能等 60s，但恨引用不实。
- 场景：S1 web_search + 日期档；S2 web_research timeout=150000（回归#2）；
  S3 web_read arxiv PDF pages；S4 链 feed_read(arxiv RSS)→web_recall。
- 主攻：research / PDF / feed / recall。

### P05 王倩 · 产品经理 · L2
- 30 岁，白天会议间隙。想要竞品动态。
- 真实目标：盯靶站 /feed.xml 增量（含无日期条目）；把 /collections 列表页摸清；盯 /changing 变化。
- 验收：增量清单带日期；列表页给出结构；变更监控第二次调用如实说变没变。
- 不告诉 AI：URL 要自己问出来（S4 考察追问）。
- 场景：S1 feed_read since=；S2 链 web_discover→web_crawl(search=详情)；
  S3 web_read /changing check ×2；S4 含糊需求「那个页面上有什么新东西」。
- 主攻：feed / discover / crawl / check。

### P06 小美 · 电商运营 · L2（急躁）
- 24 岁，大促前一周，急。
- 真实目标：读多 sheet 报表；批量读 12 个商品详情页出摘要；顺手把二维码页的信息拿到。
- 验收：批量结果逐行清楚（键名 results 是考察点）；错参数报错可自救。
- 耐心：极低；单步 >20s 记摩擦；第 2 次失败想放弃。
- 场景：S1 doc_parse xlsx 多 sheet；S2 web_read urls=/work/1..12；
  S3 参数写错：url 传数字 / anti_detect 非法值；S4 /qr 页（二维码读取）。
- 主攻：doc_parse / 批量读 / 错参数。

### P07 阿凯 · 安全研究员 · L3
- 35 岁，授权测试演练 mindset（本地靶站）。
- 真实目标：摸 dhole 的安全门：私网、robots、混淆内容、写操作纪律。
- 验收：拒绝具名、出路指对；/private/secret 行为与 robots 披露一致（本轮 IGNORE_ROBOTS=1，robots 路径不可观测，注明）；
  /shell/obfuscated 的 untrusted_content 标记出现；无 confirm 的 submit/POST 一个都不出门。
- 场景：S1 私网门（cred_defaults 点名主机路线）+ /private/secret；
  S2 /shell/obfuscated 注入观察；S3 /challenge/text read→fill→submit 同链；
  S4 web_request /checkout POST confirm=true + sys_state 对账。
- 主攻：security gates / request / act。

### P08 Devin · 外包开发 · L2
- 28 岁，替客户做集成，照着感觉用。
- 真实目标：登录态跑通（POST /login→读 /account）；上传文件走完提交；拖拽交互。
- 验收：同 session_id 读到「欢迎回来」；上传回执可读；hover/drag 生效。
- 场景：S1 登录链（回归#1）；S2 web_act upload→提交闸→confirm（回归#5）；
  S3 /hoverdrag hover+drag；S4 /captcha 读取（OCR 档是否出手）。
- 主攻：act / request / session。

### P09 方老师 · 学术编辑 · L1
- 47 岁，改稿为主，AI 用得浅。
- 真实目标：读本地 docx 给摘要；查一个术语的权威解释；有的页看不懂就算了。
- 验收：摘要可用；解释带来源；放弃点如实。
- 场景：S1 doc_parse sample.docx；S2 web_search→web_read；
  S3 sys_guide 求助；S4 「算了不用了」放弃点。
- 主攻：doc_parse / read / guide。

### P10 Helen · 市场增长 · L2（既要又要还要 + 跨会话）
- 33 岁，做投放周报。
- 真实目标：搜索只信 baidu+sogou、只要本周；又想顺手看全部引擎；召回上周读过的页。
- 验收：engines 收窄生效；矛盾要求被参数层拒或收窄且说清；recall stale 标注。
- 场景：S1 web_search engines=[baidu,sogou] freshness=week（回归#3）；
  S2 既要还要三连；S3 web_recall 跨会话；S4 链批量 read→feed_read(HN)→汇总。
- 主攻：search filters / recall / 链。
