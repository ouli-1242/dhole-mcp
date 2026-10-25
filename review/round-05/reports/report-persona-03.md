# 画像报告 · P03 蔺相宜（法务 · L2）· round-05

> 主题：已下线文件的 archive 档真触发。4 发。

## 场景实录

- **S1 外网下线页**：specification.cn 本轮 TLS 握手 eof（网络 C）→ all_tiers_failed，每层失败具名（HTTP: tls handshake eof / Stealthy: ERR_CONNECTION_CLOSED）。
- **S2 fixture 404 对照（关键证据）**：404 → **archive 档真被问到** → availability API 回 **HTTP 429**，回执原话：「archive.org fallback was asked and the availability API answered HTTP 429 - the second opinion got nothing」——第二意见问没问、为什么空手，一字不瞒（R02-002/R02-003 家产在线）。
- **S3 sys_state** 正常。

## 摩擦日志
（无——429 的诚实是好行为样本）

## 主观评分
- 4 / 4 / 5 / 5 / 4 / 总体 4.5。

## 附录
证据：sessions/persona-03/ + p03-run.log。
