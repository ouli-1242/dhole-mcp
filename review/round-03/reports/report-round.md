# round-03 轮汇总报告（长任务/跨会话/多工具链 · 10 画像 · 60 发）

> 分支 review/round-03 · 基线 1f0d06b · 服务 127.0.0.1:9203（v17.2 editable + R01/R02 全部修复）· 靶站 8399 共用。
> 证据：sessions/calls.jsonl（60 发）+ sessions/persona-01..10/*.json（跨会话补跑并入 persona-12/15）+ reports/report-persona-01..10.md。

## 覆盖矩阵（画像 × 工具）

| 画像 | 职业/L | 发数 | 触及工具 |
|---|---|---|---|
| P01 温旖 | 内容创作 L2 | 9 | web_search, web_read, cred_defaults, web_recall(跨会话) |
| P02 沙青 | 技术写作 L3 猎手 | 6 | cred_defaults, web_crawl(resume 跨会话), web_read, sys_state |
| P03 蒲国栋 | 咨询 L2 | 5 | web_read(批量/offset/timeout=), web_research |
| P04 麦冬 | 教师 L1 | 5 | sys_guide, web_read, web_crawl(search), web_recall(跨会话) |
| P05 霍砚 | 创业者 L3 猎手 | 6 | web_discover, web_crawl, web_request(POST+confirm), watch(CLI)×2, sys_state |
| P06 骆迦叶 | 政务 L1 | 5 | web_read(offset/404), sys_guide |
| P07 湛卢 | 金融研究 L2 | 4 | feed_read(since), web_research |
| P08 谷雨 | 前端 L2 | 5 | web_act(全动作面) |
| P09 迟桉 | 翻译 L2 | 4 | doc_parse, web_read(401) |
| P10 荀羽 | 攻略作者 L1 | 5 | web_search, web_read(特殊页), web_recall(跨会话) |

**工具覆盖 13/13**（web_read 24 / web_recall 7 / web_act 5 / web_crawl 4 / feed_read 3 / doc_parse 3 / web_search 3 / web_request 2 / web_research 2 / sys_state 2 / sys_guide 2 / cred_defaults 2 / web_discover 1 + watch CLI 2）。多工具链：P01/P02/P05/P08（≥3 颗）。跨会话机制：P01/P04/P10 新 session 二访；P02 resume token 跨会话照抄；P05 watch 真跨进程。所有 recall 跨会话证据**跨过了一次 9202→9203 进程重启**（存储层在盘上）。

## 回归点判定（前两轮家产，10 项全测）

| # | 回归 | 判定 |
|---|---|---|
| 1 | R02-007 recall 变体合并（P01）| **PASS**（/collections 单行 variants:2, stale:false）|
| 2 | R01-003 resume 照抄含 url（P02）| **PASS**（跨会话 resumed 1-9 of 9）|
| 3 | R02-004 显式 timeout 不被 learned 压（P03）| **PASS**（60s 只剩档位帽 30s 句，无 learned 句）|
| 4 | R01-002 crawl search-0 措辞（P04）| **PASS**（matched 0 of 5 + 词在正文解释）|
| 5 | R01-009 sys_guide 13 tools（P04）| **PASS**|
| 6 | R02-001 watch 私网行指路（P05）| **PASS**（结构事实+持久化出路在线上）|
| 7 | R01-001 feed 顶点点名坏源（P07）| **PASS**|
| 8 | R01-008 feed 缺省 20s 预算（P07）| **PASS**（无 1s 梯子）|
| 9 | R01-006 挑战页同链取值 + confirm 层次（P08）| **PASS**（「挑战通过/抄写正确」+ confirm 教在 click 本身）|
| 10 | R01-010 drag 第一拒全形（P08）| **PASS**（unexpected key(s): ['from'] + 全形）|
| 11 | R02-003 401 杠杆同源（P09）| **PASS**（recover_with=cred_defaults）|

另：写闸（web_request POST→confirm=true）「拦—教—过」三跳闭合（P05）；not_a_pdf/merged cells/GBK 自识良好行为组保持（P09）。

## 摩擦清单（按用户感知强度排序）

**皱眉级（P3 后仅存的一颗产品小修）**
1. 【P10】page_type 的字段描述把「article=散文抽取成功」说成默认可懂——P10 现场把它读成「这页是文章」——候选 R03-004（CONFIRM，B 小修：描述点名抽取形状≠体裁）。
2. 【P07】since='3d' 四轮不收（错误句自带示例）——E8 四现（已升级项的活体样本，R03-005）。

**P3 推翻的首报（诚实更正，机制无过）**
- 【P02→REJECT】私网缓存回放：豁免是**进程级**（在册设计+启动警告），「没开门也拿到」不成立（R03-001）。
- 【P08→REJECT】确认闸拒因顶层**在**（summary 后缀+next_action 全句），首报被 driver 的 100 字截断打印骗了（R03-003）。
- 【P04→降 E】召回排序是语义契约下的公平行为；「我存的优先」是产品设计诉求（R03-002→backlog E12）。

**无感级（记录）**
3. 【P03/P06】E3 三现之后再现：offset 片 summary 仍整页口径；【P03】E5：钳制句报剩余 59 不报请求 60；【P03/P06】E10：404 行 total_extracted_chars=3 噪声（R03-006，活体样本）。
4. 【P06】超纲请求（打包附件）没有「这不在工具面里」的出口，像被无视。
5. 【P08】无散文页 content_ok=False 与动作成功并存（顶层信号要拼起来读）。

**良好诚实行为（本轮真实发生）**
- 私网门 next_action「门+钥匙」一句话教学（P01 一跳自愈）。
- resume 句可照抄且跨会话有效（P02）。
- discover 的「2 never sent」队列交底 + plan id（P05）。
- 写闸「Ask the user, then re-send with confirm=true. HEAD probes need no confirm.」（P05）。
- feed 坏源顶点点名 + since 错误内嵌示例（P07）。
- research 失败源隔离不拖垮证据包（P07）。
- 挑战页落点自白「同一条链里读到的就是填进去的那一枚」（P08）。
- 神经召回空手时 terms_absent 如实（P04/P10）。

## 放弃点汇总

| 画像 | 放弃点 | 强度 | 工具之过 |
|---|---|---|---|
| P04 | 存的页召回不回，「问同事要链接」 | 皱眉 | 半（语义契约公平；E12 已记） |
| P10 | 存的是索引页没正文，一查不中「算了」 | 无感 | 半（E9 存储时的提示缺失） |
| P06 | 打包附件超纲，转人工 | 皱眉轻微 | 半（缺一句「不归我管」） |

## 主观评分汇总

| 画像 | 第一印象 | 速度 | 可信 | 报错 | 等待 | 总体 |
|---|---|---|---|---|---|---|
| P01 | 4 | 5 | 5 | 5 | 5 | 4.8 |
| P02 | 4 | 5 | 5 | 5 | 5 | 4.8 |
| P03 | 4 | 5 | 5 | 4 | 5 | 4.6 |
| P04 | 4 | 4 | 4 | 4 | 4 | 4.1 |
| P05 | 5 | 5 | 5 | 5 | 5 | 5.0 |
| P06 | 4 | 4 | 4 | 4 | 4 | 4.0 |
| P07 | 4 | 5 | 5 | 4 | 4 | 4.4 |
| P08 | 4 | 4 | 4 | 5 | 4 | 4.3 |
| P09 | 5 | 5 | 5 | 5 | 5 | 4.9 |
| P10 | 4 | 4 | 4 | 4 | 4 | 4.1 |
| **均值** | 4.2 | 4.6 | 4.4 | 4.5 | 4.5 | **4.46** |

比 round-02（4.25）回升：前两轮修复的 11 个回归点全 PASS，报错教学形状（门+钥匙、resume 照抄、写闸、watch 出路）开始被画像夸。

## 能力边界遭遇

私网豁免是进程级不是会话级（重启清零——P01 真撞上）；缓存回放与门/显式预算的语义交叠（R03-001/R03-004 观察）；无散文页 content_ok 语义（P08 观察）。

## 与「工具视角」的差异

- 工具视角与用户视角本轮三处分歧经 P3 全部收敛：豁免是进程级（文档+警告在册）、拒因顶层在场（量具截断）、召回是语义契约（公平但无「我存的」概念）——分歧剩下的只有 page_type 描述的一句精度（R03-004）。

## 候选缺陷编号索引（进 P3 对抗审查）

| # | 候选 | 一句话 | 首报 |
|---|---|---|---|
| R03-001 | 私网缓存回放不过门 | **REJECT**：豁免进程级在册，前提不成立 | P02 |
| R03-002 | recall 无「我存的」显著性 | **降 E12**：语义契约公平，产品诉求 | P04 |
| R03-003 | 步级拒因不上升顶层 | **REJECT**：顶层信号在，首报被截断打印骗了 | P08 |
| R03-004 | page_type=article 给了视频频道页 | **CONFIRM（B）**：描述点名抽取形状≠体裁 | P10 |
| R03-005 | E8 四现（since 档位词） | **CONFIRM（E）**：已升级项活体样本 | P07 |
| R03-006 | E3/E5/E10 活体再现 | **CONFIRM（E）**：offset 口径/钳制措辞/404 数字噪声 | P03/P06 |
