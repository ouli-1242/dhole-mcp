# round-13 复验记录（P5）· 2026-10-26

- 被验代码：branch `review/round-13` 工作树（P4 修复未提交，提交在 P6 落地；
  `git rev-parse HEAD` = **22e9f88** 基线 + 工作树修复）。
- **真重启**：taskkill 旧 9213（pid 16464）→ 全新进程 pid **17028**（dhole-9213.log 启动横幅）；
  靶站 8399 持续（pid 19576）。重启后 ping ok、tools/list=13。
- 修复前现场：p21 A3 / p22 C7（R13-001）、p22 #02/#03（R13-002）；
  修复后重放：`sessions/verify-r13.log`（V1-V4 全 PASS）。
- 全量 pytest：**7775 passed, 23 skipped**（7771 基线 + 4 新测；111.64s）。
- ruff：**16 条基线**（0 新增）。
- 注：修复后进程内 cred_defaults 清零，复验脚本先置 `allow_private`（r12 家产）。

## 逐项复验

| # | 项 | 证据 | 判定 |
|---|---|---|---|
| V1 | R13-001 显式 selector 收窄不再被判 extraction_collapsed | 新 URL `css_selector="h1"` → `content_ok=True`、chars=10、error 空（修复前同形一发 content_ok=false + extraction_collapsed） | **FIXED**（verify-r13.log V1） |
| V2 | R13-002 缓存回放补回 selector 0 匹配注记 | 同 URL+`#no-such` 两发：live summary 有句；第二发 `cached=true` 且 summary 同句在场 | **FIXED**（verify-r13.log V2） |
| V3 | 对照：收窄不报 / 无 selector 旧塌陷页 | `css_selector="article.main"` error 空；`/longaside` 无 selector 由 plain converter 回收（`content_ok=true`，summary 点名 plain converter） | **PASS（对照口径见下）** |
| V4 | 家产抽检 | pin 命中（r13 跨调用回读一致 + pin hit 句）；feed max_items=-3 具名拒 | **PASS**（verify-r13.log V4） |

### V3 的口径说明（诚实记录）

原计划用 `/longaside` 做「无 selector 仍报塌陷」的线上阴性对照。实测该页现在被
**恢复尺**（plain converter 回填）接住：`content_ok=true`、summary 明说 recovered，
线上无法再触发 collapse 判决。因此：

- 线上 V3 只证「点名收窄不报 + 恢复路径未坏」；
- 判决尺的阴性（无 selector 时真塌陷仍报）由单测
  `test_without_a_selector_a_real_loss_stays_collapsed` 承担（红测前置断言 + 修后保持绿）。

没有把「线上没触发」写成「通过」；也没有为了造阴性去改靶站。

## 关联文件

- 修复：`fixes/fix-R13-F1.md`（R13-001）、`fixes/fix-R13-F2.md`（R13-002）。
- 测试：`tests/test_a_narrowed_fragment_is_not_an_extraction_collapse.py`、
  `tests/test_a_cache_replay_still_says_the_selector_matched_nothing.py`。
- 原始回执：`sessions/verify-r13.log`；P3 现场 `sessions/persona-20..22/`。
