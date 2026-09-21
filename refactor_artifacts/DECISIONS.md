# DECISIONS.md — 关键决策日志

> 本任务没有可用的 memory MCP（见 `TOOLING.md` §5），因此以本文件承担"决策持久化"职责。
> 每条决策包含：背景 → 选项 → 选定 → 理由 → 证据/影响。**只记录已执行的决策，不记录设想。**

---

## D-1 先做完整基线提交，再谈重构

- **背景**：仓库处于 `unborn HEAD` 状态：95 个文件已 `git add` 但**零提交**，另有 11 个文件在工作区继续被修改；当前分支名 `fix/silent-degradation-hardening`；同时存在 `origin/master`、`master`(`699197a`) 与 v13.15–v14.6 的 tags。
- **选项**：(a) 直接在现有暂存区上改；(b) `git stash`；(c) 新建 `wip-before-refactor` 分支并提交当前工作区。
- **选定**：**(c)**，并同时把工作区树复制到 `.refactor_backup/`。
- **理由**：任务禁止"大爆炸"并要求"可回滚"。没有提交就没有回滚点 —— `git stash` 在同一未提交状态上反复 stash 会迅速变得不可审计；而借 `git reset --hard` 回退未提交改动正是任务禁止的破坏性操作。提交一次即获得永久的、可 `git diff` 的基线。
- **证据**：`git rev-parse HEAD` 之前报 `fatal: ... does not have any commits yet`；提交后 `74524dc575d39d2269e7268113dff9cf0b391208`。
- **影响**：`master` 与 `origin/master` 未被触碰；未推送；未删除任何分支。

## D-2 备份目录用 `.git/info/exclude` 隔离，而不是改 `.gitignore`

- **背景**：`.refactor_backup/` 与 `refactor_artifacts/` 需要长期存在，但 `git add -A` 会把它们一起提交。
- **选项**：(a) 修改受版本控制的 `.gitignore`；(b) 写入本地 `.git/info/exclude`。
- **选定**：**(b) 只排除 `.refactor_backup/`**；`refactor_artifacts/` **有意保持可提交**（它是交付物，要进分支）。
- **理由**：改 `.gitignore` 是修改产品仓库文件，会给"纯重构"分支引入无关 diff；而 `refactor_artifacts/` 是任务要求的交付物，必须能被提交。
- **证据**：`.git/info/exclude` 追加 `.refactor_backup/`；提交 `f27a0b1` 只包含 `refactor_artifacts/**`，不含备份。

## D-3 明确冻结外部契约面（C1–C8）

- **背景**：任务要求"外部可观察行为必须保持不变"，但没有给出边界。
- **选定**：把契约面**显式列举**在 `CONTRACTS.md`：MCP 协议面（传输/端点/版本/instructions）→ `tools/list` 的 8 个工具逐字段 → `tools/call` 的 `content`/`isError` 语义 → 24 个环境变量 → 5 个状态文件 → CLI 子命令 → 3 个测试守卫。
- **理由**：没有显式边界，"保持行为"就退化为主观判断。列举后，每次改动都能机械地回答"是否触及 C 系列任一字段"。
- **影响**：`CONTRACTS.md` C8 同时给出**允许面**（仅 S-4/S-5/S-6/S-7），把改动空间压到最小。

## D-4 重构成败的判据是"不变量 I 系列"，不是"代码变好看了"

- **背景**：任务允许注释清理、结构清晰化，但也禁止为优雅改行为。
- **选定**：在 `FIRST_PRINCIPLES.md` 中把每项事实分成 **I-1…I-15（必须不变）** 与 **S-1…S-8（可安全替换）**，并在 §8 把阶段 2 的改动面收敛为 `S-4 死代码 + S-5 同模块重复字面量 + S-6 说谎的类型标注 + S-7 误导性注释`。
- **理由**：这四类都满足"diff 小、可回滚、不需要改测试、不碰 I 系列"。
- **明确排除**（记入 `FIRST_PRINCIPLES.md` §7 末）：换正文提取器、改排序/去重/并发/超时/重试参数、拆分 `server.py`（4,587 行 God Module）、打断 `server.py ⇄ crawl.py` 导入环、把惰性导入提到顶层、运行格式化。
- **影响**：这解释了为什么本轮**不做**架构级拆分 —— 拆 God Module 与打断导入环会改变导入时序与冷启动行为，属于 I 系列。

## D-5 禁止运行 `ruff format`（基线即不通过）

- **背景**：`ruff check .` 通过（exit 0），但 `ruff format --check .` **基线就不通过**：66 files would be reformatted, 12 already formatted（exit 1）。
- **选定**：本轮**不运行** `ruff format` / `--write`，不格式化任何文件。
- **理由**：任务禁止"大爆炸重写"。66 个文件的纯格式 diff 会让"是否改变行为"的审查失去可行性，也会掩盖真实改动的 diff。
- **证据**：`refactor_artifacts/baseline/ruff_format_check_baseline.txt`。

## D-6 测试与网络策略：全程离线，拒绝真实外网

- **背景**：任务要求测试优先本地 mock，且禁止不必要的外部请求。
- **选定**：
  - 只跑默认 pytest（`addopts = -m "not e2e and not live"`）；**不跑** `-m live`（真实搜索引擎）、**不跑** `-m e2e`（真实浏览器子进程，且浏览器依赖在本机可能缺失）。
  - 自建 MCP 快照器对所有 `tools/call` 只用**必然被本地 SSRF 守卫拒绝**的目标（`127.0.0.1` / `169.254.169.254` / `file://`）。
  - 不执行 `pip install`；环境依赖已就绪。
- **理由**：离线才能让"行为无变化"的结论可重复；被拒绝的响应本身也是错误语义的契约证据。
- **影响**：`BASELINE.md` B2/B5 的结论都可在断网机器上复现。

## D-7 不改 README / CHANGELOG；不一致只记录

- **背景**：README 被作者自认为"最接近契约"但仍有多处与代码不符；且 `tests/test_tool_descriptions.py`(35 个用例) 会用措辞与数字钉住工具描述与 wire 体积预算。
- **选定**：本轮**不修改** `README.md` / `CHANGELOG.md`。所有不一致只写入 `DOC_CODE_DRIFT.md`，附命令与输出，标"记录，不修"。
- **理由**：任务明令"禁止顺手改 README 去对齐代码，或改代码去对齐 README"；且改 README 的措辞可能直接破测试、或改变 agent 的路由行为（属于 I 系列）。
- **影响**：README 声称的 14.6 token 表（`instructions 333 / tools/list 2,931 / 合计 3,264`）会被**重新实测**并把实际值报告在 `BASELINE.md`/`REFACTOR_REPORT.md`，但**不回写 README**。

## D-8 用"提交即回滚点"的策略约束每一步

- **背景**：任务要求每个绿色步骤单独 commit，且审查不通过要回滚到上一个绿色 commit。
- **选定**：每个原子改动 = 一次提交；提交前必须跑（1）相关测试、（2）全量 `pytest -q`、（3）`ruff check .`。`STATE.json` 的 `last_green_commit` 随每次绿色提交更新。
- **理由**：把"是否可回滚"变成机械事实（`git revert <hash>` 或 `git reset --hard <上一个绿色 hash>`），而不是依赖记忆。
- **影响**：`STATE.json` 是断点续跑的唯一事实来源；与 git 冲突时以 git + 测试证据为准。

## D-9 会话内自建 MCP 客户端，替代缺失的 mcp-inspector

- **背景**：任务要求"优先用独立 MCP 客户端/inspector 调用"，但会话中**没有** mcp-client / mcp-inspector MCP（见 `TOOLING.md` §5）。
- **选定**：降级为自建 `refactor_artifacts/tools/mcp_snapshot.py`（纯标准库 stdio JSON-RPC），并以**独立子进程**方式拉起被测服务器。
- **理由**：验证必须独立于被测对象。自建客户端不 import 任何 `dhole_mcp` 代码，只通过 stdin/stdout 说话，因此不构成"用被测对象验证自己"。
- **影响**：`BASELINE.md` B5 与其原始 JSON 输出即为该降级的证据。

## D-10 版本号与 fork 身份不作任何改动

- **背景**：README 声称版本唯一来源是 `src/dhole_mcp/__init__.py::__version__`。
- **实测**：`__version__` = **14.7**；`pyproject.toml` 确为 `dynamic=["version"]` + `[tool.hatch.version] path="src/dhole_mcp/__init__.py"`。
- **选定**：不动版本号，不动 `LICENSE` / `NOTICE.ddgs.txt`，不动自更新默认值。
- **理由**：三者都在 I 系列的对外身份面里；改动它们没有任何"易维护性"收益，只有风险。

---

# 追加决策（原任务完成之后，用户授权的新工作）

## D-11 复核 D-01…D-07：先证实/证伪，再谈动不动手

- **背景**：`DOC_CODE_DRIFT.md` 附录里 7 条发现来自被我覆盖掉的子代理报告，只是**转述**，我从未逐条验证。任务铁律 6 要求"没有证据不许下结论"，所以它们在文档里一直标着"待复核"。
- **选定**：写 `tools/verify_d_findings.py`，把每条都用**可复现的实测**跑一遍再定论。D-01 直接驱动真实 `MasterFetchServer.cache_clear()`，D-02 对比 env 前后 `_family_universe()` 的返回值，D-06 真调 `save_proxies()` 再 stat 文件，D-07 检查 `paths.home()` 在 `_run_repair` 里的出现次数。
- **理由**：转述的发现不能直接当事实用——**实测立刻抓到两处转述错误**：D-07 引用了一个根本不存在的模块 `src/dhole_mcp/repair.py`；D-04 漏报了 `.htm` 与 `.xhtml` 两个具体缺口。若不做这一步，后续可能照着错的定位去改代码。
- **证据**：`analysis/verify_d_findings.txt`（脚本自带 socket 守卫，实测外发尝试 `[]`）。
- **结论**：7 条**全部证实**，两处出处/范围更正后写入 `DOC_CODE_DRIFT.md` 与 `KNOWN_BUGS.md`。

## D-12 KB-6 单开分支修，且**不放在重构分支上**

- **背景**：KB-6（明文凭据文件未收紧权限）是安全加固，**会改变文件模式**，因此不属于"行为保持重构"。用户授权单独处理。
- **选定**：从 `wip-before-refactor`（`74524dc`，即重构前的真实代码状态）切出 `fix/kb-6-state-file-permissions`，只在该分支上动 `src/` 与 `tests/`。
- **理由**：
  1. **不该从 `master` 切** —— `master`（`699197a`）比 `origin/master` 落后 22 个提交，且与工作树差 59 个文件，它不是"当前代码"。
  2. **不该放在重构分支上** —— 那条分支的全部价值就是"可证明不改任何可观察行为"。把 chmod 改动挂上去，等于亲手毁掉这个前提。
- **影响**：两个分支各自成立、互不依赖。`refactor_artifacts/` 只存在于重构分支；KB-6 分支只含 `src/` + `tests/`，不复制文档进历史。合并验证见 `REFACTOR_REPORT.md` 的追加章节。

## D-13 KB-6 的实现口径："只加收紧调用，不碰 mkdir/open"

- **背景**：最自然的写法是把 `os.makedirs(...)` 换成现成的 `paths.ensure_private_dir()`。但那会**改变错误语义** —— `ensure_private_dir` 吞掉所有异常，而 `os.makedirs` 会抛。`save_proxies` 没有外层 try，换了之后"建目录失败"会从 `OSError` 变成后面 `open()` 抛的 `FileNotFoundError`。
- **选定**：新增与 `harden_file` 对称的 `paths.harden_dir()`——**只 chmod，不 mkdir**。所有调用点保留原来的 `mkdir` 原样不动，只在其后追加收紧。
- **理由**：行为保持的边界要划在"可观察行为"上，错误类型与错误时机都是可观察的。收紧调用自身 never-raise，所以它们不会新增异常面。
- **影响**：新增 39 行、删除 2 行，零处修改 `mkdir`/`open` 的调用与顺序。范围限制也写进了实现：`DHOLE_USAGE_LOG` 指向 home 之外时**一个 chmod 都不发**（那是用户的文件）。

## D-14 KB-6 的验证：反向验证证明测试不是空转

- **背景**：新加的权限测试在本机（Windows）读到的 mode 全是 `0o777`/`0o666`，**连已有 `harden_file` 的对照组也一样** —— 这台机器根本无法验证 POSIX 权限位。那么"测试通过"会不会只是空转？
- **选定**：三道证据。① mock `os.chmod` 断言"调用发生了、参数是 0o700/0o600"（平台无关，沿用 `tests/test_paths.py` 既有范式）；② 把新测试拿到**修复前的源码**（临时 `git worktree` 指向 `HEAD^`）上跑，必须失败；③ POSIX 真权限位断言加 `skipif`，等 Linux/macOS 上执行。
- **实测**：反向验证结果为 **9 failed, 6 passed, 3 skipped** —— 失败的 9 条正是"新行为"，通过的 6 条正是"行为保持"断言（内容不变、错误照抛、追加不截断、外部路径不碰、`add/remove/clear` 都走 `save_proxies`）。分组与设计完全吻合。
- **证据**：`analysis/kb6_negative_check.txt`、`analysis/kb6_before.txt`、`analysis/kb6_after.txt`。
- **诚实边界**：**POSIX 上的真实 mode 没有被本机实测过** —— 本机没有 POSIX 文件系统（WSL 的 `docker-desktop` 发行版无 python3，Docker 守护进程未运行，仓库也没有 CI）。这条链的最后一环靠 Linux 上的 `test_real_mode_on_posix` 补，**本报告不声称已实测**。
