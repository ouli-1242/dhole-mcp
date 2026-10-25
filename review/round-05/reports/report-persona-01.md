# 画像报告 · P01 邵岐（SRE · L3 猎手）· round-05

> 主题：故障三连与降级账目。6 发。p01-/p41- 前缀。

## 场景实录

- **S1 假域名**：`network error · 0B`（错误族 network_error 族）。
- **S2 死端口**：无豁免先撞私网门（对）；带豁免 → `network_error: timeout (TCP preflight)`（原始 connect_ex 10061 但 OS 层就拖了 2s——E 观察记 P3）。
- **S3 紧预算**：timeout=5000 → 「stealthy tier skipped: only 988ms of the 5000ms call budget was left, which is not enough to launch and navigate a browser」——**预算分账精确到毫秒**。
- **S4 降级链**：EUR-Lex → `all_tiers_failed: Attempted: HTTP -> Stealthy`，每层失败各自具名（202 + js_shell_detected ×2）。

## 摩擦日志
（账目全对得上墙钟；无骂点）

## 主观评分
- 4 / 4 / 5 / 5 / 4 / 总体 4.5。

## 附录
证据：sessions/persona-01/41/ + p01-run.log。
