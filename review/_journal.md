# 全局追加日志（断点续跑）

## 2026-10-25T21:14 会话启动（goal round 1）
- 读宪章 loop/PROMPT.md + loop/ref/*（tools/templates/env-facts）。
- 实测：dhole v17.2 editable 全能力就绪；tools/list 13 颗；review/ 仅 manual-2026-10-25。
- git：WIP(50 文件) 存档 commit 3cf035e → wip/inflight-20261025-2120 与 review/round-01 同指；main 未动（e917220）。
- decisions.md 落 5 条；common-issues/backlog 种子化。
- 下一步：起服务（9201 + 靶站 8399）→ harness 自测 → 封存 10 画像 → P1。

## 2026-10-25T23:5x round-01 收官（P6 完成）
- P2：10 画像报告齐 + report-round.md（101 发、13/13 工具、回归 5/5 PASS）。
- P3：14 候选 → 8 CONFIRM（7 产品 + 1 harness）+ 1 wontfix（007 设计取舍）+ 5 backlog；4 项真机补测，3 处审查推翻自己（R01-005 产品→harness、R01-006 能力→可发现性、R01-010 snapshot 半）。
- P4：7 处产品修复 + harness，全先测后改（7 新测试文件 + 3 回归锁更新，论证在测试内）；全量 pytest 7728 绿、ruff 0 新增。
- P5：真重启复验 8 项全 FIXED（V1–V8 原始回执在 verification.md）。
- P6：提交 aeb7059（src+tests+review 全套）；index/backlog/common-issues 更新；fixture-evidence 归档。
- 下一步：round-02 P0（主题：边缘与刁难；端口 9202；从 aeb7059 切出）。

## 2026-10-26T01:1x round-02 收官（P6 完成）
- P1：10 画像（P01 运维/P02 法务/P03 学生/P04 数据工程/P05 外贸/P06 建材/P07 医师/P08 自动化测试/P09 财务/P10 研究生；L1×2/L2×7/L3×1）64 发，13/13 工具，多工具链 5 画像；P10b 受控旁证（9203 env 关闭）首验 per-call ignore_robots 三态。
- P2/P3：7 候选 → 5 CONFIRM（含 2 半 CONFIRM 半 REJECT）+ 1 wontfix（R02-005 设计）+ 1 backlog 升级（R02-006：E1/E3/E8 三现线）；E2 升格确认（R02-007）。
- P4：5 处修复全先测后改（5 新测试文件 18 测试；wire 描述改动走 dhole wire lock --write 重锁 + 4 处常量同步）；ruff 0 新增。
- P5：真重启复验 W1–W5 全 FIXED，原始回执落盘 sessions/verify/；全量 pytest **7746 绿**。**教训在册**：W3 曾两度误判——①缓存命中的回执本来就没有 attempt 句（没走 HTTP 档，正确行为，不是缺陷）；②learned 句首发缺席=冷 EMA（无样本不念，正确）。复验遇「预期 note 不在」先查路径是否真走了对应档，再判缺陷。
- P6：提交（round-02 全套）；index/backlog/common-issues/journal 更新；calls.jsonl 89 行对账说明在 verification.md。
- 下一步：round-03 P0（主题：长任务/跨会话/多工具链；端口 9203；从 round-02 顶端切出）。
