# round-14 环境日志（P0/P1）

> 服务 127.0.0.1:9214（dhole，pid 16212）+ 127.0.0.1:8399（靶站，pid 22360）·
> env DHOLE_IGNORE_ROBOTS=1 · DHOLE_USAGE_LOG=1。
> 分支 review/round-14 · 基线 f5f9340。**工作树含另一位 agent 的未提交改动，本轮被测对象
> 即当前工作树（editable）**；按用户指示不提交/不回滚其改动。

## 启动

- 2026-10-26T11:05:55+08:00 fixture-8399 STARTED（pid 22360）。
- 2026-10-26T11:05:59+08:00 dhole-9214 STARTED（pid 16212）。
- 自测：ping ok（Dhole v17.2）· tools/list=**13** · 真实 search「dhole mcp」→ 2 results
  （baidu/sogou/seznam，rerank=neural）。
- `dhole -v` v17.2；`import dhole_mcp` → `D:\Dev\Tools\dhole-mcp\src\dhole_mcp\__init__.py`（editable）。

## 靶站日志偏移（P1 侧证基准）

| 日志 | P0 时点 | 说明 |
|---|---|---|
| evals/.runtime/orders.log | 7 行 | P04 若真提交将 +1 |
| evals/.runtime/challenge.log | 2 行 | 本轮不主打 captcha |

## 备注

- 本轮 DHOLE_IGNORE_ROBOTS=1：robots 门不可观测（报告注明）。
- 真重启后进程内 cred_defaults 清零：每个脚本先置 `{"set": {"allow_private": True}}`。
- schema 里新增可见：feed since 支持相对窗口（'3h'/'3d'/'2w'）；web_act actions 新增
  navigate/pdf/evaluate；本轮按用户视角自然使用，不预读源码。
