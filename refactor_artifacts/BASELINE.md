# BASELINE.md — 重构前基线证据

> 阶段 0 第 6 步产出。**本文件中的所有数字都是实测值**，命令可原样重跑；
> 原始输出保存在 `refactor_artifacts/baseline/`。
> 基线 commit：`74524dc575d39d2269e7268113dff9cf0b391208`（分支 `wip-before-refactor`），
> 重构分支 `refactor/dhole-mcp-behavior-preserving` 正是从它拉出的。

---

## B1. 仓库初始状态（重构前的真实处境）

| 事实 | 值 |
| --- | --- |
| 初始 HEAD | **不存在**（unborn，`fatal: your current branch 'fix/silent-degradation-hardening' does not have any commits yet`） |
| 初始暂存区 | 95 个文件全部 `git add` 但**从未提交**；另有 11 个文件在工作区被继续修改（`AM` 状态） |
| 现有 ref | `refs/heads/master` = `699197a`、`refs/remotes/origin/master` = `58c292c`、tags `v13.15`…`v14.6` |
| 当前分支名 | `fix/silent-degradation-hardening`（无提交） |
| 处置 | 按阶段 0 第 2 步：先做完整提交保全 → `wip-before-refactor` @ `74524dc`；再从它拉出重构分支 |
| 备份 | `.refactor_backup/`（`src/`、`tests/`、根文档副本，5.0 MB）；并写入 `.git/info/exclude` 以防被 `git add -A` 提交进仓库 |

**风险提示（已规避）**：仓库里存在 tags 与 `dist/dhole_mcp-14.6-*.whl`，但**没有任何可 diff 的
基线提交**。若直接开始改动，一旦出错将无法回到"原样"。这是本轮第一步就做提交的原因。

---

## B2. 测试基线（绿色）

| 项 | 命令 | 结果 | 退出码 | 原始输出 |
| --- | --- | --- | --- | --- |
| 全量单测 | `python -m pytest -q --tb=short -p no:cacheprovider` | **1128 passed, 2 skipped, 15 deselected in 63.33s** | **0** | `baseline/pytest_baseline.txt` |
| 收集数 | `python -m pytest -q --collect-only` | 1130/1145 collected（15 deselected） | 0 | `baseline/pytest_collect.txt` |
| 逐文件测试数 | （见下） | 35 个测试文件 | — | `baseline/pytest_per_file_counts.txt` |

默认 `addopts = -m "not e2e and not live"`（`pyproject.toml`），因此：
**2 skipped** 与 **15 deselected** 是设计行为，不是失败。
`-m e2e`（真实子进程靶场）与 `-m live`（真实搜索引擎）本轮不跑：前者需要真实 `dhole.exe`，
后者明确禁止外部请求。

### B2.1 逐文件测试分布（实测，前 20）

```
138 tests/test_security.py          119 tests/test_server.py        115 tests/test_search.py
 68 tests/test_bug_report_regressions.py  65 tests/test_envelope.py    50 tests/test_errors.py
 46 tests/test_proxy.py              44 tests/test_hardening_regressions.py
 39 tests/test_proxy_cli.py          38 tests/test_engine_parsers.py
 35 tests/test_tool_descriptions.py  30 tests/test_focus.py          29 tests/test_fetcher.py
 27 tests/test_engine_registry.py    22 tests/test_engine_yield.py
 21 tests/test_metadata.py           20 tests/test_links.py          19 tests/test_paths.py
 17 tests/test_structured.py         17 tests/test_stealth.py
```

**覆盖率评估（对验收的意义）**：验证矩阵里最难的几项**已被现有测试覆盖**，因此本轮
不需要新造测试就有客观 gate：

| 验证矩阵项 | 现有覆盖（实测存在） |
| --- | --- |
| SSRF / 内网 / `file://` / 元数据地址 | `test_security.py`(138)、`test_audit_fixes.py::test_feed_fetch_rejects_internal_url`、`::test_redirect_to_internal_rejected` |
| 重定向策略 | `test_audit_fixes.py::test_max_redirects_bounded`、`test_fetcher.py::TestFollowRedirectsCoercion` |
| DNS 二次校验（含开关与 hosts 钉） | `test_audit_fixes.py::test_dns_recheck_*`(5 个) |
| robots / sitemap 内网拒绝 | `test_audit_fixes.py::test_robots_sitemap_*` |
| 浏览器 SSRF（含 e2e 靶场计数） | `test_browser_ssrf.py`(15) |
| 凭据不泄漏（重试警告脱敏、代理凭据不进 stdout） | `test_hardening_regressions.py::test_retry_warning_redacts_proxy_credentials`、`test_proxy_cli.py`(39) |
| 缓存隔离与污染 | `test_hardening_regressions.py::TestCacheContextIsolation` |
| PDF 口令三态 | `test_hardening_regressions.py::TestPdfPasswordReachesTheExtractorAndTheCache`、`test_pdf_real.py`(6) |
| 空壳 schema 结构化拒绝 | `test_schema_param.py`(15)、`test_bug_report_regressions.py::TestStructuredInputErrors` |
| 参数类型/选项强制转换 | `test_bug_report_regressions.py::TestOptionsCoercion`、`test_server.py::TestStrictOptions` |
| `max_results` 钳制可见性 / `engine_state` / 调用预算 | `test_bug_report_regressions.py::TestMaxResultsClampIsVisible`、`::TestEngineState`、`::TestCallBudget` |
| 工具描述与 wire 体积预算 | `test_tool_descriptions.py`(35) |
| doctor 永不抛异常 / 进程扫描编码安全 | `test_doctor.py`(13) |
| 引擎解析器与注册表 | `test_engine_parsers.py`(38)、`test_engine_registry.py`(27)、`test_engine_yield.py`(22) |
| 编码 / 空 HTML / 巨型响应 | `test_fetcher.py::TestExtractEncoding`、`test_server.py::TestIsJsShell`、`::TestChunking` |

---

## B3. 静态检查基线

| 项 | 命令 | 结果 | 退出码 | 原始输出 |
| --- | --- | --- | --- | --- |
| Lint | `python -m ruff check .` | **All checks passed!** | **0** | `baseline/ruff_check_baseline.txt` |
| 格式检查 | `python -m ruff format --check .` | **66 files would be reformatted, 12 files already formatted** | **1** | `baseline/ruff_format_check_baseline.txt` |

**关于格式检查退出的重要决策**：`ruff check` 通过而 `ruff format --check` **基线即不通过**，
说明仓库从未被 ruff-format 过。因此本轮**禁止运行 `ruff format` 或 `ruff format --write`** ——
否则会产生 66 个文件的纯格式 diff，属于任务明令禁止的"大爆炸改动"，且会让
"是否改变行为"的审查无法进行。此项已在 `STATE.json` 与 `DECISIONS.md` 登记。

> `ruff` 配置（`pyproject.toml`）：`target-version = py311`、`line-length = 120`、
> `select = ["E4","E7","E9","F"]`、`ignore = ["E402"]`（有意保留延迟导入）。

---

## B4. 版本与身份

| 项 | 实测值 | 命令 |
| --- | --- | --- |
| 包版本 | **14.7** | `python -c "import sys;sys.path.insert(0,'src');import dhole_mcp;print(dhole_mcp.__version__)"` |
| 版本唯一来源 | `src/dhole_mcp/__init__.py::__version__`；`pyproject.toml` 用 `dynamic=["version"]` + `[tool.hatch.version] path=...` | `Read pyproject.toml:5,96-98` |
| fork 身份 | `LICENSE`、`NOTICE.ddgs.txt` 存在于仓库根；`NOTICE.ddgs.txt` 在 `.gitattributes` 标记 `linguist-vendored` | `ls`、`git show-ref --tags`（tags 到 v14.6） |

---

## B5. MCP 协议快照（阶段 0 第 7 步）

**方法**：自建 stdio JSON-RPC 客户端 `refactor_artifacts/tools/mcp_snapshot.py`
（会话中没有 mcp-inspector / mcp-client MCP 可用，故降级为自建，见 `TOOLING.md` §5）。
它以独立进程拉起被测服务器 `PYTHONPATH=src python -m dhole_mcp`，
把 `DHOLE_HOME` 指到 `refactor_artifacts/tmp_home/`（避免写真实 `~/.dhole`），
发送 `initialize` → `notifications/initialized` → `tools/list` → 8 个工具的 `tools/call`
（正常/缺参/错参三类），并把**原始响应**落盘。

**产出的证据文件**：

| 文件 | 内容 |
| --- | --- |
| `baseline/mcp_tools_list.json` | `tools/list` 原始结果（保留键顺序，`json.dumps(indent=2)`） |
| `baseline/mcp_tools_call.json` | 全部 `tools/call` 原始响应（含 JSON-RPC 信封） |
| `baseline/mcp_calls_log.txt` | 每次调用的入参 + 原始响应，逐条 |
| `baseline/mcp_server_stderr.log` | 服务器 stderr（日志面证据） |

**离线保证**：所有 `tools/call` 的目标地址都取**必然被本地 SSRF 守卫拒绝**的值
（`http://127.0.0.1/`、`http://169.254.169.254/`、`file:///...`），因此不产生任何真实外网流量；
被拒绝的响应本身就是错误语义的契约证据。

> **快照结果数值与 token 重测**：见本节末的"快照结果"小节（由 harness 运行后填入；
> README 声称的 14.6 token 表 `instructions 333 / tools/list 2,931 / 合计 3,264` 需重新实测，
> README 自身已标注"尚未按 14.7 重新测"）。

---

## B6. 基线可复现命令清单（照抄即可）

```bash
cd /d/tools/dhole-mcp
git log --oneline --decorate -3            # 应看到 74524dc (wip-before-refactor) 与 f27a0b1 (HEAD)
python -m pytest -q --tb=short             # 期望 1128 passed, 2 skipped, 15 deselected
python -m ruff check .                     # 期望 All checks passed!
python -m ruff format --check .            # 期望 66 files would be reformatted（基线即如此）
python -c "import sys;sys.path.insert(0,'src');import dhole_mcp;print(dhole_mcp.__version__)"   # 期望 14.7
```
