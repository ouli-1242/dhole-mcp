# 画像报告 · P07 湛卢（金融研究 · L2）· round-03

> 主题：feed 增量盯盘 + 研究档交叉验证。4 发。p07- 前缀。

## 场景实录

- **S1 早盘增量（坏源混入、不带 timeout，R01-001/R01-008 回归）**：「2 URLs asked · 2 rows answered · 1 rows carried 5 items… · 1 rows errored (**http://127.0.0.1:8399/no-such-feed.xml** - each row names its own error…)」顶层数点名坏源（R01-001 PASS）；无 1s/2s 梯子句，缺省预算正常（R01-008 PASS）。
- **S2 三天以来（口语档位词，E8 四现）**：since='3d' → 行级错误「since='3d' is not a date - use ISO-8601 (2026-09-20 or 2026-09-20T08:00:00Z) or an RFC-822 feed timestamp (…)」——**错误里带示例**（自愈成本低），但「3d」这个档位词本身四轮不收（manual/R01/R02/R03）。顶层还教了「web_read the site page when the feed itself is gone」。
- **S3 改说 ISO**：since='2026-10-20' → 5 items 正常回。
- **S4 交叉验证**：web_research 90s 预算 → 「2 of 3 source(s) read into an evidence pack…; 1 failed (each row says why)」——失败源不拖垮整包。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因 | 严重度 |
|---|---|---|---|---|---|
| S2 | 「3d 又不行」 | 1 发 | E8 四现（报错带示例，但档位词仍不收） | 皱眉（四轮了） |
| S4 | 「2 of 3, 行」 | — | 失败隔离诚实 | 无感 |

## 放弃点

无（改 ISO 即通——被错误句教会的）。

## 主观评分

- 第一印象 4 / 速度 5 / 可信 5 / 报错可读 4（能自愈但四轮不改）/ 等待 4（research 51s，急人设记一下）/ 总体 4.4。

## 改进清单

1. 【皱眉→希望】E8：since 收「Nd/Nw」档位词（四现，已在 backlog 升级标注）。
2. 【保持】坏源顶点点名 + 失败隔离。
3. 【保持】错误内嵌示例的形状。

## 附录

证据：sessions/persona-07/ + p07-run.log。
