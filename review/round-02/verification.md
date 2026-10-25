# round-02 复验记录（P5）· 2026-10-26

- 被验代码：branch `review/round-02` 工作树（P4 修复尚未提交——复验先于提交执行，提交在 P6 一并落地）
- **真重启**：kill 9202/8399 → 以修复后代码重启（9202 MCP + 8399 fixture，共用 round-02 起法）；受控旁证 9203 仅 P10b 用过，复验期未动。
- harness：PersonaSim 初始化 → serverInfo Dhole 17.2、protocol 2025-03-26 ✓；工具 13 颗（本轮 P1 已验）。
- 全量 pytest：**7746 passed, 23 skipped, 0 failed**（pytest-full.log，128.62s；round-01 结点 7728 + 本轮新测试 18 = 7746 对账平）。
- ruff：基线 16 条旧告警，本轮 **0 新增**（16 条与基线同源逐一对应）。
- 复验方式：W1-W5 逐步回放原始调用与参数，原始回执落盘 sessions/verify/*.json|txt；证据引文件，不引记忆。

## 逐项复验

| # | 缺陷 | 回放步骤 | 原始证据（关键返回，落盘文件） | 判定 |
|---|---|---|---|---|
| W1 | R02-002 archive 索引页盖章 | `web_read https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32016R0679 timeout=60000` | archive 档真被问到且抓回 capture 索引页 → note 全文「listed snapshots of this URL but the snapshot itself answered with the Wayback capture index, not archived content - the origin's failure above stands; try a dated web.archive.org/web/<timestamp>/<url> URL directly」，**content_ok: False**（修复前 True + 482B 目录）。本轮源站 202+0B（网络态与 P02 当时有别），守卫照常咬合 | **FIXED**（w1-archive.json） |
| W2 | R02-003 401 杠杆空 | `web_read https://wsj.com/articles/tech-companies-data-privacy-11634996753` | status 401；`recover_with: "store credentials for this host with cred_defaults (set={auth: {type:'basic'..."`——与 next_action 同源，白卷消失 | **FIXED**（w2-401.json） |
| W3 | R02-004 learned 压显式预算 + 逐发重复 | 三连发 `web_read /article?w3=<run-unique>`（同主机）；另 probe_w3c 观察冷启动 | 三连发全带新句「one HTTP attempt is bound by the learned ceiling for this host: 5000ms, not the default 30000ms - an explicit timeout= lifts it」（w3-learned.txt）；冷 EMA 首发无此句（无样本不念，正确）。**缓存命中的回执无此句也是正确**：没走 HTTP 档，无 attempt 可约束（复验中两次撞上，先误判为缺陷后确认为行为正确——已记 journal） | **FIXED**（w3-learned.txt；显式预算半边由单测 test_an_explicit_timeout_is_not_undercut_by_the_learned_ceiling 锁，wire 侧 fixture 主机过快无显式压现场可回放） |
| W4 | R02-007 recall 变体打架 | `web_crawl /collections max_pages=3 cache_ttl=0` ×2（max_pages 3/1）→ `web_recall query='推荐专题'` | /collections **单行**：`variants: 2`、`stale: False`（合并取最新 fetched_at）；其余 45 分钟前旧页（/work/*）stale: True 各归各行 | **FIXED**（w4-recall.json） |
| W5 | R02-001 watch 私网错误行不指路 | 真实 CLI：`dhole watch add http://127.0.0.1:8399/changing` → `dhole watch run` | 错误行带结构事实与出路：「watch runs in its own process, so the MCP-side cred_defaults waiver in memory mode never reaches it: set DHOLE_PERSIST_CREDENTIALS=1 and store allow_private once (cred_defaults), or watch a publicly reachable URL」。复验后 watchlist 已 remove 清场 | **FIXED**（w5-watch.txt） |

## 回归锁更新（改期望的论证，逐条）

| 测试 | 旧期望 | 为什么旧期望本身错误 |
|---|---|---|
| tool_defs_lock.json + CONNECT_CEILING/test_server_3/test_fetcher_1 | web_read 描述改动前的 wire 字节数 | 描述改动是 R02-003 的**修复本体**（承诺点名状态族）；锁经 `dhole wire lock --write --reason "R02-003 ..."` 重锁，非手工改数 |
| test_guide start ≤4000 | （未破） | 本轮未动 start.md；R06-009 契约保持 |

## 缓存控制

W1/W2 真源（外网，无缓存可撞）；W3 两轮撞缓存（run-unique 查询串解决，撞上的两发反而成为「缓存回执无 attempt 句=正确」的行为证据）；W4 crawl 全部 `cache_ttl=0`；W5 真实 CLI 进程。复验全程未清全局缓存。

## calls.jsonl 台账说明

sessions/calls.jsonl 现为 89 行 = **P1-P2 画像 64 发 + P5 复验/探针 25 发**（tag p93-p99：verify_replays.py / verify_w4.py / probe_w3c.py / probe_w3d.py，脚本均在 scripts/）。画像数与轮报告的 64 对账以此为准；复验发不计入画像统计。
