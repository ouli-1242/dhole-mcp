# 画像报告 · P10 岑砚（研究生 · L2 跨会话长任务）· round-02

> 隔天回来继续文献调研：召回、被挡的名站、变体。9 发（p10-*，含受控旁证进程三发）。

## 场景实录

- **S1 读两页再召回**：ICO 页 → recall「data protection individual rights」→ **命中 ICO 页**（同一服务进程内跨会话）✓；中文 recall「专题 长文」→ /longaside + /collections。
- **S3 变体（R01-004 跨轮复发，实锤升级）**：recall 里 **`/collections` 出现两行、is_stale 一行 true 一行 false 互相打架**；`/` 也两行全 stale——与 R01 P04 同根同形，第二现轮。「到底信哪行？」
- **S2 受控旁证（回归#6 ✓）**：宪章 §8 路线——只指向本地靶站的 9203 进程、env 关掉 IGNORE_ROBOTS，三态隔离：
  1. 无豁免 → `invalid_request: 私网 IP`（私网门先于 robots）；
  2. allow_private + **不带** ignore_robots → `robots_disallowed`（robots 门独立工作）；
  3. allow_private + **ignore_robots=true** → 200，label「bypassed (ignore_robots=true) (this body was fetched under: bypassed (DHOLE_IGNORE_ROBOTS=1, process-wide))」——**per-call 档首次真实验证通过**；括号里那句是缓存 provenance（body 是 9202 在 P5 复验时抓的）——诚实但读着像绕口令（E7 的活体样本）。
- 附注：第一跑 sidecar 8 秒没起（连接拒绝）导致脚本中断，修复后整卡重跑——台账里有两轮记录，报告以第二轮为准。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因 | 严重度 |
|---|---|---|---|---|---|
| 01:06 | S3 stale 打架 | 「哪行是真的」 | 0 | R01-004 变体不去重（跨轮复发→升级） | 骂人边缘 |
| 01:07 | S2 provenance 绕口令 | 「两层 bypassed 谁是谁」 | 0 | E7（缓存 metadata 陈旧性） | 皱眉 |
| 01:03 | 首跑 sidecar 失败 | 「环境问题」 | 8s+ | 我的脚本（非工具） | 无感 |

## 放弃点

无（robots 三态走完，调研链没断）。

## 主观评分

- 第一印象 4 / 找答案速度 4 / 答案可信度 3（stale 打架直接扣）/ 报错可读性 4 / 等待感受 4 / 总体 3.9。

## 改进清单

1. 【我想骂人→希望】recall 同 URL 合并变体（保留最新 fetched_at，variants=N），stale 标注打架两轮了。
2. 【希望】provenance 括号改成独立字段（cached_from_process_env），别塞在一句里。
3. 【保持】per-call ignore_robots 真实可用；recall 跨会话命中。

## 附录

证据：sessions/persona-10/01-08 + sidecar 隔离三发（07/08 落盘）。
