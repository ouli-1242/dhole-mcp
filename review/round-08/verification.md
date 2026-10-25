# round-08 复验记录（P5）· 2026-10-26

- 被验代码：branch `review/round-08` 工作树（P4 修复未提交，提交在 P6 落地）。
- **真重启**：P4 修复落盘 → kill 旧 9207 失败在册（Git Bash `kill` 对该进程无效，改 `taskkill //PID //F`）→ 全新进程 pid **20768**（dhole-9208.log 启动横幅）→ V1-V3 重放命中该进程；靶站 8399 持续。
- 全量 pytest：**7765 通过，23 跳过**（7757 + 本轮新测试 8 = 7765 对账平）。
  - xdist 全量（8 workers）：**5 连跑同 2 个 DNS-recheck 测试 failed**（`test_dns_recheck_*`，round-06/07 已在册的 flake 本轮转顽固）——**每次隔离跑 2 passed**。
  - 顺序标准跑（`-p no:xdist -m "not e2e and not live"`）：**7765 passed, 0 failed（12:15）**——失败钉死在并行执行（8 worker 真实 DNS 并发 + fake-ip 隧道），非代码回归。诚实记录：第一轮顺序尝试曾被 addopts 坑（`-p no:xdist` 撞上 `-n 8`）与误选 e2e/live（12 failed 超纲），修正旗标后才是可比跑。
- ruff：**16 条基线**（0 新增；新测试文件的 F401 即时清掉）。
- 复验方式：三个修复（4 项裁决）一发真重启后的线上重放（sessions/verify-r8.log）。

## 逐项复验

| # | 项 | 证据 | 判定 |
|---|---|---|---|
| V1a | R08-001 相对 URL 前缀 → 具名 frame 错误 | `frame '/frame/inner' is not a usable address - it is neither an iframe name, an absolute URL prefix (copy it from a snapshot frame row), nor a CSS selector…` | **FIXED**（verify-r8.log V1a） |
| V1b | R08-005 错名 frame → 0 匹配具名 + 在场清单 | `frame '#nosuch' matched 0 <iframe> element(s) in the top document - the iframes present: ['app -> http://127.0.0.1:8399/frame/inner']…` | **FIXED**（verify-r8.log V1b） |
| V2 | R08-003 重铸寄存器出声 | read 行带 `'dup' was already minted by an earlier read in this chain ('UHXMR') - this read replaced it; later {=dup} uses THIS value` | **FIXED**（verify-r8.log V2） |
| V3 | R08-002 上传寿命句 | upload 行带 `the files ride THIS page instance - submit within the same call (a later call starts a fresh page)` | **FIXED**（verify-r8.log V3） |

## 本轮「组合与长链」总账

web_act 动作面全家桶（fill/click/read as=/confirm/upload/select/hover/drag/dialog/tab/frame/pin/session）两两组合 93 发：**4 处真缺口全修（frame 落空×2、重铸换值、上传寿命）**，两起假候选被审查用自己的回执推翻——组合态的正面账（弹窗五形态/页签四动作/双 session 隔离/跨 frame 寄存器三件套/pin+session 人在中途）全部成立。
