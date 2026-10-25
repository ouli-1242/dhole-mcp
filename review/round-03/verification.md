# round-03 复验记录（P5）· 2026-10-26

- 被验代码：branch `review/round-03` 工作树（P4 修复未提交，提交在 P6 落地）。
- **真重启**：kill 9203（pid 22532）→ 以修复后代码重启（env 顶层赋值，与 P0 同法）；靶站 8399 持续。
- harness：P0 自测 ping 2025-03-26 + 13 tools；本轮 P1 全程走 PersonaSim（60 发）。
- 全量 pytest：**7747 passed, 23 skipped, 0 failed**（pytest-full.log，139.23s；round-02 结点 7746 + 本轮新测试 1 = 7747 对账平）。
- ruff：基线 16 条旧告警，本轮 **0 新增**（修复后全量计数 16）。
- 复验方式：R03-004 是唯一 B 类修复；W1 回放 + 单测双证。

## 逐项复验

| # | 缺陷 | 回放步骤 | 原始证据（关键返回，落盘文件） | 判定 |
|---|---|---|---|---|
| W1 | R03-004 page_type 描述 | 真重启后 `web_crawl /collections max_pages=2 cache_ttl=0` | rows: `/collections → article`（269B 散文，语义正确）、`/work/1 → fallback`——**取值行为零变化**（修复只在描述层）；单测 test_a_page_type_word_describes_the_extraction_not_the_page 绿（描述含 prose + 体裁免责） | **FIXED**（verify-r3.log） |
| W2 | 回归点 1-11（R01/R02 家产） | P1 期间全部真调用回访 | 见 report-round.md 回归表：11/11 PASS（resume 跨会话、显式 timeout、watch 指路、401 杠杆、challenge 同链、drag 全形、feed 顶层点名、recall 变体合并、search-0 措辞、13 tools、私网门教学） | **全 PASS** |

## 意外恢复记录（诚实在册）

P4 期间一次 `git stash`/`stash pop` 撞上 fixture 进程锁住的 `.runtime` 日志（Windows unlink 失败，pop 半途 fatal）。恢复：`git checkout stash@{0} -- src/dhole_mcp/crawl.py`（stash 只卷走了 crawl.py 一个跟踪文件；review/ 在 gitignore 里从未进 stash）+ 验证 EXTRACTED 描述在场 + drop stash。**教训记 journal**：fixture/服务进程持日志时不得 stash。

## 缓存控制

W1 `cache_ttl=0` 真抓；回归点回访各按场景（P03 显式 timeout 用 fresh query、P07 feed 无缓存路径）。复验全程未清全局缓存。
