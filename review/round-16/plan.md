# round-16 计划（P0 封存 · 主题：编码与多语言）

> 分支 review/round-16 · 基线 6f262fa（round-15 收官）· 端口 9216(dhole)/8399(靶站)/**8398(自建多语言页面站)**。
> **并发前提**：工作树仍有另一位 agent 的未提交改动；本轮只读不写其 src/tests/skills 文件，
> P6 只暂存 review/ 资产（若有修复，单 hunk 暂存）。

## 主题：编码与多语言

十个真实职业在“非 ASCII 世界”里用工具：中文/日文/韩文/阿拉伯文/俄文/法文/emoji/GBK/UTF-16。
看三件事：URL 里的非 ASCII 与 percent-encoding 是否端到端一致；正文抽取是否保真（不乱码、
不吞 emoji、不切断多字节）；本地文件的编码参数与误判是否说得清。

## 画像卡（P0 封存，不得事后改写）

| # | 画像 | 层级 | 场景 | 绑定工具 |
|---|---|---|---|---|
| P01 | 中文编辑 苏禾 | L2 | 中文搜索 → 读中文页；fixture 中文 URL 参数两读；CJK recall | web_search, web_read, web_recall |
| P02 | 阿拉伯语译者 莱拉 | L2 | ar.html RTL 正文保真；带阿拉伯语的 URL 参数；登录名用阿拉伯语 | web_read, web_request, sys_state |
| P03 | 日文研究员 高桥 | L3 | ja.html 与 emoji 页；日文搜索；多字节 offset 窗口 | web_read, web_search |
| P04 | 数据录入员 陈默 | L1 | GBK CSV（encoding=gbk / 缺省）；UTF-16 CSV | doc_parse, sys_guide |
| P05 | 社媒运营 小满 | L1 | emoji 页读两遍；emoji URL 参数；emoji recall；feed | web_read, web_recall, feed_read |
| P06 | 法语编辑 克莱尔 | L2 | latin1.html 重音保真；重音搜索；重音 CSV | web_read, web_search, doc_parse |
| P07 | 韩语用户 金敏 | L2 | ko.html；韩文搜索；韩文 URL 参数 | web_read, web_search |
| P08 | 俄语分析师 伊万 | L3 | ru.html；西里尔搜索；research 西里尔 query | web_read, web_search, web_research |
| P09 | 粤语用户 阿珍 | L1 | 繁体中文 recall（跨画像写入）；feed 相对窗口 | web_recall, feed_read, web_read |
| P10 | 多语言 QA 艾米 | L3 | mixed.html + crawl + discover；emoji 文件名 doc_parse；state | web_crawl, web_discover, doc_parse, sys_state |

## 每画像固定动作

1. 先置 `cred_defaults {"set": {"allow_private": true}}`。
2. 3-5 场景、5-7 发；跨语言读数打印码点/长度证据（len、repr、片段）。
3. 台账 `sessions/calls.jsonl`；回执逐发落盘 `sessions/persona-NN/`。

## 必带回归（common-issues round-15 必测点）

- R1 空框 property 读：ok=False + current value + 无 UnboundLocalError。
- R2 双 pin session 隔离 / 双 selector 缓存键隔离 / 登录 vs 匿名。
- R3 live→replay fetched_at 不晚于 live。
- R4 相对 since / offset 窗口 / check 两相。
- R5 r13 三件（css 收窄 / selector 注记 / feed -3）。
- R6 本轮新增：非 ASCII URL / 正文 / 文件编码三面。

## 完成判据

13/13 工具至少各一发；≥50 发；10 份画像报告 + 轮报告；P3 逐条攻击；
P4 只修 CONFIRM；P5 真重启 + 复验 + 全量 pytest + ruff 16；P6 资产与提交。
