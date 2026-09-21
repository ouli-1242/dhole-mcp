# TOOLING.md — 可用工具 / Skill / MCP 清单与降级方案

> 本文件是阶段 0 第 4 步的产出：记录本次"行为保持重构"实际可用的能力、不可用的能力、
> 以及每一项的降级方案。所有"必须使用 X"的要求，若 X 不存在，均在此登记降级事实。

生成时间：2026-09-21（UTC）
执行者：Qoder Agent（自主执行，无人工确认）

---

## 1. 环境事实（阶段 0 第 5 步）

| 项目 | 实测值 | 命令 |
| --- | --- | --- |
| OS | Windows 10.0.26200 (win32, x64) | 会话环境信息 |
| Shell | Git Bash（`D:\Program Files\Git\bin\bash.exe`） | 会话环境信息 |
| Python | 3.14.5 | `python --version` |
| Python 路径 | `D:\Program Files\Python314\python` | `which python` |
| pip | 26.1.2 | `python -m pip --version` |
| pytest | 9.1.1 | `python -m pytest --version` |
| ruff | 0.16.8 | `python -m ruff --version` |
| 包管理 / 锁文件 | 无 lock 文件（无 requirements.txt / poetry.lock / uv.lock）；依赖声明在 `pyproject.toml` | `ls` 仓库根 |
| 项目根 | `D:\tools\dhole-mcp`（即 MCP 服务器本体，无需下钻子目录） | `ls` |
| 服务器入口 | `python -m dhole_mcp` → `src/dhole_mcp/__main__.py` → `dhole_mcp.server.main()` | `Read src/dhole_mcp/__main__.py` |
| 传输方式 | stdio（由 `server.py:main()` 实现） | 见 BASELINE.md |
| 版本单一来源 | `src/dhole_mcp/__init__.py` 的 `__version__`；`pyproject.toml` 用 hatch `dynamic = ["version"]` + `path = "src/dhole_mcp/__init__.py"` | `Read pyproject.toml` |

### 关键命令

| 用途 | 命令 |
| --- | --- |
| 安装（开发） | `python -m pip install -e ".[dev]"`（本次未执行，环境已就绪） |
| 测试（默认，离线） | `python -m pytest -q`（`addopts = -m "not e2e and not live"`，`pythonpath = ["src"]`） |
| 测试（e2e 靶场） | `python -m pytest -m e2e` |
| 测试（真实引擎） | `python -m pytest -m live --engine-fixtures check`（本任务禁止默认跑；需要真实外网） |
| Lint | `python -m ruff check .` |
| 格式检查 | `python -m ruff format --check .`（注意：仓库未启用 format，改动前需确认基线是否原本就不通过） |
| 启动冒烟 | `PYTHONPATH=src python -m dhole_mcp`（stdio，需 JSON-RPC 握手） |

### 测试隔离守卫（实测存在，见 `tests/conftest.py`）

| 守卫 | 实际名称 | 作用 |
| --- | --- | --- |
| 禁止真实 home 迁移 | `_no_real_home_migration`（autouse） | `paths._legacy_migrate_done = True`，阻止把真实 `~/.dhole_mcp_cache` 迁进真实 `~/.dhole` |
| 禁止真实状态写入 | `_no_real_home_state_writes`（autouse） | 把 `engine_stats.json` / `circuit_breaker.json` / `search_feedback.json` / `search_proxies.json` 重定向到 `tmp_path`；`real_state_paths` 标记的用例可退出 |
| 固定 DNS | `_offline_dns`（autouse） | 把 `socket.getaddrinfo` 固定为 `93.184.216.34`；`live` 标记的用例例外 |

---

## 2. 可用 Skills（本次会话实际注册）

| Skill | 本任务用途 | 是否实际调用 |
| --- | --- | --- |
| `python-code-quality` | ruff lint/format 与类型检查的规则与配置指导（阶段 2/3 质量门禁） | 计划调用 |
| `verification-before-completion` | 阶段 3 收口：要求"无命令与原始输出不得声称通过" | 已按其原则执行（见 REFACTOR_REPORT） |
| `code-review` | 阶段 2 每个原子改动后的只读审查（"审查者模式"） | 计划调用 |
| `tdd` | 阶段 2 每个原子改动前先确认相关测试为绿色 | 已按其原则执行 |
| `writing-plans` | 阶段 0/1 的测试基线与重构顺序规划 | 已按其原则执行 |
| `brainstorming` | 阶段 1 建模（本任务需求已完全指定，仅用于结构化） | 未调用（需求已明确） |
| `diagnosing-bugs` | 遇阻时的调试回路（本次记录 bug 不修，仅定位） | 备用 |
| `grilling` | 阶段 4 对抗性审查的替代手段（反复质询"行为是否真的没变"） | 计划调用 |
| `research` | 需要外部一手资料时（本任务禁止外网请求，基本不用） | 未调用 |
| `doc-index` / `domain-modeling` | 文档导航 / 术语统一 | 未调用（不在本任务范围） |
| `handoff` | 会话中断时的断点续跑（本任务用 `STATE.json` 承担同一职责） | 未调用（STATE.json 已覆盖） |
| `finishing-a-development-branch` | 分支收尾（本任务禁止合并/推送，仅保留分支） | 不适用 |

## 3. 不可用但本任务期望使用的 Skills（→ 降级方案）

| 期望 Skill | 状态 | 降级方案（实际执行） |
| --- | --- | --- |
| `refactoring` | **不存在** | 以"每次一个关注点 + 改前跑相关测试为绿 + 改后跑全量测试 + 只读 diff 审查"的人工流程替代 |
| `adversarial-review` / `red-team` / `security-audit` | **不存在** | 以 `grilling` 式自我质询 + 真实执行的红队测试脚本替代（见 `refactor_artifacts/ADVERSARIAL_REVIEW.md`，全部为实测而非臆测） |
| `systematic-debugging` / `root-cause-tracing` | **不存在** | 用"最小复现 + 二分定位 + 证据留存"替代 |
| `requesting-code-review` | **不存在** | 用 `code-review` + 在 `STAGE_X_REVIEW.md` 中按审查者视角逐条回答 10 问 |
| `memory` / `decision-log`（MCP） | 见下节，**不存在** | 用 `refactor_artifacts/DECISIONS.md` + `STATE.json` 承担持久化 |
| `sequential-thinking` / `first-principles` | **不存在** | 用 `refactor_artifacts/FIRST_PRINCIPLES.md` 中的显式推理链替代 |

---

## 4. 可用 MCP 服务器（本次会话实际连接）

| MCP 服务器 | 工具数 | 本任务用途 | 是否实际使用 |
| --- | --- | --- | --- |
| `playwright` | 25 | 浏览器渲染验证（原功能含 JS 渲染路径） | **未使用**：原功能的浏览器路径依赖 patchright/playwright 可选依赖，且本次禁网；降级为不测该路径并记录 |
| `browser-use` | 16 | 同上 | 未使用（同上） |
| `node-repl` | 5 | 可做 JSON 变换 / diff 辅助 | 未使用（用 `python` + `json` 模块替代） |
| `github` | 26 | 只读查看上游仓库历史以核对"为何这么写" | **未使用**：本任务禁止上传任何内容，且远端为个人 fork；仅用本地 git 标签 |
| `context7` | 2 | 查询 MCP SDK / 库文档 | **未使用**（避免不必要的外部调用；MCP SDK 用法已由现有代码与实测确认） |
| `builtin` | 7 | 会话管理 | 未使用 |
| `plugin:qoder-qmind:qoder-qmind` | 7 | 知识库检索 | 未使用 |
| `plugin:sites:sites` | 47 | 站点发布 | **不适用**（禁止发布/上传） |
| `extension-market` | 2 | 安装扩展 | 未使用 |

## 5. 不可用但本任务期望使用的 MCP（→ 降级方案）

| 期望 MCP | 状态 | 降级方案（实际执行） |
| --- | --- | --- |
| shell/terminal MCP（专用） | **不存在** | 使用内置 `Bash` 工具（Git Bash）执行测试、构建、lint、git、启动服务器 |
| git MCP | **不存在** | 使用 `Bash` 调用 `git`；本任务只做本地分支/提交/回滚，不推送 |
| mcp-client / mcp-inspector MCP | **不存在** | **自建** stdio JSON-RPC 快照器：`refactor_artifacts/tools/mcp_snapshot.py`，保存 `tools/list`、`tools/call` 原始响应 |
| fetch/http MCP | **不存在** | 用 Python 标准库在同进程内构造本地 fixture / 直接用被测代码的 mock 通道；禁止真实外网请求 |
| sequential-thinking MCP | **不存在** | 显式推理文档 `FIRST_PRINCIPLES.md` |
| memory MCP | **不存在** | `DECISIONS.md` + `STATE.json` 持久化 |

> **关键约束遵守情况**：本任务的被测对象是 Dhole MCP 服务器自身。会话中的 MCP 列表里
> **没有** Dhole，因此不存在"用被测对象验证自己"的问题；验证一律通过独立自建的
> stdio JSON-RPC 客户端（`mcp_snapshot.py`）和 pytest 完成。

---

## 6. 安全边界（强制遵守，全程有效）

**禁止访问的路径**
- `D:\tools\dhole-mcp` 之外的任何用户文件（系统工具、包管理器缓存除外）。

**禁止外传的数据**
- 源码、密钥、日志、抓取结果一律不得上传到任何外部服务（含 GitHub、pastebin、图床、WebFetch）。
- 因此：`github` MCP 的写操作、`sites` MCP 的发布、`ImageGen`、`WebFetch`/`WebSearch` 在本任务中一律不用。

**禁止的真实外部请求**
- 默认所有网络交互走本地 mock / 本地 fixture / 必然被拒的地址（`127.0.0.1`、`169.254.169.254`、`file://`）。
- 明确**不执行**：`pytest -m live`（真实搜索引擎）、真实引擎 SERP 抓取、模型权重下载（HuggingFace）。
- 唯一允许的"真实网络"是对**本项目自身**的 https 请求都不发起——包括 pip 安装；环境依赖已就绪，全程离线。

**禁止的 git 操作**
- 不合并到 `master`/默认分支；不推送远程；不删除分支；不使用 `--force`；不使用 `git reset --hard`
  丢弃未提交工作；`wip-before-refactor` 与 `refactor/dhole-mcp-behavior-preserving` 两个分支都必须保留。

**状态文件隔离**
- 本任务所有自建脚本在启动被测服务器时，把 `DHOLE_HOME` 指向 `refactor_artifacts/tmp_home/`，
  并在运行前清空，确保不写用户真实的 `~/.dhole/`。
- 备份：`.refactor_backup/`（已加入 `.git/info/exclude`，保证 `git add -A` 不会把备份提交进仓库）。
