# round-05 轮汇总报告（慢与坏 · 故障路径 · 10 画像 · 53 发）

> 分支 review/round-05 · 基线 959ed79 · 服务 127.0.0.1:9205（v17.2 editable + R01–R04 全部修复）· 靶站 8399 共用。
> 证据：sessions/calls.jsonl（53 发）+ sessions/persona-*/ + pNN-run.log。

## 覆盖矩阵（画像 × 工具）

| 画像 | 职业/L | 发数 | 触及工具 |
|---|---|---|---|
| P01 邵岐 | SRE L3 猎手 | 6 | web_read(故障三连/梯子), cred_defaults, sys_state |
| P02 梅疏影 | 编辑 L1 | 3 | web_search, sys_guide |
| P03 蔺相宜 | 法务 L2 | 4 | web_read(404→archive), cred_defaults, sys_state |
| P04 弭兵 | 数据工程 L3 猎手 | 4 | web_read(urls=混坏), sys_state |
| P05 亢明玉 | 采购 L2 | 3 | web_read(非法参数), web_request(补条 POST) |
| P06 秋原野 | 摄影师 L1 | 5 | web_read(边界值), web_act(login) |
| P07 满宠儿 | 财经自媒体 L2 | 5 | feed_read(混坏/since 边界), web_research |
| P08 傅筛 | 安全研究 L3 猎手 | 9 | web_read(私网谱), cred_defaults(怪值), sys_state |
| P09 宿雨 | 学术编辑 L1 | 4 | web_read(假 DOI/check), web_recall |
| P10 戚山雨 | 客服 L1 | 3+4 | web_read(urls=), sys_guide + 补条(discover/crawl/request/doc_parse) |

**工具覆盖 13/13**。多工具链：P01/P03/P04/P05/P07/P08/P10。

## 回归点判定（common-issues 8 项）

| # | 回归 | 判定 |
|---|---|---|
| 1 | iframe 超时提示（R04-F1）| 本轮场景未触发（记录）|
| 2 | watch 出路句（R04-F2）| 未触发（记录）|
| 3 | resume 照抄（R01-003）| 未触发（记录）|
| 4 | 显式 timeout 不被 learned 压（R02-004）| **PASS**（P04 S2 批量 60s）|
| 5 | 401 杠杆（R02-003）| 未触发（本轮无 401 场景——记录）|
| 6 | archive 档账目（R02-002）| **PASS**（P03：404→archive 被问→429 逐字申报；P01：all_tiers_failed 逐层具名）|
| 7 | feed 顶点点名 + 20s 预算（R01-001/008）| **PASS**（P07）|
| 8 | sys_guide 13 tools（R01-009）| **PASS**（P02/P10）|

## 摩擦清单（按感知强度）

**皱眉级（候选）**
1. 【P01】死端口的错误族：原始 connect_ex=10061（refused）但 OS 层拖了 2s，dhole 报「timeout (TCP preflight)」——refused 与 timeout 在 OS 语义上可分（候选 R05-001，E 观察级：2s 延迟本身是本机防火墙环境所致，非稳定复现）。

**良好诚实行为（本轮大面积发生——这就是故障路径轮想要的）**
- 预算分账精确到毫秒：「stealthy tier skipped: only 988ms of the 5000ms call budget was left」。
- all_tiers_failed 逐层具名（HTTP tls handshake eof / Stealthy ERR_CONNECTION_CLOSED / js_shell_detected 各归各）。
- archive 档真触发且 429 逐字申报（「the second opinion got nothing」）。
- 私网谱三档话术：cloud metadata 单独具名（永不放行）/ 段名精确（10.0.0.1 in 10.0.0.0/8）/ 怪值（0/""/[]）全拒带形状句。
- 非法参数第一拒教全名单（force_fetcher 两个合法值 / 未知参数全 Supported 列表）。
- 边界值钳制申报（-1→500 with range）+ offset 越界哨兵（[No more content.]）。
- since 未来日期：older_than_since=6 + without_date=2（放行的无日期条目报了数）。
- 批量混坏三种错误族互不冒充、好行不受拖累；web_request 对登录墙页 auth_wall_detected 具名。

## 放弃点汇总

（无——故障路径轮的放弃点都是「该失败的失败了」）

## 主观评分汇总

| 画像 | 第一印象 | 速度 | 可信 | 报错 | 等待 | 总体 |
|---|---|---|---|---|---|---|
| P01 | 4 | 4 | 5 | 5 | 4 | 4.5 |
| P02 | 4 | 5 | 5 | 4 | 5 | 4.6 |
| P03 | 4 | 4 | 5 | 5 | 4 | 4.5 |
| P04 | 4 | 5 | 5 | 5 | 5 | 4.8 |
| P05 | 4 | 5 | 5 | 5 | 5 | 4.8 |
| P06 | 4 | 5 | 5 | 5 | 5 | 4.8 |
| P07 | 4 | 5 | 5 | 5 | 5 | 4.8 |
| P08 | 4 | 5 | 5 | 5 | 5 | 4.8 |
| P09 | 4 | 4 | 4 | 4 | 4 | 4.0 |
| P10 | 4 | 5 | 5 | 5 | 5 | 4.8 |
| **均值** | 4.0 | 4.7 | 4.9 | 4.8 | 4.7 | **4.66** |

比 round-04（4.56）再升：错误路径的话术质量是前四轮修复的复利。

## 候选缺陷编号索引（进 P3 对抗审查）

| # | 候选 | 一句话 | 首报 |
|---|---|---|---|
| R05-001 | preflight 失败错误族不区分 refused/timeout | E 观察级（OS 延迟为环境所致） | P01 |
| R05-002 | E7/E10 家产活体（404 行 3B 噪声再现） | backlog 在册 | P04/P10 |
