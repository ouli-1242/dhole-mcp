# round-15 计划（P0 封存 · 主题：并行与隔离）

> 分支 review/round-15 · 基线 ea25700（round-14 收官）· 端口 9215(dhole)/8399(靶站)。
> **并发前提**：工作树仍有另一位 agent 的未提交改动；本轮只读不写其 src/tests/skills 文件，
> P6 只暂存 review/ 资产。被测对象 = 当前工作树（editable）。

## 主题：并行与隔离（十个真实“同时干两件事”的职业场景）

上一轮验证“回访”；本轮盯“两个上下文同时在手上”时产品是否互相污染：
会话隔离（pin / session_id）、缓存冷热交错、同 URL 不同 selector/提取类型、
check 与 read 交错、feed 与长文交错、两条搜索/召回流交错、doc_parse 与 read 交错。
不预读源码，按用户视角自然使用。

## 画像卡（P0 封存，不得事后改写）

| # | 画像 | 层级 | 手上同时有的两件事 | 绑定工具（覆盖） |
|---|---|---|---|---|
| P01 | 医学院图书馆员 沈砚 | L2 | 论文检索 session 与门诊指南 session 并行；同一页两种 selector | web_read, sys_guide, sys_state |
| P02 | 专利审查员 陆知微 | L3 | 冷读与缓存回放交错；check 与 read 同 URL 交错；两种 extraction_type | web_read, web_request |
| P03 | 咖啡店主 阿吉 | L1 | 两条订阅流 + 一篇长文分片（offset 窗口） | feed_read, web_read |
| P04 | 供应链分析师 罗盘 | L2 | 批量读与 recall 交错；登录会话与匿名读交错 | web_read, web_recall, web_request |
| P05 | 数据记者 程野 | L3 | 两次 research 并行；crawl 与 read 同 URL 代际 | web_research, web_crawl, web_read |
| P06 | 兽医 林澄 | L2 | 两份本地文件（PDF/XLSX）交错解析；parse 后读同一文件 | doc_parse, web_read |
| P07 | 博物馆策展人 闻溪 | L2 | discover 地图与 crawl 队列交错；两个 session 的地图隔离 | web_discover, web_crawl, web_read |
| P08 | 体育数据分析师 顾准 | L3 | 广谱 search 后批量读；同 URL 换 selector 回读 | web_search, web_read, feed_read |
| P09 | 园艺师 花婶 | L1 | 两个具名 session 的表单/页签状态隔离；pin 与不 pin | web_act, sys_state |
| P10 | 编剧 江照 | L1 | 两个 session 的 recall 写入/读交错；offset 续片 | web_recall, feed_read, web_read |

## 每画像的固定动作

1. 先置 `cred_defaults {"set": {"allow_private": true}}`（真重启后进程内豁免清零）。
2. 3-4 个场景、5-8 发；场景之间 `pause(3-6)` 让缓存/时间窗可辨。
3. 收尾 sys_state 或 sys_guide（至少 P01/P09）。
4. 台账 `sessions/calls.jsonl`，回执逐发落盘 `sessions/persona-NN/`。

## 必带回归（来自 common-issues round-14 必测点）

- R1 相对 since 三档：回执 `since` 绝对 ISO、窗口外计数、undated 放行。
- R2 `cache_ttl=0` 真回源；正数回放 `cached=True` 且 `fetched_at` 同值。
- R3 check 两相：baseline recorded → changed=True / unchanged。
- R4 offset 续片 summary `<本窗> of <总量> markdown`（尾窗也说是切片）。
- R5 r13 三件：css 收窄不判 collapse / 缓存 selector 注记 live+hit / feed max_items=-3 具名拒绝。
- R6 discover `max_pages`/`path_include` 谓词 + 种子行；手册 `max_depth` 漂移待并发 agent 落地。

## 完成判据

- 13/13 工具至少各一发；≥50 发；10 份画像报告 + 轮报告；P3 逐条攻击后定论；
  P4 只修 CONFIRM；P5 真重启 + 复验 + 全量 pytest + ruff 16；P6 资产与提交。
