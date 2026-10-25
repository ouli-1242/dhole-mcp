# 画像报告 · P02 杜蘅（法务/合规 · L2）· round-02

> 审合同：PDF 指定页引用、双源交叉、硬墙找旧版、盯两份文本。5 发（p02-*）。

## 场景实录

- **S1 EUR-Lex PDF（候选 R02-002，骂人）**：GDPR 正式文本 PDF `pages=1-2` → 抓取失败一路升级 http→stealthy→**archive.org**，交回的「内容」是 **Wayback 的 capture 列表页**（"Wayback Machine / 978 captures / 11 Mar 2017 - 17 Oct 2026"，482B），**content_ok=true**、summary「200 OK」。回执确实诚实标了「archived copy, 2026-10-17」和「pages= 只作用于 PDF 响应，这份不是」——但把 capture 索引页当可引用内容盖章，法务直接引用就是垃圾。超时钳制「89->30」再现（R01-013 backlog 佐证）。
- **S2 双源交叉**：ICO + 欧盟委员会两页并发 → 2/2 content_ok（7,626 字），summary 把「answered without HTTP error」与「can quote」两个数的区别讲清了。「两个数不是一回事，它先替我分开了。」
- **S3 WSJ 硬墙（候选 R02-003）**：401 + 43B。**archive 第三档没有触发**——工具描述写着「on a hard block, content_ok can be true of a dated Wayback snapshot」，实际 401 直接终局。且 `diagnosis.recover_with=''` 而 next_action 教 cred_defaults——**机器可读孪生与散文出路打架**。
- **S4 check 批量**：首检 2 baseline、复检 2 unchanged，计数对账精确；「0 changed, 2 unchanged, 0 baseline」的句式第一眼要读两遍（无感）。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因 | 严重度 |
|---|---|---|---|---|---|
| 00:16 | S1 archive 存根 | 「这页是目录不是条款」 | 13s | archive 档把 capture 索引当内容、content_ok=true（候选 R02-002） | 骂人 |
| 00:17 | S3 401 | 「说好的硬墙走 archive 呢」 | 0.5s | 401 不触发第三档 + recover_with 与 next_action 矛盾（候选 R02-003） | 骂人 |
| 00:16 | S1 钳制 89 | 「又是这个 89」 | 0 | R01-013 旧账复发（跨轮） | 皱眉 |

## 放弃点

S1/S3 的「拿到可引用文本」目标**未达成**（S1 靠 archive 也只拿到索引页；S3 没有出路）——法务当天改用数据库，工具侧放弃。真实的失败路径，如实记录。

## 主观评分

- 第一印象 3.5 / 找答案速度 3 / 答案可信度 2（S1 的 content_ok=true 是可信度杀手）/ 报错可读性 4 / 等待感受 4 / 总体 3.3。

## 改进清单

1. 【我想骂人→希望】archive 档交回 capture 索引页时必须 content_ok=false（或 page_type=wayback_index），别盖章「可引用」。
2. 【我想骂人→希望】401/403 这类硬墙也走 archive 档（或工具描述改口说哪些挡法才算 hard block）。
3. 【希望】diagnosis.recover_with 与 next_action 同源：有出路就填，没出路就都别装。

## 附录

证据：sessions/persona-02/01（Wayback 索引页 content_ok=true）、03（401 + recover_with=''）、04/05（check 对账）。
