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
