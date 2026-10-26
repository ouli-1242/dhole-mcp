# 决策日志（追加式）

## 2026-10-25T21:2x+08:00 宪章材料目录与产物目录分离
- 背景/冲突点：PROMPT.md 自述「配套材料在 review/ref/」「产物落在 review/」；实测宪章与其 ref/ 现居 loop/（review/ 下只有手动摸底轮产物）。
- 可选方案：①把 loop/ 改名回 review/；②宪章材料留 loop/，轮产物仍按宪章落 review/。
- 选择与理由：②。改名会动用户已提交的路径（loop/ 已入 git），而宪章对产物位置的要求（review/round-NN/…）不受影响；最小惊讶。
- 影响范围：全部轮次。

## 2026-10-25T21:2x+08:00 版本漂移适配：v17.2 + 13 颗工具（宪章写 11 颗）
- 背景/冲突点：宪章按 v17.0/11 颗写（覆盖 ≥10/11）；实测 dhole -v = 17.2，tools/list = 13 颗（多 web_research、web_recall）。
- 可选方案：①按宪章字面只考 11 颗；②按实测 13 颗适配，覆盖 ≥12/13。
- 选择与理由：②。宪章 §3 明说「以实测为准」；新工具恰恰是最该被真实使用模拟覆盖的。
- 影响范围：round-01 起全部轮次的覆盖判据。

## 2026-10-25T21:2x+08:00 脏工作树处置：round-01 从 WIP commit 切出（偏离宪章 P0.1 字面）
- 背景/冲突点：宪章 P0.1 要求「WIP 存到 wip/inflight-* → 切回基线 → 从基线切 round-01」。实测脏工作树 = 今日 manual 摸底轮的两个已修缺陷（web_request session_id、research timeout 钳制）+ 17.3 在途工作，manual 轮报告全量 pytest 7,692 passed。若切回 e917220（无这些修复），round-01 会把已知已修缺陷重新「发现→修复」一遍，制造假工作量。
- 可选方案：①宪章字面：round-01 基线 = e917220；②round-01 基线 = WIP commit 3cf035e，WIP 同时保留在 wip/inflight-20261025-2120（同一 commit）。
- 选择与理由：②。宪章最高原则「被测对象就是当前源码」+「诚实优先于好看」（不造假工作）；用户改动零丢失（wip 分支 + main 未动）。
- 影响范围：round-01 及后续（round N 恒从 round N-1 顶端切出）。

## 2026-10-25T21:2x+08:00 git 身份缺失 → 设仓库级身份
- 背景/冲突点：本机 git 无任何 user.name/email，commit 直接失败；不能动全局配置。
- 选择与理由：git config（仓库级）user.name=ouli, user.email=ouli@local；与既有提交作者 ouli-1242 区分开，轮产物 commit 一眼可辨。
- 影响范围：本仓库全部轮次 commit。

## 2026-10-25T21:2x+08:00 驱动方式：复用 manual 轮 sim.py 的进程内驱动，不逐发走 harness CLI
- 背景/冲突点：宪章 §5.2 指定 loop/ref/harness/dhole_cli.py；它是一发一进程（ping/tools/call 各一次 initialize）。本轮一个画像 4–6 场景 × 多发，一发一进程开销大且节流间隔难落在同一会话里。
- 选择与理由：模拟阶段用**进程内驱动脚本**（同一 JSON-RPC 契约：trust_env=False、协议 2025-03-26、Mcp-Session-Id 复用），每场景一份 transcript JSON + 追加 calls.jsonl，字段与 harness --log 对齐（ts/tag/tool/args/wall_s/isError/text）。harness CLI 仍用于 P0 自测与 P5 复验抽查（宪章指定动作不省）。
- 影响范围：round-01 P1/P5。

## 2026-10-26T00:5x+08:00 round-02 两项设计确认（裁决不改行为，只改话术/记录）
- 背景/冲突点：R02-003a（401/403 不触发 archive 第三档）与 R02-005（硬墙主机无早停）都被画像报为骂人级/边缘。逐条攻击后：前者 quality.py docstring 与 _ARCHIVE_FALLBACK_STATUSES=(404,410,451) 是同一决策在册（archive 不会有登录墙内容，去了白付一跳）；后者早停需 per-host 失败记忆参与升级决策——新机制，误拒可恢复站点的风险大于 90s 等待的代价。
- 选择与理由：两处都**不改行为**。R02-003a 修工具描述至与策略一致（点名 404/410/451 vs 401/403）；R02-005 wontfix 记观察（backlog E11），R02-004 的显式预算修复给了调用方真实自救杠杆。
- 影响范围：web_read 描述（wire 锁重锁）；后续轮次若硬墙场景再现，E11 重开裁决。

## 2026-10-26T09:5x+08:00 round-13 收官口径三项

- **「三试上限」计划项未观测**：P06 卡面写了「验证码三试上限」，靶站 /challenge/verify
  无次数限制，本轮也未出现任何上限句。按实测记「未观测」，不写通过、不编缺陷。
- **V3 阴性对照线上不可搭**：原计划用 /longaside 证「无 selector 真塌陷仍报」，实测该页
  已被恢复尺（plain converter）接住、content_ok=true。线上只证「收窄不报 + 恢复路径未坏」，
  判决尺阴性由单测 `test_without_a_selector_a_real_loss_stays_collapsed` 承担；不为造阴性改靶站。
- **循环在此收口**：用户指示「做完 r13 就停止，不要继续循环」。P7 与 round-14 不启动；
  round-14 的必测回归点仍写入 common-issues（若日后继续可直接用）。

## 2026-10-26T11:07+08:00 round-14：并发 agent 与脏工作树处置（偏离宪章 P0.1）
- 现场：工作树有另一位 agent 的未提交改动（src/tests/docs 多文件），用户明确要求不打扰、不提交、不回滚。
- 处置：不执行 P0.1 的 WIP commit / 切回基线；直接 `git switch -c review/round-14`（工作树零改动），
  本轮被测对象 = 当前工作树（editable，含对方在途改动）；P6 只暂存 review/round-14 与本轮修复 hunk，
  不整文件 `git add` 吞掉他人改动。
- 影响：P5 全量 pytest 若因对方在途改动出现红，先按旧债/in-flight 归因（不记本轮回归），如实写入 verification。

## 2026-10-26T11:43+08:00 round-15：并发工作树里的修复与单 hunk 暂存
- R15-001 的修复必须落在 `actions.py`（对方正在改的文件，336 行在途）。处置：只改 property
  读分支 3 行；P6 用 `git diff` 提取该 hunk + `git apply --cached` 只暂存本 hunk，
  其余在途改动保留在工作树，不提交、不回滚。
- 测试改动落在 `tests/test_actions_2.py`（该文件在对方改动清单之外），整文件暂存安全。
- `review/backlog.md` 仍含对方在途状态更新：round-15 的 E 记录写进 common-issues.md，
  待其落地后再合并回 backlog。

## 2026-10-26T12:06+08:00 round-16：确认的 B 类缺陷在并发编辑下推迟修复
- R16-001（meta-only 编码页被当 UTF-8）确认 B。修复必须落在 `fetcher._decode_html_bytes`
  与 `trafilatura_extractor` 的二进制判定，再经 crawl 的解码路径生效；这三个文件在画像跑完
  时正由另一位 agent 编辑（注释翻译，mtime 11:38-11:50）。
- 处置：**不写其文件、不接线测试**（避免在对方工作树上留红测试）。4 枚测试草稿存
  `review/round-16/fixes/test-R16-F1-draft.py`，修法逐条写进 review.md；
  round-17 先查其编辑是否落地，落地即接线修复。
- 归因：`git show HEAD` 无 sniff 代码，对方 diff 只有注释措辞——缺陷是既有的，不是其引入。
- 这是循环首次「CONFIRM 但推迟修复」；记录在案，不把推迟读成 REJECT。
