# round-06 轮汇总报告（读什么像什么 · 抽取与交付质量 · 10 画像 · 50 发）

> 分支 review/round-06 · 基线 5820f11 · 服务 127.0.0.1:9206（v17.2 editable + R01–R05 全部修复）· 靶站 8399 共用。
> 证据：sessions/calls.jsonl（50 发）+ sessions/persona-*/ + pNN-run.log。

## 覆盖矩阵（画像 × 工具）

| 画像 | 职业/L | 发数 | 触及工具 |
|---|---|---|---|
| P01 阎立本 | 数据分析 L2 | 4 | web_read(structured/css_selector) |
| P02 邝云汉 | 翻译 L3 猎手 | 6 | web_read(focus 谱), sys_state |
| P03 乐正绫 | 编辑 L1 | 5 | web_read(html/text/article), sys_guide |
| P04 商辛 | 财务 L2 | 4 | doc_parse(真/假 PDF/xlsx) |
| P05 长孙蔷 | 电商 L2 | 5 | web_read(check/headers), web_act(checkout) |
| P06 百里萱 | 教务 L1 | 4 | web_recall(跨会话), sys_guide, web_read |
| P07 观棋 | 播客 L2 | 2 | feed_read |
| P08 荆无涯 | 爬虫 L3 猎手 | 4 | web_read(attr-selector/html/伪类) |
| P09 陶知遥 | 研究助理 L2 | 4 | web_research, web_recall |
| P10 凤九霄 | UI 设计 L1 | 4+4 | web_read, web_recall, sys_guide + 补条(search/discover/crawl/request) |

**工具覆盖 13/13**。多工具链：P05/P08/P09/P10。

## 回归点判定（common-issues 8 项）

本轮场景以抽取面为主：R03-F1 语义（P01 structured 行）、R01-009（P03/P06/P10 sys_guide）PASS；显式 timeout/archive/watch/resume 未触发（记录）。R02 家产（check diff/provenance/not_a_pdf）全部 PASS（P05/P04）。

## 摩擦清单（按感知强度）

**骂人级**
1. 【P01】**css_selector 0 匹配静默回全页**：`.no-such-class-xyz` → 整页 269 字符、content_ok=true、summary 无一字提匹配失败——缩取参数被静默丢弃且看起来像成功。**候选 R06-001（B）**。P08 的伪类第一拒是最锋利旁证：引擎在伪类处自己说了「it would have been ignored and the whole page returned」——同一个引擎知道后果，0 匹配处不说。

**骂人边缘**
2. 【P01/P08】**命中 selector 的碎片被散文尺误判**：`.card a` / `a[href^='/work/']` 正好取回用户点名的 11 行（59 字符），却 content_ok=false + 「no prose (menu fragments only)」——用户要的就是碎片，回执说它不行。**候选 R06-002（B）**。

**皱眉级（E 家族活体）**
3. 【P06/P09/P10】查询串变体三行并排（E2 家族：同页 ?t=text/?h=1/?r3v=1 各一行）——五轮样本，冗余在 L1 眼里是「怎么又三行」。
4. 【P02】focus 用了哪把引擎未在回执露脸（E 观察）。
5. 【P04】doc_parse 每 sheet 无小计（E 观察第四轮）。

**良好诚实行为**
- 伪类拒绝句把因果与后果全说完（含 would-have-been）。
- doc_parse 真 PDF：渲染不了的图点名 + 出路（metadata.figures / vision tool）。
- focus_mode 非法值三连第一拒都带合法名单；正形两档压缩比与关键词保真双达标。
- article/text/html 三档形态区分可感；structured 档 JSON 键值齐。
- feed item 正文零标签残留；check 盯价逐发 diff 在场；headers 在 metadata.response_headers 全量交付。

## 放弃点汇总

（无）

## 主观评分汇总

| 画像 | 第一印象 | 速度 | 可信 | 报错 | 等待 | 总体 |
|---|---|---|---|---|---|---|
| P01 | 3 | 4 | 3 | 3 | 4 | 3.4 |
| P02 | 4 | 5 | 5 | 5 | 4 | 4.6 |
| P03 | 4 | 5 | 5 | 4 | 5 | 4.6 |
| P04 | 5 | 5 | 5 | 5 | 5 | 5.0 |
| P05 | 4 | 5 | 5 | 4 | 5 | 4.6 |
| P06 | 4 | 5 | 4 | 4 | 5 | 4.4 |
| P07 | 4 | 5 | 5 | 4 | 5 | 4.6 |
| P08 | 4 | 4 | 4 | 4 | 4 | 4.0 |
| P09 | 4 | 5 | 4 | 4 | 5 | 4.4 |
| P10 | 4 | 5 | 4 | 4 | 5 | 4.4 |
| **均值** | 4.0 | 4.8 | 4.4 | 4.2 | 4.7 | **4.44** |

可信度分被 R06-001/002 拖累——抽取面的「沉默」比故障路径的「多话」更伤信任。

## 候选缺陷编号索引（进 P3 对抗审查）

| # | 候选 | 一句话 | 首报 |
|---|---|---|---|
| R06-001 | css_selector 0 匹配静默回全页 | 缩取参数静默丢弃、看起来像成功 | P01 |
| R06-002 | 命中 selector 的碎片被散文尺误判 content_ok=false | 要的碎片拿到了却被说不行 | P01/P08 |
| R06-003 | E2 家族：查询串变体三行并排 | 五轮样本升级在即 | P06/P09/P10 |
| R06-004 | focus 引擎名未上回执 + doc_parse sheet 小计缺 | E 观察登记 | P02/P04 |
