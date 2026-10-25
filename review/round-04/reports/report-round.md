# round-04 轮汇总报告（状态与写路径 · 10 画像 · 55 发）

> 分支 review/round-04 · 基线 7cd2bc0 · 服务 127.0.0.1:9204（v17.2 editable + R01/R02/R03 全部修复）· 靶站 8399 共用 · 受控旁证 9205/9206（robots 三态 / 持久化，env 各按需，只指本地）。
> 证据：sessions/calls.jsonl（55 发）+ sessions/persona-*/ + pNN-run.log。

## 覆盖矩阵（画像 × 工具）

| 画像 | 职业/L | 发数 | 触及工具 |
|---|---|---|---|
| P01 沈砚青 | 站点管理 L3 猎手 | 6 | cred_defaults, web_crawl(path_include), sys_state, 旁证三态×3 |
| P02 顾晚舟 | 测试开发 L2 | 11 | web_act(upload/dialog/iframe/drag/press), sys_guide |
| P03 秦守拙 | 档案管理 L2 | 旁证 3 生命周期 + watch×2 | cred_defaults(持久化), watch(CLI) |
| P04 贺兰珠 | HR L1 | 7 | cred_defaults(host auth), web_act(login), web_read, web_recall, sys_guide |
| P05 佟既明 | 游戏策划 L2 | 4 | doc_parse, web_read, web_research |
| P06 卫朝雨 | 保险理赔 L2 | 5 | web_act(checkout 三跳), web_read(check/404) |
| P07 华青野 | 健身教练 L1 | 5 | web_search, web_read, web_recall, sys_guide |
| P08 商陆 | 审计 L3 猎手 | 7 | web_read(provenance/check), sys_state |
| P09 阮青梧 | 心理咨询 L2 | 5 | cred_defaults(清单/unset), web_read |
| P10 尉迟雪 | 旅游博主 L1 | 4+3 | web_read(QR), web_search, web_recall + 补条(discover/request/feed) |

**工具覆盖 13/13**（web_read 18 / web_act 13 / cred_defaults 8 / sys_guide 3 / web_recall 3 / sys_state 2 / web_search 2 / doc_parse 1 / web_crawl 1 / web_research 1 / web_discover 1 / web_request 1 / feed_read 1 + watch CLI 2）。多工具链：P01/P02/P03/P04/P05/P06/P08/P09。

## 回归点判定（common-issues 9 项）

| # | 回归 | 判定 |
|---|---|---|
| 1 | page_type 描述语义（R03-F1）| PASS（P07 读 B 站页不再误读）|
| 2 | recall 变体合并（R02-007）| PASS（同 URL 单行；新观察：查询串变体不合并，见 E 邻接）|
| 3 | 显式 timeout 不被 learned 压（R02-004）| PASS（round-03 已验，本轮场景未触发——记录）|
| 4 | resume 照抄跨会话（R01-003）| PASS（round-03 已验；本轮场景未触发——记录）|
| 5 | watch 私网行指路（R02-001）| PASS（错误行在场）+ **出路句兑现**（P03：带 flag 的 watch 进程 baseline 200 exit 0）|
| 6 | 401 recover_with（R02-003）| PASS（round-03 已验，本轮未触发——记录）|
| 7 | challenge 同链 + drag 全形（R01-006/010）| PASS（P02 S4）|
| 8 | feed 顶点点名 + 20s 预算（R01-001/008）| PASS（补条 feed 3 items；点名在 round-03 已验）|
| 9 | per-call ignore_robots 三态（P10b）| PASS（P01 旁证三态齐：门拒/robots_disallowed/metadata.robots bypassed）|

## 摩擦清单（按感知强度）

**骂人边缘**
1. 【P02】web_act selector 超时**不提 iframe**：三步各烧 10s，错误只说 matched no element——页面明明有 iframe、文档里有 frame: 寻址——候选 R04-001。

**皱眉级**
2. 【P03】R02-001 出路句不完整：`set DHOLE_PERSIST_CREDENTIALS=1` 没说 flag 必须在 **watch 进程**的环境里（不带 flag 的 watch run 依旧 exit 2）——候选 R04-002（出路句已验证的方向，话术补半个前题）。
3. 【P01】E7 活体：旁证撞主进程 41 分钟前的磁盘缓存，回执自白缓存但没说「存入时的 robots/env 语境」。

**无感级（记录）**
4. 【P07】查询串变体三行并排（合并键=精确 URL，契约如此；E2 邻接观察）。
5. 【P06】E10 三现（404 行 total_extracted_chars=3）。

**良好诚实行为（本轮真实发生）**
- 混 set（auth 无 host）整单 fail-closed，一个字不落盘（P03 首试撞上——拒得对）。
- 持久化跨重启：落盘豁免让重启后的私网门直接放行（拦它的是 robots，正话）。
- watch 带 flag → 私网 baseline 200 exit 0（R02-001 的路真通）。
- allow_private 点名清单：127.0.0.1 过、127.0.0.2 拒；unset 即时生效。
- provenance 三读逐发诚实（http/cache+cached copy/ttl=0 http）；缓存回放 /changing 内容不推进。
- upload 提交闸 + 「received 报价说明.pdf (1590 bytes)」落点；dialogs 收据行；iframe frame: 语法（文档在）。
- check 附 diff（changed/first_divergence_at/diff_window/hunks）如约。

## 放弃点汇总

| 画像 | 放弃点 | 强度 | 工具之过 |
|---|---|---|---|
| P02 | 无（QA 查文档后全通） | — | 半（R04-001 的 33s 税） |

## 主观评分汇总

| 画像 | 第一印象 | 速度 | 可信 | 报错 | 等待 | 总体 |
|---|---|---|---|---|---|---|
| P01 | 4 | 5 | 5 | 5 | 5 | 4.8 |
| P02 | 4 | 3 | 5 | 3 | 4 | 4.0 |
| P03 | 4 | 4 | 5 | 4 | 4 | 4.4 |
| P04 | 4 | 5 | 5 | 4 | 5 | 4.6 |
| P05 | 4 | 5 | 5 | 4 | 5 | 4.6 |
| P06 | 4 | 5 | 5 | 4 | 5 | 4.6 |
| P07 | 4 | 5 | 4 | 4 | 5 | 4.4 |
| P08 | 4 | 5 | 5 | 5 | 5 | 4.8 |
| P09 | 4 | 5 | 5 | 5 | 5 | 4.8 |
| P10 | 4 | 5 | 5 | 4 | 5 | 4.6 |
| **均值** | 4.0 | 4.7 | 4.9 | 4.3 | 4.8 | **4.56** |

比 round-03（4.46）再升：状态面/写路径的诚实形状（闸、provenance、豁免边界、持久化）经得起 L3 猎手对账。

## 候选缺陷编号索引（进 P3 对抗审查）

| # | 候选 | 一句话 | 首报 |
|---|---|---|---|
| R04-001 | web_act selector 超时不提 iframe/frame 寻址 | 三步 33s 税，错误不指路 | P02 |
| R04-002 | watch 出路句缺「flag 在 watch 进程 env」半个前题 | 带 flag 的路验证通，话术差半句 | P03 |
| R04-003 | E7 活体：跨进程缓存回放缺「存入时 env 语境」 | 旁证撞主进程缓存 | P01 |
| R04-004 | E2 邻接：查询串变体不合并（E10 活体再现） | 观察登记 | P07/P06 |
