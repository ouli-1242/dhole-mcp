# round-17 计划（P0 封存 · 主题：保存与下载往返）

> 分支 review/round-17 · 基线 b60a90a（round-16 收官）· 端口 9217(dhole)/8399(靶站)/8398(多语言站)。
> 首位工作是接线 round-16 的 R16-001 修复（测试已转绿、真机已验证）。
> **并发前提**：另一位 agent 仍在把 src 注释翻成中文（14:11 仍在改 reranker.py）；
> 本轮只改 `fetcher.py`（11:50 后未再动）并单 hunk 暂存。

## 主题：保存与下载往返

十个真实职业把网页/截图/PDF 落到本机，再把文件读回来。看四件事：
`save_to` 的路径纪律（不覆盖、父目录、系统目录、遍历）；`max_save_bytes` 的帽；
截图/PDF 的落盘形状；保存下来的字节能否被 `doc_parse`/后续工具无损读回。

## 画像卡（P0 封存，不得事后改写）

| # | 画像 | 层级 | 场景 | 绑定工具 |
|---|---|---|---|---|
| P01 | 研究员 周岚 | L2 | 中文名保存正文；同路径二次保存（覆盖门）；文件字节核对 | web_read, web_search |
| P02 | 记者 方卓 | L3 | 故意用 `../` 遍历与系统目录；登录后保存受保护页 | web_read, web_request, web_research |
| P03 | 设计师 米粒 | L1 | 截图保存 PNG；二次截图同路径（覆盖语义）；文件头核对 | web_act |
| P04 | 档案员 沈括 | L2 | `max_save_bytes` 小帽拒绝；正常帽落盘；recall 找回 | web_read, web_recall |
| P05 | 审计员 何清 | L2 | 上传中文名文件；上传路径纪律 | web_act |
| P06 | 数据员 杜衡 | L2 | `pdf` 动作落盘 → `doc_parse` 读回 → 页数/文本核对 | web_act, doc_parse |
| P07 | 教师 林一 | L1 | 父目录不存在的两种工具语义（read 拒绝 / 截图建目录） | web_read, web_act |
| P08 | 开发者 阿码 | L3 | emoji 文件名保存；命中缓存时 save_to 的行为 | web_read |
| P09 | 图书管理员 罗薇 | L2 | 二进制 PNG 保存；crawl/discover/feed 顺带覆盖 | web_read, web_crawl, web_discover, feed_read |
| P10 | QA 艾米 | L3 | `save_to` 边界（目录/空/后缀）+ state + guide + 清理 | web_read, doc_parse, sys_state, sys_guide |

## 每画像固定动作

1. 先置 `cred_defaults {"set": {"allow_private": true}}`。
2. 3-5 场景、5-7 发；所有落盘都放 `review/round-17/sessions/files/`（遍历用例落到 sessions/ 内并清理）。
3. 台账 `sessions/calls.jsonl`；回执逐发落盘。

## 必带回归（common-issues round-16 必测点）

- R1 **R16-001**：meta-only GBK/Latin-1 页 read 与 crawl 按 meta 解码；content_ok 真实。
- R2 R15-001：空框 property 读可读说明。
- R3 r14 三件（相对 since / offset 窗口 / check 两相）+ r13 三件（收窄/注记/feed -3）。
- R4 多语言正向：UTF-8 六文字 / 非 ASCII URL 与表单。

## 完成判据

13/13 工具至少一发；≥50 发；10 份报告 + 轮报告；P3 逐条攻击；
P4 只修 CONFIRM；P5 真重启 + 复验 + 全量 pytest + ruff 16；P6 资产与提交。
