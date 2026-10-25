# 画像报告 · P09 阮青梧（心理咨询师 · L2 · 隐私敏感）· round-04

> 主题：allow_private 点名清单边界 + 撤销豁免。5 发。p09- 前缀。

## 场景实录

- **S1 点名放行**：`cred_defaults set allow_private: ["127.0.0.1"]` → 名单内读 /article ok。
- **S2 名单外仍拒**：127.0.0.2（同环回段）→ `invalid_request: URL targets internal/private IP (127.0.0.2 in 127.0.0.0/8)`——**名单语义精确到主机，不是「开了个环回口子」**。
- **S3 撤销即生效**：`cred_defaults unset allow_private` → 「forgot allow_private. 1 key(s) in force.」→ 立刻 fresh 读 → 私网门当场拒（不等重启、不残留）。

## 摩擦日志
（边界行为全部与话术一致——隐私敏感人设的验收全过）

## 放弃点
无。

## 主观评分
- 第一印象 4 / 速度 5 / 可信 5 / 报错可读 5 / 等待 5 / 总体 4.8。

## 改进清单
1. 【保持】点名清单/撤销的即时语义。
2. 【希望】unset 的回执顺带说「生效范围：本进程」（呼应 R03-001 澄清——进程级）。

## 附录
证据：sessions/persona-09/ + p09-run.log。
