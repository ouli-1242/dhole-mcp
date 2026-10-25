# 画像报告 · P05 霍砚（创业者 · L3 猎手）· round-03

> 主题：discover→crawl→web_request→watch 多工具链 + 真跨进程 watch。7 发 + 2 次 CLI。p05-/p15- 前缀。

## 场景实录

- **S1 竞品站出地图**：web_discover → 「mapped 12 URL(s)…using 10 page fetch(es); no content extracted; stopped: max_pages reached · plan 8a57b3742859 holds the whole map: 12 row(s), 10 shipped here, 2 never sent」——地图、账目、断点三件齐。
- **S2 往深处爬**：web_crawl 6 页全 content_ok。
- **S3 接口形状本地验证（POST 被写闸拦）**：web_request POST /login → `invalid_request: method=POST is a write…`，next_action 一句话教全：「Ask the user, then re-send the same call with confirm=true. HEAD probes need no confirm.」
- **S3b 照抄重发（p15）**：confirm=true → 200（/login 302 后落点），liveness probe 与 loopback 头自白在场。写闸的「拦—教—过」三跳闭合。
- **S4 盯竞品页（真跨进程 watch，R02-001 回归）**：`dhole watch add /changing` 收下并预告「first run records baseline」→ `watch run` exit 2，错误行**带着进程边界事实与出路**：「watch runs in its own process, so the MCP-side cred_defaults waiver in memory mode never reaches it: set DHOLE_PERSIST_CREDENTIALS=1 and store allow_private once (cred_defaults), or watch a publicly reachable URL」（R02-001 修复在线上活着）。复验后 watchlist 已清。
- **S5 自查**：sys_state 正常。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因 | 严重度 |
|---|---|---|---|---|---|
| S3 写闸 | 「又要 confirm，行吧」 | 1 发 | 写闸拦得对、教得全 | 无感 |
| S4 watch 私网 | 「早说嘛——好，说了」 | 1 run | 错误行带结构事实（R02-001 生效） | 无感 |

## 放弃点

无（L3 猎手，链路全通）。

## 主观评分

- 第一印象 5 / 速度 5 / 可信 5 / 报错可读 5 / 等待 5 / 总体 5.0。

## 改进清单

1. 【保持】写闸「ask then confirm=true」一句教全。
2. 【保持】watch 错误行的进程边界话术。
3. 【希望】discover 的 2 never sent 两行直接附 URL 名单（要去翻 plan）。

## 附录

证据：sessions/persona-05/ + persona-15/ + p05-run.log（含 watch CLI 原始输出）。
