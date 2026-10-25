# 画像报告 · P04 弭兵（数据工程 · L3 猎手）· round-05

> 主题：批量混坏。4 发。

## 场景实录

- **S1 批量 8 发混 3 坏**：5 好行 content_ok；坏行三种错误族**互不冒充**：`network_error: timeout (TCP preflight)`（死端口）/ `http_error_404`（死链）/ `invalid_request: URL targets internal/private IP (10.0.0.1 in 10.0.0.0/8)`（私网）。好行不被拖累。
- **S2 显式 timeout=60000 批量**：正常（R02-004 家产）。
- **S3 sys_state** 正常。

## 摩擦日志
（无）

## 主观评分
- 4 / 5 / 5 / 5 / 5 / 总体 4.8。

## 附录
证据：sessions/persona-04/ + p04-run.log。
