# round-01 轮汇总报告（广谱基线 · 10 画像 · 101 发）

> 分支 review/round-01 · 基线 3cf035e · 服务 127.0.0.1:9201（v17.2 editable，DHOLE_IGNORE_ROBOTS=1, DHOLE_USAGE_LOG=1）· 靶站 127.0.0.1:8399。
> 证据：sessions/calls.jsonl（101 发台账）+ sessions/persona-01..10/*.json（逐发 wire 回执）+ reports/report-persona-01..10.md。

## 覆盖矩阵（画像 × 工具）

| 画像 | 职业/L | 发数 | 触及工具 |
|---|---|---|---|
| P01 陈曦 | 数据分析师 L2 | 17 | doc_parse, feed_read, cred_defaults, web_read |
| P02 老周 | 后端 L3 猎手 | 18 | web_discover, web_crawl, web_read, sys_state, web_recall |
| P03 Sarah | 自媒体 L1 | 9 | web_search, web_read, sys_guide |
| P04 林老师 | 研究员 L3 | 6 | web_search, web_research, web_read, feed_read, web_recall |
| P05 王倩 | 产品经理 L2 | 7 | feed_read, web_discover, web_crawl, web_read |
| P06 小美 | 电商运营 L2 | 7 | doc_parse, web_read, (OCR 档) |
| P07 阿凯 | 安全研究 L3 | 15 | sys_state, web_read, web_act, web_request |
| P08 Devin | 外包开发 L2 | 11 | web_request, web_read, web_act |
| P09 方老师 | 学术编辑 L1 | 5 | doc_parse, web_search, web_read, sys_guide |
| P10 Helen | 市场增长 L2 | 6 | web_search, web_recall, web_read, feed_read |

工具覆盖 **13/13**（cred_defaults 3 / doc_parse 6 / feed_read 13 / sys_guide 4 / sys_state 3 / web_act 12 / web_crawl 8 / web_discover 3 / web_read 34 / web_recall 4 / web_request 3 / web_research 1 / web_search 7）。多工具链画像 ≥3 颗：P01/P02/P04/P05/P08/P10（6 个 ≥3 链）。失败/被挡/参数写错场景：P01（私网门+since 格式）、P02（边界四连+resume 照抄）、P03（robots 拒）、P06（类型/枚举拒绝）、P07（robots+挑战页）、P08（drag 三连拒）。

**回归点判定**：#1 登录链 PASS；#2 research timeout 分摊 PASS（150s→2/2 源）；#3 降级池如实 PASS（P01/P10 双确认）；#4 私网门具名拒绝+cred_defaults PASS（P01 真实撞门自愈）；#5 提交闸全链路 PASS（P07/P08 双链）。

## 摩擦清单（按用户感知强度排序）

**骂人级**
1. 【P07/P03】robots 自述矛盾：`DHOLE_IGNORE_ROBOTS=1` 进程级豁免**没有**绕过靶站 robots 点名条目（/private/secret → robots_disallowed；sohu 同形状），而缓存回执却自称「fetched under: bypassed (process-wide)」——env 到底管不管、谁管，工具自己两个说法（候选 R01-005）。
2. 【P07】挑战页「同链 read→fill→submit」语义无解：fill 只吃静态文本，每导航换码，无 url 续操作又被拒——机械链路全通、值永远传不进去，5 试 ≈1min 后放弃（候选 R01-006）。
3. 【P09】web_read 超时预算不是硬杀：45.8s 总耗时，自述「ran 14420ms past the budget and its 1500ms grace——something inside this call did not stop when told」（候选 R01-007）。

**骂人边缘**
4. 【P02】resume_token 自荐形状照抄即错：next_action 教「pass resume_token='…' to web_crawl」，不带 url 就撞 `Missing required argument(s): ['url']`（候选 R01-003）。
5. 【P10】feed_read 慢源死刑梯子：hnrss.org 1s 超时→2s 重试又超时，note 还说「waiting longer is not the fix here」——慢而活的源被误诊（候选 R01-008）。
6. 【P08】drag 形状三连拒才给全正形；snapshot 回执只有页面文本、无编号元素列表——选择器没有发现机制闭环。
7. 【P06】批量读每页钳制参数名分家：crawl 记得的 `max_content_chars_per` 在 read 上不存在（实际 read 用全局 `max_content_chars`），E1 键名直觉问题的姊妹篇。

**皱眉级**
8. 【P02/P03/P04】同 URL 多变体不去重：搜索同文两行（跟踪参数）、recall 同 URL 两行且 stale 标注互相打架。
9. 【P02/P05】crawl search 过滤 0 条的措辞像「爬满了」（max_pages reached），且 search 只匹配 URL/标题不匹配正文的语义从未声明（三画像三次踩）。
10. 【P01】feed 批量行级错误顶层只说「1 rows errored」，不点名哪个 URL。
11. 【P03】sys_guide start 开头「dhole is eleven tools」——实际 13 颗（手册计数漂移）。
12. 【P10】recall 低分命中出榜：relevance 0.0642、3 词只中 1 仍返回；没有她的历史时不如说「没找到」。
13. 【P02】crawl 静默开 stealthy 浏览器：sys_state 里躺着会话，回执没吭声。
14. 【P02】offset 模式 summary/total_extracted_chars 描述整页文档而非本片（7.0KB vs 779B 首眼误读）。
15. 【P03】搜索结果不带「此站 robots 不放行」徽标，点进去才挨拒。
16. 【P04】timeout 钳制消息「89→30」用内部精度（应「90s→30s/attempt」）。

**无感/环境（不进修复队列）**
- since 只收 ISO 不收「3d」（报错给了两种合法格式示例，自愈零成本——设计内）。
- allow_private 全开回环、不能点名主机（出路可用，粒度问题）。
- QR 解码不在能力内（诚实拒绝）；引擎池冷却（环境 C）；hnrss 偶发慢（环境）；mozilla 抽取空标题噪声（轻微）。

## 放弃点汇总

| 画像 | 放弃点 | 强度 | 是否工具之过 |
|---|---|---|---|
| P07 | 挑战页 5 试后放弃 | 骂人 | 是（语义无门，R01-006） |
| P08 | 拖拽端到端放弃（ID 无门） | 骂人边缘 | 半（形状可学，发现机制缺失） |
| P09 | 46s 超时页放弃 | 骂人边缘 | 是（预算越界，R01-007） |
| P03 | 日期深挖放弃 | 无感 | 否（该页无日期） |
| P06 | QR 放弃 | 皱眉 | 否（能力边界，拒绝诚实） |

## 主观评分汇总

| 画像 | 第一印象 | 找答案速度 | 可信度 | 报错可读 | 等待感受 | 总体 |
|---|---|---|---|---|---|---|
| P01 | 4 | 4 | 5 | 4 | 5 | 4.3 |
| P02 | 5 | 4 | 5 | 5 | 5 | 4.6 |
| P03 | 3.5 | 4 | 4 | 4 | 5 | 4.0 |
| P04 | 5 | 5 | 4 | 4 | 5 | 4.6 |
| P05 | 4 | 4 | 5 | 4 | 4 | 4.2 |
| P06 | 5 | 4 | 5 | 5 | 5 | 4.6 |
| P07 | 4 | 3 | 4 | 5 | 4 | 4.0 |
| P08 | 5 | 3 | 5 | 4 | 4 | 4.2 |
| P09 | 4 | 3 | 5 | 5 | 2 | 3.8 |
| P10 | 4 | 4 | 4 | 5 | 4 | 4.2 |
| **均值** | 4.4 | 3.8 | 4.7 | 4.5 | 4.4 | **4.29** |

共性亮点：报错全部「人话+出路」（4.5 分），计数与对账诚实（4.7 分）。共性短板：多步/长任务的速度感（3.8 分）——超时预算越界、挑战页语义、三连拒都在这里。

## 能力边界遭遇（记录并接受）

{{SECRET_…}} 占位符回打是设计（能回打不泄原值）；stealthy fetcher 升级 6.3s（原因未一句话点名→皱眉级 #13）；焦点模式 best 块自疑（manual 轮已见）；OCR thin 对二维码无能为力（能力边界）。

## 与「工具视角」的差异

工具视角全部「正确」的地方，用户视角仍有四处受伤：①行级错误藏在 rows[].error（工具：批量通用形状；用户：哪个错了要展开）；②offset 说文档不说片（工具：字段语义如此；用户：我只要那 800 字）；③crawl 0 条借 max_pages 措辞（工具：模板复用；用户：误导）；④recall 低分出榜（工具：阈值内就给；用户：不如不给）。

## 改进清单（按感知强度，非工程难度）

1. 名副其实或明说：`DHOLE_IGNORE_ROBOTS` 与点名条目的优先级写进 env 帮助与报错（R01-005 裁决后修）。
2. 挑战页同链取值原语（read 结果可被 fill 引用），或明示「跨调用+snapshot 手动搬运」为唯一正解（R01-006）。
3. 超时预算到点真收手（R01-007）。
4. resume_token 自荐形状照抄能跑（R01-003）。
5. feed 慢源梯子放宽 + 「等更久没用」只在真断网时说（R01-008）。
6. 动作形状拒绝第一次给全；snapshot 带编号元素。
7. search/recall 同 URL 去重或标 variants。
8. crawl search 0 条说实话 + 语义写进参数描述。
9. 手册工具计数与 tools/list 同源（eleven→13）。
10. feed 行级错误顶层点名 URL；recall 低分不出榜；钳制消息用调用方单位。

## 候选缺陷编号索引（进 P3 对抗审查）

| # | 候选 | 一句话 | 首报 |
|---|---|---|---|
| R01-001 | feed 行级错误顶层不点名 URL | 批量 1 行错，summary 只说 rows errored | P01 |
| R01-002 | crawl search 0 条措辞+语义未声明 | 「max_pages reached」像爬满了；search 不匹配正文没说 | P02/P05 |
| R01-003 | resume_token 自荐形状缺 url | 照抄 next_action 撞 Missing url 门 | P02 |
| R01-004 | 同 URL 多变体不去重 | 搜索同文两行；recall 两行 stale 打架 | P02/P03/P04 |
| R01-005 | IGNORE_ROBOTS 与点名条目矛盾 | env=1 仍拒点名条目，缓存自述 bypassed | P07/P03 |
| R01-006 | 挑战页同链取值无解 | fill 静态值 × 每导航换码 × 无 url 续操作 | P07 |
| R01-007 | 超时预算非硬杀+自白失控 | 45.8s，过预算 14.4s「did not stop when told」 | P09 |
| R01-008 | feed 慢源 1s/2s 梯子+误诊 note | hnrss 双超时，「等更久没用」误导 | P10 |
| R01-009 | sys_guide 工具计数漂移 | 「eleven tools」实际 13 | P03 |
| R01-010 | 动作形状三连拒+snapshot 无编号 | drag 三拒才给全；选择器无发现机制 | P08 |
| R01-011 | offset 模式 summary 说文档不说片 | 7.0KB vs 779B 首眼误读 | P02 |
| R01-012 | crawl 静默开浏览器 | sys_state 有会话，回执未点 | P02 |
| R01-013 | timeout 钳制「89」内部精度 | 应显示 90→30 | P04 |
| R01-014 | recall 低分命中出榜 | 0.0642 + 1/3 词命中仍返回 | P10 |
