# 画像报告 · P03 蒲国栋（咨询顾问 · L2）· round-03

> 主题：批量读 + 长文 offset + 显式 timeout。6 发。p03- 前缀。

## 场景实录

- **S1 竞品批量（3 坏路径混入，后砍成 2 个）**：web_read urls=9 → 顶层摘要三笔账分清（answered / content_ok / 两个数为何不等）；7 行 200 回来，2 行 404 **各自具名**。E10 活体样本：404 行仍带 `total_extracted_chars: 3`（错误行的数字噪声）。
- **S2 长报告只读后半（offset）**：max_content_chars=1200 → 片 A（0–1166），offset=1166 → 片 B（1166–2327，起头「。 第 27 段」正好接片 A 尾「…认成正文」）——**连续性逐字核对成立**。E3 活体样本：summary 说的是整页口径（7.0KB / 2,577 kept），本片多大要自己对 next_offset 减。
- **S3 给慢点的时间（显式 timeout，R02-004 回归）**：timeout=60000 → summary 只有「timeout clamped 59->30 s per attempt (this tier caps one attempt at 30s)」——**没有 learned ceiling 5000ms 压制句**：显式预算没被学习值吃掉，剩下的 30s/attempt 是在册的档位帽且自报了。E5 活体样本：「59->30」报的是剩余秒不是请求的 60。
- **S4 交叉验证**：web_research 3 of 3 sources 进证据包。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因 | 严重度 |
|---|---|---|---|---|---|
| S1 | 「错误行怎么还有个 3」 | 0 | 404 行 total_extracted_chars=3（E10 二现） | 无感 |
| S2 | 「这 2,577 是整页还是这段」 | 自问 | E3 三现（summary 整页口径） | 皱眉 |
| S3 | 「59 是哪来的，我要的是 60」 | 自问 | E5 二现（钳制句报剩余不报请求值） | 无感 |

## 放弃点

无（急人设但本地靶站全亚秒）。

## 主观评分

- 第一印象 4 / 速度 5 / 可信 5 / 报错可读 4 / 等待 5 / 总体 4.6。

## 改进清单

1. 【皱眉→希望】offset 片的 summary 带本片口径（E3，已到三现线）。
2. 【希望】404 等错误行去掉 total_extracted_chars 数字噪声（E10）。
3. 【希望】钳制句报「请求 60 → 帽 30」两个真数（E5）。

## 附录

证据：sessions/persona-03/ + p03-run.log（offset 连续性核对在 verification 侧可重放）。
