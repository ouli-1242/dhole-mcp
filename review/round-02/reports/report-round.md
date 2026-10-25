# round-02 轮汇总报告（边缘与刁难 · 10 画像 · 64 发）

> 分支 review/round-02 · 基线 aeb7059 · 服务 127.0.0.1:9202（v17.2 editable + R01 全部修复）· 靶站 8399 共用 · 受控旁证进程 9203（P10 专用，env 关闭，只指本地）。
> 证据：sessions/calls.jsonl（64 发）+ sessions/persona-01..10/*.json + reports/report-persona-01..10.md。

## 覆盖矩阵（画像 × 工具）

| 画像 | 职业/L | 发数 | 触及工具 |
|---|---|---|---|
| P01 何工 | 运维 SRE L3 | 6 | sys_state, cred_defaults, web_crawl, web_search, **web_discover**, watch(CLI) |
| P02 杜蘅 | 法务 L2 | 5 | web_read(PDF/archive/check) |
| P03 小北 | 学生 L1 | 5 | web_search, web_read, sys_guide |
| P04 覃川 | 数据工程 L3 | 8 | web_read(nested/offset/批量), web_crawl(resume) |
| P05 苏姗 | 外贸 L2 | 5 | feed_read, web_search, web_read |
| P06 老范 | 建材老板 L1 | 4 | web_search, web_read |
| P07 邱原 | 医师 L2 | 4 | web_research, web_search, web_read |
| P08 周奕 | 自动化测试 L2 | 11 | web_act(全动作面), **web_request** |
| P09 韩青 | 财务 L2 | 5 | doc_parse, web_read |
| P10 岑砚 | 研究生 L2 | 11 | web_read, web_recall, sidecar robots 三态 |

**工具覆盖 13/13**（MCP 62 发：web_read 24 / web_act 9 / web_search 8 / web_recall 6 / web_crawl 4 / doc_parse 4 / feed_read 2 / sys_state 2 / cred_defaults 1 / sys_guide 1 / web_research 1 / web_discover 1 / web_request 1 + watch CLI 3）。多工具链：P01/P04/P05/P08/P10。

**回归点判定（9 项）**：#1 feed 20s 预算 PASS（1s 死刑未复发）；#2 挑战页可发现性 PASS（fill note 教学现场）；#3 drag 首拒全形 PASS；#4 resume 照抄 PASS（plan resumed 1-10 of 10）；#5 E1 复发（记录）；#6 per-call ignore_robots **首次真实验证 PASS**（受控三态）；#7 E2 复发且升级（stale 打架两轮）；#8 feed 顶层点名 PASS；#9 sys_guide 13 tools PASS（L1 路径行为级）。

## 摩擦清单（按用户感知强度排序）

**骂人级**
1. 【P02】archive 档把 **Wayback capture 索引页**当内容交回且 content_ok=true（482B 的目录页盖章「可引用」）——候选 R02-002。
2. 【P02】WSJ 401 **不触发 archive 第三档**（工具描述承诺 hard block → dated snapshot）；且 `diagnosis.recover_with=''` 与 next_action 教的 cred_defaults **机器孪生与散文打架**——候选 R02-003。
3. 【P01】watch CLI **读不到 allow_private**：add 收下私网 URL、run 永远 exit 2，报错不指路（cred_defaults 在另一进程里）——候选 R02-001。

**骂人边缘**
4. 【P04/P08】learned ceiling **压过调用方默认预算**（30s→5s 自述）且**每个回执都念一遍**（同主机 6 连发 6 遍）——候选 R02-004。
5. 【P07】NEJM 硬墙烧满 90s：http 被墙 → stealthy 30s/attempt 烧完预算，全程无「此站常驻墙，换 PMC/DOI」的早停提示——候选 R02-005（裁决要点：per-host 失败记忆属一般机制，不违「禁域名特判」）。
6. 【P10】recall 同 URL 两行 stale 打架（R01-004 跨轮第二现）——E2 升级为确认队列候选。

**皱眉级**
7. 【P05】E3 三现（summary 字节数说整页不说本片）；【P05】E8 三现（since 不认 3d）；【P04】E1 三现（results 行键）——三项均达「跨轮三现」升级线。
8. 【P10】缓存 provenance 括号绕口令（E7 活体样本）。
9. 【P03】微信签名链/视频页无类型徽标（E 类新条目）。
10. 【P09】404 行仍带 total_extracted_chars=3（错误行数字噪声，轻微）。

**良好诚实行为（本轮真实发生）**
- 挑战失败落点页自诊断：「填进来的不是这一页的字符」（P08）。
- fill note 猜错语法第一现场教 `{=name}`（R01-006 修复的真实收益，P08）。
- snapshot 编号元素表 + 未知键 18 个合法键全名单（P08）。
- all_tiers_failed 逐层账目，假域名连 archive availability 都真去问了（P06——与 401 的对照成为 R02-003 的关键证据）。
- merged cells 披露、gb18030 自识、not_a_pdf 具名（P09）。
- research 单源失败隔离 + extraction_collapsed why（P07）。

## 放弃点汇总

| 画像 | 放弃点 | 强度 | 工具之过 |
|---|---|---|---|
| P02 | 可引用文本没拿到（S1 索引页/S3 无出路） | 骂人 | 是（R02-002/003） |
| P07 | NEJM 90s 后放弃 | 骂人 | 半（R02-005） |
| P06 | 假域名后放弃 | 无感 | 否（账目诚实） |
| P01 | 私网 watch 搁置 | 骂人 | 是（R02-001） |

## 主观评分汇总

| 画像 | 第一印象 | 速度 | 可信 | 报错 | 等待 | 总体 |
|---|---|---|---|---|---|---|
| P01 | 4 | 5 | 4 | 4 | 5 | 4.4 |
| P02 | 3.5 | 3 | 2 | 4 | 4 | 3.3 |
| P03 | 5 | 5 | 5 | 4 | 4 | 4.6 |
| P04 | 4 | 5 | 4 | 5 | 5 | 4.4 |
| P05 | 4 | 5 | 5 | 5 | 4 | 4.6 |
| P06 | 4 | 4 | 5 | 4 | 3 | 4.0 |
| P07 | 4 | 3 | 3 | 5 | 2 | 3.6 |
| P08 | 4 | 5 | 5 | 5 | 5 | 4.8 |
| P09 | 5 | 5 | 5 | 5 | 5 | 4.9 |
| P10 | 4 | 4 | 3 | 4 | 4 | 3.9 |
| **均值** | 4.25 | 4.4 | 4.1 | 4.5 | 4.1 | **4.25** |

比 round-01（4.29）略降：本轮主动把刁难与边缘区喂给了画像（P02/P07/P10 拿到的是最难的场景），可接受；可信度分（4.1）被 R02-002/003 拖累。

## 能力边界遭遇

challenge 每导航换码是靶站设计（fixture 注释明示）；夹具无 /iframe、/invoice 页（缺口如实）；hnrss TLS 握手失败（本网络环境 C，非 1s 预算——错误族已变）。

## 与「工具视角」的差异

工具视角「诚实披露」的四处，用户视角仍受伤：①Wayback 索引页盖 content_ok=true（披露在 summary 深处，章盖在头上）；②learned ceiling「披露即正当」（P04：披露不等于有权压我预算）；③401 时 diagnosis 与 next_action 各说各话；④recall 两行打架——诚实的是每行自己，撒谎的是并排的它们。

## 改进清单（按感知强度）

1. archive 档不把索引页当内容盖章（R02-002）。
2. 401/403 走 archive 档 + diagnosis.recover_with 与 next_action 同源（R02-003）。
3. watch 私网 URL 的 add 时拒绝或报错指路（R02-001）。
4. learned ceiling 不压调用方预算 + 首发后才念（R02-004）。
5. 硬墙主机早停提示（R02-005，裁决后动）。
6. recall 同 URL 合并变体（E2 升级）。

## 候选缺陷编号索引（进 P3 对抗审查）

| # | 候选 | 一句话 | 首报 |
|---|---|---|---|
| R02-001 | watch CLI 与 allow_private 断链 | add 收下、run 永远拒、不指路 | P01 |
| R02-002 | archive 索引页 content_ok=true | Wayback 目录页盖章可引用 | P02 |
| R02-003 | 401 不走 archive 档 + recover_with 矛盾 | 承诺的第三档不触发；孪生打架 | P02 |
| R02-004 | learned ceiling 压默认预算+每发必念 | 30s→5s 自述成卖点 | P04/P08 |
| R02-005 | 硬墙主机无早停提示 | 90s 烧满才知道是墙 | P07 |
| R02-006 | E1/E3/E8 三现升级 | results 键 / 整页字节数 / since 档位词 | P04/P05 |
| R02-007 | E2 升级（recall stale 打架两轮） | 同 URL 合并变体 | P10 |
