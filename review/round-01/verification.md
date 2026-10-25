# round-01 复验记录（P5）· 2026-10-25

- 被验代码：`git rev-parse HEAD` = `3cf035e`（P4 修复尚未提交——复验先于提交执行，提交在 P6 一并落地，issue 表的 fix_commit 指向该提交）
- **真重启**：22:41 起（P3 补测时）；P5 前再杀再起一次（kill 9201/8399 → ensure_up.ps1，env 修复版）。服务：127.0.0.1:9201 + 8399。
- harness CLI：`ping` → serverInfo Dhole、negotiated 2025-03-26 ✓；`tools` → **13 颗** ✓
- 全量 pytest：**7728 passed, 23 skipped**（修后重跑两轮，第二 138s 轮确认全绿；ruff：基线 16 条旧告警，本轮 **0 新增**）
- 复验方式：逐步回放原始调用与参数；证据为下表原始回执摘录。

## 逐项复验

| # | 缺陷 | 回放步骤 | 原始证据（关键返回） | 判定 |
|---|---|---|---|---|
| V1 | R01-008 feed 1s 预算 | `feed_read urls=[hnrss.org/frontpage] max_items=3`（不带 timeout） | **无 1s/2s 梯子 note**；错误族从 `network_error: timeout` 变为 `tls_error`（20s 预算内根本走不到 1s 超时；hnrss 的 TLS 握手在本网络是另一族环境问题）。wall 11.5s | **FIXED** |
| V2 | R01-005-harness env | `cred_defaults(set allow_private)` → `web_read /private/secret` | `error: NONE`、`status 200`、`fetcher http`、14 字符（秘密内容回来了；修复前同参 robots_disallowed） | **FIXED** |
| V3 | R01-006 同链取值 | 一发 web_act：read #challenge as=code → fill {=code} → click confirm | steps ok=[True,True,True]、submission=POST、页面「挑战通过/抄写正确」、challenge.log `answer==expected ok:true`（GR35B） | **FIXED**（发现性修复；机制本就可用） |
| V4 | R01-003 resume 句缺 url | `web_crawl /collections max_pages=2 cache_ttl=0` | next_action 含 **`web_crawl(url=`** 与 `resume_token=` | **FIXED** |
| V5 | R01-002 search-0 措辞 | `web_crawl /collections search='详情' max_pages=5` | summary=「search='详情' **matched 0 of 5** crawled page(s) - the word only lives in page bodies…」；**无** max_pages reached | **FIXED** |
| V6 | R01-001 feed 顶点名 URL | `feed_read [/feed.xml, /no-such-feed]` | 「1 rows errored (**http://127.0.0.1:8399/no-such-feed** - each row names its own error and diagnosis)」 | **FIXED** |
| V7 | R01-009 计数漂移 | `sys_guide topic=start` | 「eleven」0 次；「13 tools」在场；R06-009 的一页预算（guide.read ≤4000）测试通过，磁盘正文 3994 字符 | **FIXED** |
| V8 | R01-010 drag 首拒 | `web_act actions=[{drag:{from,to}}]` | 第一拒即全形：「action 0 'drag' must be **{selector\|index, to}** - … unexpected key(s): ['from']」 | **FIXED** |

## 回归锁更新（改期望的论证，逐条）

| 测试 | 旧期望 | 为什么旧期望本身错误 |
|---|---|---|
| test_actions_5 ×3 | SNIPPET 写死 `{"confirm": true}` | 该常量即 R01-006 缺陷源头（不说层次）；改为从 actions 导入单一真源；`}}` 检查退役（合法 JSON 嵌套收尾），转义损坏由逐字匹配覆盖 |
| test_regressions_external_reports | 清空 summary 含 `crawled 0 page(s)` | 该句正是 R01-002 被评的误导措辞；report 本意「summary 描述实际交回的」由新句（matched 0 of N）更好满足 |
| test_guide start ≤4000 | （未破） | start 增补后曾到 4230，已压回 3994（磁盘）；R06-009 契约保持 |

## 缓存控制

V2/V3/V4/V5/V8 均 `cache_ttl=0` 或首次访问（不受 `~/.dhole/cache.db` mask）；V1 真源、V6 一好一坏均不读缓存。复验全程未清缓存。
