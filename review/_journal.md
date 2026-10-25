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

## 2026-10-26T02:2x round-03 收官（P6 完成）
- P0/P1：9203 新起 + 10 卡封存（10 职业/L3×2/L1×3）→ 60 发、13/13 工具、跨会话（新 session 二访 + resume token 跨会话 + watch 真跨进程）+ 跨进程重启的 recall 存量被真实用到。
- P2/P3：6 候选 → **1 CONFIRM(B)**（R03-004 page_type 描述）+ **2 REJECT（审查推翻首报）**：R03-001 豁免是进程级（在册），R03-003 顶层信号在（**量具教训：driver 的 100 字截断打印会藏掉信号，判「顶层缺信号」前必须看全文**）；R03-002 降 backlog E12；R03-005/006 记 E 活体。
- P4：唯一修复 test-first（1 新测试文件）；ruff 0 新增；**事故在册**：一次 git stash/pop 撞上 fixture 进程锁住的 .runtime 日志（Windows unlink fail），恢复=checkout stash 的单文件 + drop——fixture 持日志时不得 stash。
- P5：真重启（9203 kill→起）复验 W1（page_type 取值零变化）+ 前两轮 11 项回归全 PASS；全量 pytest 7747 绿。
- P6：提交（round-03 全套）；index/backlog(E12)/common-issues/journal 更新。
- 下一步：round-04 P0（新主题自定；端口 9204；从 round-03 顶端切出；先读 common-issues 的 9 项必测回归点）。

## 2026-10-26T03:1x round-04 收官（P6 完成）
- P0/P1：主题「状态与写路径」；9204 + 旁证 9205（robots 三态）/9206（持久化）；10 新职业（L3×2/L1×3）；55 发 + 补条 3 发 = 13/13 工具。
- P2/P3：4 候选 → 2 CONFIRM(B) + 2 E 记录；**三条大行为澄清**：①持久化跨重启成立（重启后报 robots_disallowed 恰是私网门放行的证明）；②混 set fail-closed 整单拒零落盘（P03 首试误判成「持久化坏了」——脚本没读返回值）；③R02-001 出路真通（带 flag watch baseline 200 exit 0）。
- P4：2 处修复 test-first（2 新测试文件 4 测试）：iframe 超时步级提示（失败现场用子 frame 数，一般机制）、watch 出路句补 watch 进程 env 前题。
- P5：真重启复验 W1/W2；全量 pytest **7751 绿**（首轮 -x 曾 1 failed 疑 flaky，两轮完整重跑全绿——诚实记录）；ruff 0 新增。
- P6：提交（round-04 全套）；index/backlog(E7 三现/E10 三现)/common-issues/journal 更新。
- 下一步：round-05 P0（端口 9205 段；从 round-04 顶端切出；common-issues 的 8 项必测回归点）。

## 2026-10-26T04:0x round-05 收官（P6 完成）
- P0/P1：主题「慢与坏（故障路径）」；9205；10 新职业（L3×3/L1×3）；53 发 13/13 工具。
- P2/P3：2 候选 → **0 B（首个零确认轮）**——故障路径在前四轮加固下全站住了；R05-001 preflight 错误族=环境所致不立案（connect_ex 10061 但 OS 拖 2s）；报告侧证据密度排除偷懒嫌疑（§8 只一轮零确认，不作胜利宣言）。
- P5：pytest 7751 绿（零改动零新测试，对账平）、ruff 0 新增、真重启 13 tools。
- P6：提交（round-05 全套）；index/common-issues/journal 更新；观察项入册。
- 下一步：round-06 P0（端口 9206 段；从 round-05 顶端切出）。

### 观测量具教训（跨轮复用）
- driver 的 summary 100 字截断会藏信号（round-03 R03-003）→ 判「顶层缺信号」前必须看全文。
- 旁证进程撞主进程磁盘缓存会让「受控三态」失真（round-04 P01 态3）→ 受控测试一律 cache_ttl=0。
- 脚本不读返回值会把 fail-closed 拒绝误判成功能损坏（round-04 P03）→ 诊断脚本必须打印全回执。
