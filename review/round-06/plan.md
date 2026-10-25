# round-06 计划（P0 封存）· 主题：读什么像什么（抽取与交付质量）

> 分支 review/round-06 · 基线 5820f11（round-05 收官提交）· 服务 127.0.0.1:9206 + 靶站 8399。
> 主攻**抽取面**：extraction_type 全谱（markdown/html/text/article/structured/check）、css_selector、
> focus 三档、doc_parse 全格式（xlsx/csv/pdf/假 PDF/docx?）、交付形态（真人可直接用 vs 需二次加工）。
> 回归压力：common-issues 8 项必测绑定。

## 十张画像卡（封存，执行前不改）

### P01 阎立本 · 数据分析师 L2
- 目标：structured 档抓表格页（/collections 的 .card 结构）；css_selector 缩取对照。
- 验收：结构化行的键值与页面一致；selector 说清「匹配几处」。
- 不告诉 AI：会传一个匹配 0 处的 selector。
- 主攻：web_read(structured/css_selector)；绑定 R03-F1 语义。

### P02 邝云汉 · 技术翻译 L3（猎手）
- 目标：focus 三档同一页对照（压缩比与关键词保真）；focus_engine 显式指定。
- 验收：三档各自申报用了哪把刀；关键词在不在。
- 不告诉 AI：给的 focus 词是页尾冷词。
- 主攻：web_read(focus/focus_engine)。

### P03 乐正绫 · 自媒体编辑 L1
- 目标：html/text/article 三档同一页对照——L1 视角「哪个像人话」。
- 验收：三档形态区分可感；不产生「全是标签」的交付。
- 主攻：web_read(extraction_type 谱)；绑定 R01-009。

### P04 商辛 · 财务 · L2
- 目标：doc_parse 真 PDF（tests/*.pdf）+ 假 PDF 对照；多 sheet 字数小计（E 愿望清单回归）。
- 验收：真 PDF 抽取页数/字数；假 PDF not_a_pdf 具名（R02 良好行为）。
- 不告诉 AI：一个文件是改扩展名的文本。
- 主攻：doc_parse。

### P05 长孙蔷 · 电商运营 L2
- 目标：商品页列表 → check 档盯价格 → change 后的 diff 可读性；include_headers 对账。
- 验收：diff 窗口人类可读；headers 与 curl 对照一致。
- 主攻：web_read(check/include_headers) + web_request；绑定 P08 provenance 家产。

### P06 百里萱 · 教务老师 L1
- 目标：跨会话课表存取（recall）；guided 路径全走 sys_guide。
- 验收：找回可用；L1 不迷路。
- 主攻：web_recall + sys_guide + web_read；绑定 R01-009/E12。

### P07 观棋 · 播客主播 L2
- 目标：feed 的条目正文抽取质量（item 内 HTML 清洗）；audio 链接的呈现。
- 验收：feed item 正文不是一坨标签。
- 不告诉 AI：feed 里有一条正文是纯 HTML 块。
- 主攻：feed_read + doc_parse（show notes 导出）。

### P08 荆无涯 · 爬虫工程师 L3（猎手）
- 目标：css_selector 深水（伪类/嵌套/属性选择器）、html 档的原始性（不给洗过的）、truncated 语义。
- 验收：选择器引擎与浏览器行为一致；html 档字节级原样（除声明外）。
- 不告诉 AI：会用 ::after 伪类。
- 主攻：web_read(css_selector/html) + web_crawl。

### P09 陶知遥 · 研究助理 L2
- 目标：web_research 的证据包质量（引用可回链、逐源失败隔离）+ recall 语义查准。
- 验收：每条引用能点回去；两源失败不污染第三源。
- 主攻：web_research + web_recall；绑定 R02 家产。

### P10 凤九霄 · UI 设计师 L1
- 目标：读设计规范页的 table/清单形态；跨会话找回配色段。
- 验收：表格不散架；找回的是那段不是别的。
- 主攻：web_read + web_recall(跨会话) + sys_guide。

## 覆盖核对

- 职业 10 全新；L3×2（P02/P08）、L1×3（P03/P06/P10）✓。
- 工具 13/13 目标：web_read 全员 / doc_parse（P04/P07）/ feed_read（P07）/ web_research（P09）/ web_recall（P06/P09/P10）/ web_act（P05 旁支 checkout）/ web_request（P05）/ web_crawl（P08）/ web_discover（P08 旁支）/ cred_defaults（全员私网）/ sys_state（P02/P08）/ sys_guide（P03/P06/P10）/ web_search（P09 旁支）✓。
- 回归绑定：P01(R03-F1)/P03(R01-009)/P04(良好行为组)/P05(provenance)/P06(R01-009/E12)/P09(R02)/P10(R01-009) ✓ ≥60%。

## 执行参数

- 节流：画像间 ≥18s；场景间 5-8s。
- 端口：9206；env DHOLE_IGNORE_ROBOTS=1（不可观测——注明）。
