# 画像报告 · P01 何工（运维/SRE · L3 猎手）· round-02

> 夜间值班把 dhole 当探针：watch 靶站、重启对账、爬取边界、熔断语义。5 发 + 3 次 CLI（p02- 前缀 p01-*）。

## 场景实录

- **S0 开场**：sys_state 全景 → census 可读。
- **S1 watch（候选 R02-001）**：`cred_defaults(set allow_private)` → `dhole watch add http://127.0.0.1:8399/changing` 收下 → `watch run` 两次 **exit 2**：`invalid_request: URL targets internal/private IP`。**watch 是独立 CLI 进程：MCP 服务里存的 allow_private 它根本读不到，私网 URL 永远查不了**——而报错只说"内网拒绝"，不告诉 watch 用户这条路的尽头在哪（cred_defaults 帮不上 watch、报错也没点名）。exit 2=error 语义本身正确。「加的时候怎么不说？」
- **S2 对账**：sys_state show=['identities'] → **具名拒绝**并列出合法 token（cache/engine_state/sessions/cred_defaults）——报错形状好，词猜错成本一次。「identities 这个词是我猜的，它教了我真名。」
- **S3 爬取边界**：web_crawl max_pages=40 + deadline_ms=30000 → 靶站只有 12 页，578ms 全回、12 content_ok、无停因位谎报——队列空了就正常收。「该说的都说了，没编停因。」
- **S4 熔断语义**：点名冷却中的 so360 → 顶层 error「No results from any engine… retry in 60s (so360 60s cooling)」+ 指路 sys_state(engine_state)。「如实说冷却，还教我去哪看全队状态。」

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因 | 严重度 |
|---|---|---|---|---|---|
| 00:07 | S1 watch run exit 2 | 「加的时候怎么不说？」 | 2×run | watch CLI 进程读不到服务内存的 allow_private；报错不指路（候选 R02-001） | 骂人 |
| 00:08 | S2 词猜错 | 「哦，叫这个名」 | 一次 | 未知 token 具名拒绝（好行为） | 无感 |
| 00:09 | S4 冷却 | 「明说了 60 秒，行」 | 0.4s | 熔断如实 | 无感 |

## 放弃点

S1 私网 watch：**换公网页重试的思路有了，但本轮到 2 次 run 为止**（L3 会记下来，不会放弃调查）。

## 主观评分

- 第一印象 4 / 找答案速度 5（本地全亚秒）/ 答案可信度 4 / 报错可读性 4（watch 那句扣分）/ 等待感受 5 / 总体 4.4。

## 改进清单

1. 【我想骂人→希望】`watch add` 收私网 URL 时就拒绝，或 watch 能读 cred_defaults（或报错点名「watch 进程不共享 MCP 服务的豁免」）。
2. 【保持】未知 token / 熔断冷却的具名拒绝形状。
3. 【希望】watch run 的 error 行带 diagnosis 结构（与 MCP 行为对齐）。

## 附录

证据：sessions/persona-01/01-05 + 本文件 watch CLI 三段输出。
