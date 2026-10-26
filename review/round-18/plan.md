# round-18 计划（P0 封存 · 主题：误用与自愈）

> 分支 review/round-18 · 基线 ad352bf（round-17 收官）· 端口 9218(dhole)/8399(靶站)/8398(多语言站)。
> **并发前提**：另一位 agent 仍在把 src 注释翻成中文；本轮只读 src、不修源码（除非 P3 出现 CONFIRM，
> 届时只改与对方无关的文件并单 hunk 暂存；不得 revert/stage 对方在途改动）。

## 主题：误用与自愈

十个真实职业不是照着 schema 调工具，而是**凭记忆和直觉误用**：把对象写成字符串、
字段名记成 camelCase、数字带引号、负的 max、URL 缺协议、selector 带尾随空格、
secret 塞进普通 header、steps 形状错。看四件事：

1. 每次误用是否**具名拒绝**（field / expected / got），还是 500/栈/静默吞掉；
2. `next_action` 是否**能把人领回正确形状**（自愈链：改一处再发就成功）；
3. 误用后**状态是否干净**（allow_private、缓存、会话、限流不被污染）；
4. 同一会话连发误用后，**后续正常调用仍成功**（服务不崩、不挂死）。

## 画像卡（P0 封存，不得事后改写）

| # | 画像 | 层级 | 场景 | 绑定工具 |
|---|---|---|---|---|
| P01 | 新手产品经理 苏晓 | L1 | 对象写成 JSON 字符串、数字带引号、字段名 camelCase、参数名记错（q 代 query） | web_read, web_search, web_act |
| P02 | 数据工程师 满仓 | L3 | 各 max_* 负数/0/浮点/巨大值/字符串；clamp 是否具名且不误当 0 | web_read, web_crawl, web_discover, feed_read |
| P03 | 测试工程师 白盒 | L3 | 未知字段/空对象/null/重复键/`__proto__`/深嵌套；契约外键是否被静默吞 | web_read, web_act |
| P04 | 运维 老纪 | L2 | 无协议/`ftp://`/`file://`/`data:`/`javascript:`/超长 URL/空格 URL；method 拼错 | web_request, web_read |
| P05 | 安全审计 闻笛 | L3 | password/cookie/bearer 经 cred_defaults 与 header 进入；回执/state 是否回显明文 | cred_defaults, web_request, sys_state |
| P06 | 内容编辑 小满 | L1 | 查询与 selector 带 emoji/零宽/换行/尾随空格；URL 带中文与 emoji 参数 | web_search, web_act, web_read |
| P07 | 分析师 阿图 | L2 | steps 形状错（字符串/字典/元素字符串/缺 action/action 拼错/空数组） | web_act |
| P08 | 研究者 沈未 | L2 | 枚举错（depth/since/engine）、空 query、负 offset、负 cache_ttl、超大 top_k | web_search, web_research, web_recall |
| P09 | 图书管理员 罗薇 | L2 | doc_parse 不存在/目录/URL/二进制后缀；feed_read 非 feed + 错 max_items；recall 空词 | doc_parse, feed_read, web_recall |
| P10 | QA 艾米 | L3 | sys_guide 错 topic、sys_state 错 selector、cred_defaults 形状错；收尾正常调用验未污染 | sys_guide, sys_state, cred_defaults, web_read |

## 每画像固定动作

1. 先置 `cred_defaults {"set": {"allow_private": true}}`（P10 另测形状错）。
2. 4-6 场景、5-7 发；每发先误用、再按回执提示自愈一次（能自愈就算 PASS）。
3. 台账 `sessions/calls.jsonl`；回执逐发落盘。
4. 画像末尾发一发正常读，确认同会话未被误用拖坏。

## 必带回归（common-issues round-17 必测点）

- R1 R16-001：meta-only GBK/Latin-1 的 read 与 crawl 按 meta 解码。
- R2 save_to：覆盖拒 / 小帽拒 / 系统目录拒 / sha256 / 中文名（抽验 2 条）。
- R3 截图/PDF 头与 PDF→doc_parse 往返（抽验 1 条）。
- R4 r15 空框 property / r14 三件 / r13 三件（抽验）。
- R5 E 观察：空 save_to 话术、save_to summary 口径、路径语义两套。

## 完成判据

13/13 工具至少一发；≥50 发；10 份报告 + 轮报告；P3 逐条攻击；
P4 只修 CONFIRM；P5 真重启 + 复验 + 全量 pytest + ruff 16；P6 资产与提交。
