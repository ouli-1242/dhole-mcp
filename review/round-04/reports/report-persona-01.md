# 画像报告 · P01 沈砚青（站点管理员/SEO · L3 猎手）· round-04

> 主题：robots 受控三态（旁证 9205 env 关）+ crawl path_include + sys_state。7 发 + 3 次旁证调用。p01- 前缀。

## 场景实录

- **S1 path_include 语义**：path_include=['/work'] → 4 页（种子 + 3 张 /work），scope 生效、`stopped: max_pages reached` 如实。
- **S2 受控三态（旁证 9205，env 全关，只指 8399）**：态1 无豁免 → `invalid_request: URL targets internal/private IP`；态2 豁免+不带 ignore → `robots_disallowed: http://127.0.0.1:8399/robots.txt disallows this URL for dhole-mcp`；态3 豁免+ignore_robots=true+cache_ttl=0 → 200、14B 内容回来，**`metadata.robots: "bypassed (ignore_robots=true)"`**（per-call 档，与进程 env 无关）。三态互不污染。（首测撞缓存：同 URL 41 分钟前被主进程 env-on 抓过，旁证直接回放缓存——E7 的跨进程活体：缓存行的 provenance 是铸造时刻的 env，回执说了是缓存但没说「当时 env 是什么」。）
- **S3 sys_state 对账**：主进程 census 正常；三态只发生在旁证。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 根因 | 严重度 |
|---|---|---|---|---|
| 态3 首测 | 「怎么直接 200 了」 | 旁证撞了主进程的磁盘缓存（跨进程共享）——回执自白缓存但没带「当时 env」 | E7 活体（已建档） | 皱眉（轻微） |

## 放弃点
无。

## 主观评分
- 第一印象 4 / 速度 5 / 可信 5 / 报错可读 5 / 等待 5 / 总体 4.8。

## 改进清单
1. 【保持】三态语义与 metadata.robots 的具名标注。
2. 【E7】缓存行跨进程回放时，provenance 注一句「存入时的 robots/env 语境」。

## 附录
证据：sessions/persona-01/ + p01-run.log（三态原始输出）。
