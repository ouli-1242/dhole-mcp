# 画像报告 · P02 沙青（技术写作 · L3 猎手）· round-03

> 主题：整站 crawl + 跨会话 resume + 私网豁免边界。9 发（含补跑 p02b）+ 3 会话。p02-/p12- 前缀。

## 场景实录

- **S1 整站地图（长任务）**：cred_defaults 开门 → web_crawl /collections max_pages=3 → 3 页、`stopped: max_pages reached`、next_action 说清「9 URL(s) discovered but never sent… that subtree holds 8 of the 9」并给出**可照抄的 resume 调用**（`Or pass web_crawl(url='…', resume_token='98e0a043bf59:0')…`）。resume token 落在顶层 `next_resume_token`（脚本首轮找错键记为观察，p02b 照抄 next_action 文案重跑）。
- **S2 跨会话 resume（照抄，R01-003 回归）**：新 session → 照抄 → **「plan resumed at 1-9 of 9: 9 ok, 0 with error」**——token 跨会话有效，不重抓已回的 3 页。
- **S3 内网 staging 页**：带豁免读 /private/secret → 200、14B 内容回来。
- **S4 豁免跟着谁（猎手题，首报候选 R03-001，P3 推翻）**：**新会话不带任何 cred_defaults**，读同一 URL → **content_ok=true，14B 私网内容照给**——来自缓存回放（summary 自白 cached copy）。首报以为是「没开门也拿到了」；P3 查证：**豁免是进程级的**（cred_defaults 存在进程级 identity store，同端口所有会话共享——security.py:122 在册 + 9203 启动警告明说 "Every client on this port shares one credential store"），本进程的豁免是 S1 自己开的，新会话继承的是同一份存量。门与缓存在同一份豁免之下，语义一致。真正的边界是**进程**（watch CLI 够不着，R02-001 的故事），不是会话。
- **S5 状态自查**：sys_state 正常。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因 | 严重度 |
|---|---|---|---|---|---|
| S2 首跑 | 「token 在哪」 | 1 发 | 脚本找错键（next_resume_token 在顶层，next_action 文案里也有）——工具无罪 | 无感（记录） |
| S4 缓存回放 | 「没开门也拿到了？」→「哦，是进程级的，我自己开的」 | 1 发 | 豁免进程级共享（在册设计；P3 推翻首报 R03-001） | 无感（学习成本一次） |

## 放弃点

无（L3 猎手全程记录）。

## 主观评分

- 第一印象 4 / 速度 5 / 可信 5 / 报错可读 5 / 等待 5 / 总体 4.8。

## 改进清单

1. 【保持】进程级豁免/缓存的启动警告与 security.py 头注（P3 侧确认它们把边界说清了）。
2. 【保持】resume 的可照抄句（R01-003 修复持续生效）。
3. 【保持】「9 queued, subtree holds 8」的队列交底。

## 附录

证据：sessions/persona-02/ + persona-12/（跨会话续爬）+ p02-run.log。
