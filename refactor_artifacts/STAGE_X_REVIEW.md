# STAGE_X_REVIEW.md — 阶段 2 每个原子改动的"审查者模式"记录

> 按任务要求，每个原子改动后以**审查者视角只读审查 `git show`**，逐条回答那 10 个问题。
> 本轮共有 4 个生产代码改动 commit（另有 3 个 artifact/文档 commit）。
> 审查依据是 `git show <hash>` 的实际 diff，不是记忆。

---

## 审查 R-1：`ebc51dc` — 删除 `updater.py::_reranker_model_present`

| # | 问题 | 回答 |
| --- | --- | --- |
| 1 | 是否改变工具名/Schema/描述？ | **否**。diff 只动 `updater.py`，该文件不参与 `_TOOL_DEFS`。已用实测 `tools/list` 复核（见 R-5）。 |
| 2 | 是否改变返回 content/isError/错误消息？ | **否**。被删函数从未被任何代码调用（全仓 grep 仅命中定义），因此不可能出现在任何返回路径上。 |
| 3 | 是否改变超时/重试/缓存/并发/UA/请求头？ | **否**。删除的是纯文件系统检查函数，不参与 HTTP。 |
| 4 | 是否改变日志/配置/CLI/环境变量/退出码？ | **否**。不在 `__all__` 中，`dhole -v` / `--doctor` 的输出不受影响（`doctor()` 未引用它）。 |
| 5 | 是否引入未测试路径？ | **否**，反而减少一个未测试函数。 |
| 6 | 是否影响爬取去重/深度/同域/排序/分页？ | **否**。`updater.py` 不参与 crawl。 |
| 7 | 是否影响搜索解析/排序/分页？ | **否**。`updater.py` 不参与 search。 |
| 8 | 是否可回滚？ | **是**。`git revert ebc51dc` 即可（18 行纯新增回补）。 |
| 9 | 注释是否准确？ | **是**（删掉了唯一一处不准确的地方：它宣称"Diagnose via `dhole -v`"，但没有调用者，`dhole -v` 从不诊断它）。 |
| 10 | 有无性能显著退化？ | **无**。删除代码不可能变慢。 |

**审查结论：通过。** 证据：`pytest` 1128 passed / `ruff` clean / `import dhole_mcp.updater` 正常。

---

## 审查 R-2：`6e248fe` — 修正 4 处描述"不存在的代码"的注释/docstring

| # | 问题 | 回答 |
| --- | --- | --- |
| 1 | 是否改变工具名/Schema/描述？ | **否**。**关键点**：`server.py:2751` 我改的是 `stealthy_fetch()` 的内部 docstring（第 2745 行附近），**不是** `_TOOL_DEFS` 里的 `smart_fetch` description（第 4000 行）。二者是不同对象。已用实测 `tools/list` 复核（见 R-5）**逐字节一致**。 |
| 2 | 是否改变返回 content/isError/错误消息？ | **否**。改的全是 `#` 注释与 docstring；docstring 不被任何返回路径读取（`stealthy_fetch` 的 docstring 只服务开发者）。 |
| 3 | 是否改变超时/重试/缓存/并发/UA/请求头？ | **否**。 |
| 4 | 是否改变日志/配置/CLI/环境变量/退出码？ | **否**。 |
| 5 | 是否引入未测试路径？ | **否**。零可执行语句改动（`git diff` 只有 `#` 与 docstring 行）。 |
| 6 | 是否影响爬取去重/深度/同域/排序/分页？ | **否**。 |
| 7 | 是否影响搜索解析/排序/分页？ | **否**。`search.py` 的两处改动是模块 docstring 与注释；`_CORE_QUERY_ENGINES` 逻辑一字未动。 |
| 8 | 是否可回滚？ | **是**。`git revert 6e248fe`。 |
| 9 | 注释是否准确？ | **这是本 commit 的唯一目的**。每条都先用命令证伪了原注释（`grep -rin qwant`、`grep -rn MIN_ENGINES`、`python -c "from dhole_mcp.reranker import MODELS"`、`_should_try_archive` 的 404/410/451 实测）。 |
| 10 | 有无性能显著退化？ | **无**。 |

**审查结论：通过。** 特别复核：**没有**顺手"修好" 410 的 archive 分支，只把注释改成与代码一致 —— 保留行为、消除误导。

---

## 审查 R-3：`9391ca0` — 删除两个未被引用的模块级常量 + 修正它们支撑的 docstring

| # | 问题 | 回答 |
| --- | --- | --- |
| 1 | 是否改变工具名/Schema/描述？ | **否**。`links.py` / `fetcher.py` 均不出现在 `_TOOL_DEFS`。 |
| 2 | 是否改变返回 content/isError/错误消息？ | **否**。`_MAIN_XPATH` / `_IMPERSONATE_POOL` 在全仓（src+tests+docs）**只出现于各自的定义行**，删除不可能影响任何返回。 |
| 3 | 是否改变超时/重试/缓存/并发/UA/请求头？ | **需重点说明**：`fetcher.py` 的 impersonation 行为**未变**。真正生效的是标量 `impersonate: str = "chrome"`（`fetcher.py:307,528`）；被删的 `_IMPERSONATE_POOL` 从未被读取。`search_engines.py:176` 另有一份**内联**列表，也未受影响。 |
| 4 | 是否改变日志/配置/CLI/环境变量/退出码？ | **否**。 |
| 5 | 是否引入未测试路径？ | **否**。 |
| 6 | 是否影响爬取去重/深度/同域/排序/分页？ | **否**。 |
| 7 | 是否影响搜索解析/排序/分页？ | **否**。 |
| 8 | 是否可回滚？ | **是**。`git revert 9391ca0`。 |
| 9 | 注释是否准确？ | **是**，且此 commit 修正了最严重的一处：`links.py` 的 docstring 声称 citations 是"main-content area 内的链接"，而代码只做 `in_nav` 判定（同域且非导航即 citation）—— `_MAIN_XPATH` 正是那个不存在的判定的遗骸。删常量 + 改 docstring 一起做，才让文档与代码对齐。 |
| 10 | 有无性能显著退化？ | **无**。 |

**审查结论：通过。** 证据：`pytest` 1128 passed / `ruff check .` All checks passed / `grep` 确认无残留引用（仅 `.pyc` 缓存命中）。

---

## 审查 R-4：跨改动的"十条铁律"总复核

| 检查 | 结论 | 证据 |
| --- | --- | --- |
| 工具名/描述/`inputSchema` 未变 | **是** | 见 R-5 的逐字节 diff |
| `content` / `isError` 语义未变 | **是** | `tools/call` 计数不变：34 次调用，`isError=True=16`、`False=18`（重构前后完全一致） |
| 超时/重试/缓存/并发/UA/请求头未变 | **是** | `git diff` 未触及任何 `timeout` / `retry` / `cache` / `semaphore` / `User-Agent` / `headers` 字面量 |
| 环境变量未变 | **是** | 未新增/删除/改名任何 `DHOLE_*`（`module_inventory.py` 前后对比） |
| CLI 未变 | **是** | 未改 `cli.py`；`updater.__all__` 未变 |
| 排序/去重/分页未变 | **是** | 未触及 `search*.py` 的排序/去重/分页逻辑（只有 2 处注释/docstring） |
| 爬取不变 | **是** | 未改 `crawl.py` / `sitemap.py` / `focus.py` |
| 测试守卫未削弱 | **是** | 未改 `tests/`（`git status` 证实 tests/ 从未出现在任何 commit 中） |
| 版本号/fork 身份未变 | **是** | `__version__` 仍 14.7；`LICENSE` / `NOTICE.ddgs.txt` 未被任何 commit 触及 |
| 可回滚 | **是** | 4 个生产 commit 都是纯文本改动，`git revert` 全部可逆 |

---

## 审查 R-5：契约回归（重构前 vs 重构后，同一台机器、同一命令）

```bash
# 重构前（commit 74524dc）冻结的基线，保存为 FROZEN_before_refactor_tools_list.json
# 重构后（commit 9391ca0）重跑同一个 harness
python refactor_artifacts/tools/mcp_snapshot.py
```

| 指标 | 重构前 | 重构后 | 结论 |
| --- | --- | --- | --- |
| `tools/list` 语义（键排序后 JSON） | — | — | **逐字节相同**（length 11875 == 11875） |
| `tools/call` 调用数 | 34 | 34 | 相同 |
| `isError=True` 条数 | 16 | 16 | 相同 |
| `isError=False` 条数 | 18 | 18 | 相同 |
| 超时/异常 marker | none | none | 相同 |
| 单次运行耗时 | 10.5 s | 10.4 s | 无可测退化 |

**这一条是"行为保持"最直接的证据**：工具 Schema 逐字节一致，错误面计数一致。

---

## 审查 R-6：`ruff check .` 全仓清洁度的自我纠错（值得记录）

在加入我自己的分析脚本后，`ruff check .` 从基线时的 "All checks passed!" 变成 "Found 2 errors"。
我一度以为是生产代码回归，**核查后确认两条都在我自己的脚本里**：
`module_inventory.py` 的未用变量 `getattr_imports`、`stale_reference_scan.py` 的未用 `import io`。
已删除并在 `d334fd9` 中修复。`ruff check src tests` 在整个过程中始终是 **All checks passed!**。

**教训（写给后续维护者）**：`refactor_artifacts/` 也在 `ruff check .` 的作用域内，
往里放脚本会改变这条命令的输出，不要把它误读成生产代码的问题。
