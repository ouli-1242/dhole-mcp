# round-15 环境日志（P0/P1）

> 服务 127.0.0.1:9215（dhole，pid 18560）+ 127.0.0.1:8399（靶站，pid 22360，沿用 r14）。
> env DHOLE_IGNORE_ROBOTS=1 · DHOLE_USAGE_LOG=1。
> 分支 review/round-15 · 基线 ea25700。**工作树含另一位 agent 的未提交改动，本轮被测对象
> 即当前工作树（editable）**；按用户指示不提交/不回滚其改动。

## 启动与自测

- 2026-10-26T11:30:00+08:00 dhole-9215 STARTED（pid 18560）；fixture-8399 已在（pid 22360）。
- 自测（scripts/p0_selftest.py）：server Dhole v17.2 · tools/list=**13**
  （web_read, web_act, web_request, web_crawl, web_discover, web_search, web_research,
  web_recall, cred_defaults, sys_state, doc_parse, feed_read, sys_guide）。
- 真实 search「dhole mcp」→ 2 rows（pypi + github）。
- 私网门：未置 allow_private 时 `/about` → `invalid_request: URL targets internal/private IP`
  （预期；每个画像脚本先置 `cred_defaults {"set": {"allow_private": true}}`）。

## 靶站日志偏移（P1 侧证基准）

| 日志 | P0 时点 | 说明 |
|---|---|---|
| evals/.runtime/orders.log | 7 行 | P04 真提交将 +1 |
| evals/.runtime/challenge.log | 2 行 | 本轮不主打 captcha |

## 备注

- 主题「并行与隔离」：会话/缓存/selector/提取类型交错；缓存冷热由各脚本自造 cb 参数隔离。
- 另：9214（r14 服务）仍在监听，互不干扰；本轮所有调用只打 9215。
