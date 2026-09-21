# REFACTOR_REPORT.md — Dhole MCP 服务器"行为保持重构"总报告

分支：`refactor/dhole-mcp-behavior-preserving`（**未合并、未推送、未删除任何分支**）
基线 commit：`74524dc575d39d2269e7268113dff9cf0b391208`（分支 `wip-before-refactor`）
产出目录：`refactor_artifacts/`　备份目录：`.refactor_backup/`

---

## 1. 执行摘要

**目标**：在不改变任何外部可观察行为的前提下，让 Dhole MCP 服务器更易维护、注释准确、可验证、可回滚。

**结果**：达成，但**范围刻意做得很小**。4 个生产代码 commit 共
**删除 27 行、改写 11 行注释/docstring，零可执行语句变更**。

**为什么这么小**：任务的第一铁律是"行为必须不变"，同时禁止大爆炸。而 `FIRST_PRINCIPLES.md`
的第一性原理推导得出的结论是 —— 在这个代码库里，**绝大多数"看起来该重构"的东西恰恰是会改行为的东西**：

| 想做的"清理" | 为什么不能做 |
| --- | --- |
| 拆分 `server.py`（4,587 行 God Module） | 会改变函数内延迟导入的时序 → 改变冷启动与 HTTP-only 降级行为 |
| 打断 `server.py ⇄ crawl.py` 导入环 | 同上，属导入时序 |
| 把函数内 `from dhole_mcp.x import y` 提到顶层 | 会改变重依赖（primit/playwright）的加载时机 |
| 换正文提取器（trafilatura → 别的） | 输出文本直接进 `content`，是可观察行为 |
| 统一 `timeout` 单位（fetch 是毫秒、feed/resolve 是秒） | 单位不一致本身就是契约（见 `CONTRACTS.md` C2 末注） |
| "顺手统一"错误封装（有的工具用 `isError`、有的不用） | 实测确认这是既有契约，且被测试钉住 |
| 跑 `ruff format` | 基线就未格式化过，会产生 66 文件纯格式 diff |

因此阶段 2 的动作面被收敛为四类**结构上不可能改变行为**的改动：
`S-4 死代码`、`S-6 说谎的类型标注`、`S-7 误导性注释`、`S-5 同模块重复字面量`。
最终只落了前三类。

**最有价值的产物不是那 27 行删除，而是"什么不能动"的证据链**：
`CONTRACTS.md`（逐字段的 8 个工具契约）、`mcp_snapshot.py`（可重跑的协议层快照器）、
`FIRST_PRINCIPLES.md`（I-1…I-15 不变量 vs S-1…S-8 可替换面的分界）。

---

## 2. 使用的 skills 与 MCP 清单

完整清单见 `TOOLING.md`。要点：

**实际调用的 skill**：`python-code-quality`（ruff 规则/行宽/select 集）、
`verification-before-completion`（无命令输出不得声称通过 —— 全文遵循）、
`code-review`（每个 commit 后以审查者视角逐条回答 10 问，见 `STAGE_X_REVIEW.md`）、
`tdd`（改前先确认相关测试为绿）、`writing-plans`（阶段规划）、
`grilling`（阶段 4 红队自查的替代手段）。

**不存在而必须降级的 skill**（已记录到 `TOOLING.md` §3）：
`refactoring`、`adversarial-review`、`red-team`、`security-audit`、
`systematic-debugging`、`root-cause-tracing`、`requesting-code-review`、
`memory`、`decision-log`、`sequential-thinking`、`first-principles`。

**不存在而必须降级的 MCP**（`TOOLING.md` §5）：
没有 shell MCP（用内置 `Bash`）、没有 git MCP（用 `git` CLI）、
没有 **mcp-client / mcp-inspector MCP**（→ **自建** `tools/mcp_snapshot.py`）、
没有 memory MCP（→ `DECISIONS.md` + `STATE.json`）、
没有 sequential-thinking MCP（→ 显式推理文档）。
`playwright` / `browser-use` / `context7` / `github` / `sites` 等会话中可用的 MCP，
按任务的禁网与禁外传约束**刻意未使用**。

**关键约束遵守**：会话的 MCP 列表里**没有 Dhole**，所以不存在"用被测对象验证自己"的问题；
验证一律经由独立子进程 + 自建 stdio JSON-RPC 客户端完成。

---

## 3. 基线证据

见 `BASELINE.md`（完整、含原始输出路径）。核心四项：

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 单测 | `python -m pytest -q` | **1128 passed, 2 skipped, 15 deselected in 63.33s**，exit 0 |
| Lint | `python -m ruff check .` | **All checks passed!** |
| 格式 | `python -m ruff format --check .` | 66 files would be reformatted（**基线即不通过** → 本轮禁跑 format） |
| 版本 | `import dhole_mcp; __version__` | **14.7** |

**仓库初始处境（必须记录）**：HEAD 是 **unborn**（零提交），95 个文件已 `git add` 但从未提交，
另有 11 个文件在工作区继续被改。**没有任何回滚点。** 阶段 0 的第一步就是把它提交成 `74524dc`。

**MCP 协议基线**：`tools/mcp_snapshot.py` 启动独立子进程，抓 `initialize` / `tools/list` /
8 个工具 × {正常, 缺参, 错参} = **34 次 `tools/call`** 的原始 JSON。
`DHOLE_HOME` 指向 `refactor_artifacts/tmp_home/`，socket 守卫拦住所有外发。

---

## 4. 行为契约清单

见 `CONTRACTS.md`（C1–C7 为契约面，C8 为允许改动面）。摘要：

- **协议面**：stdio 默认 + `--http` 时可用的 `127.0.0.1:8765/mcp`；协议版本 `2025-06-18` 原样回显。
- **8 个工具**：`smart_fetch`/`smart_crawl`/`screenshot`/`smart_search`/`cache_clear`/`parse`/`feed_fetch`/`resolve_url`，
  逐工具的 `properties` / `required` / `annotations` 已固定（`cache_clear` 是唯一的
  `readOnlyHint=False` + `openWorldHint=False`）。
- **默认值写在 description 文本里而非 schema 里** —— 因此改描述文本 = 改 agent 行为。
- **单位不一致是契约**：fetch/crawl/screenshot 的 `timeout` 是毫秒，feed_fetch(20)/resolve_url(15) 是秒。
- **错误面不对称是契约**：5 个工具用 `isError=false` 承载校验/SSRF 错误，`screenshot`/`feed_fetch` 用 `isError=true`。
- **环境变量**：`src/` 实测读取 24 个；`DHOLE_*` 为产品配置面。
- **状态文件**：`~/.dhole/` 下 5 个惰性写入文件，全部被测试守卫重定向。
- **测试守卫**：3 个 autouse fixture（`_no_real_home_migration` / `_offline_dns` / `_no_real_home_state_writes`）。
- **版本唯一来源**：`src/dhole_mcp/__init__.py::__version__`（`pyproject.toml` 用 hatch dynamic）。

---

## 5. 分阶段变更与 commit hash

| # | commit | 类型 | 内容 |
| --- | --- | --- | --- |
| 0 | `74524dc` | 基线 | 把未提交的 95 文件工作区提交为 `wip-before-refactor`（**唯一回滚点**） |
| 1 | `f27a0b1` | 文档 | `TOOLING.md`、`STATE.json`、`FIRST_PRINCIPLES.md`、`CONTRACTS.md`、AST 依赖图工具与原始输出 |
| 2 | `0d81f3e` | 文档 | `BASELINE.md`、`DECISIONS.md`、源码级 payload 测量工具与结果 |
| 3 | `ebc51dc` | **代码** | 删除 `updater.py::_reranker_model_present`（18 行，全仓零引用） |
| 4 | `d334fd9` | 工具 | 提交分析脚本与原始输出；修掉我自己脚本引入的 2 个 ruff 报错 |
| 5 | `6e248fe` | **代码** | 修正 4 处描述"不存在的代码"的注释/docstring（`search.py`×2、`search_metasearch.py`、`reranker.py`×2、`server.py`×2） |
| 6 | `9391ca0` | **代码** | 删除 `links.py::_MAIN_XPATH`、`fetcher.py::_IMPERSONATE_POOL`；修正 `links.py` 的 citations docstring |

**每个 commit 都是"一个关注点"**，且都附了可重跑的验证命令与结果。回滚见第 9 节。

---

## 6. 每阶段验证结果

| 阶段 | 门禁 | 结果 |
| --- | --- | --- |
| 0 基线 | `pytest` / `ruff` / `ruff format --check` / 版本 / MCP 快照 | 全绿（格式项按设计不通过，已决策禁跑） |
| 1 建模 | 模块依赖图（AST）、共享状态清单、env 清单、契约清单 | 产出 4 份文档，结论可复现 |
| 2 每个原子改动 | 改前相关测试绿 → 改动 → `ruff` + 全量 `pytest` + `import` 冒烟 | 3 个代码 commit 各自：`ruff` All checks passed、**1128 passed / 2 skipped / 15 deselected** |
| 2 审查 | 审查者模式逐条回答 10 问 | 见 `STAGE_X_REVIEW.md`（R-1…R-6，全部通过） |
| 3 契约回归 | 重构前后各跑一次 `mcp_snapshot.py`，键排序后 diff | **`tools/list` 逐字节相同**（11875 == 11875）；`tools/call` 计数相同（34 / 16 `isError=true` / 18 `false` / 0 超时） |
| 3 缺陷注入 | 见 `ADVERSARIAL_REVIEW.md` 第二/三节 | 已覆盖项全绿；**4 类盲区如实登记**（429、gzip/brotli/zstd、shift_jis、按异常类名的超时/连接错误） |
| 3 性能 | 快照器总耗时 | 10.5 s → 10.4 s，无可测退化 |
| 4 红队 | 7 个攻击面 + 3 个主动证伪尝试 | **未能证伪"行为未变"**；发现 4 条网络/权限卫生问题（只记录） |

**唯一一次"意外"**：加入我自己的分析脚本后 `ruff check .` 出现 2 个报错。
核查确认**两条都在我的脚本里**（未用变量、未用 import），`ruff check src tests` 全程全绿。
已在 `d334fd9` 修复，并把这个教训写进 `STAGE_X_REVIEW.md` R-6。

---

## 7. 对抗性审查发现

完整见 `ADVERSARIAL_REVIEW.md`。要点：

**未能证伪的三条独立证据链**：契约面逐字节相同 / 测试计数与基线完全一致 /
4 个生产 commit 零可执行语句变更。

**发现但按任务要求未修的问题**（全部登记在 `KNOWN_BUGS.md`）：

| 编号 | 问题 | 影响 |
| --- | --- | --- |
| KB-1 | `cache_clear(engine_state=true)` 的 `engine_health` 恒为 `{}`（先 reset 再 snapshot） | 外部可观察：agent 会误判引擎池已恢复 |
| KB-2 | `DHOLE_DEFAULT_ENGINES` 不改变 `engines_consensus` 分母 | 外部可观察：自建小池被读成"池降级" |
| KB-3 | 有代理配置时向 `example.com` 发真实 fire-and-forget 探测 | 真实外网流量 |
| KB-4 | 启动 preflight `1.1.1.1:443` | 真实外网流量 |
| KB-5 | 首次 `smart_search` 触发 HuggingFace 权重下载 | 网络 + 数百 MB 磁盘 |
| KB-6 | `search_proxies.json`（**明文代理凭据**）与 `usage.jsonl` 未 0600 | **安全面**；修它不改公开行为，最值得单开 PR |
| KB-7 | `cli.py`→`repair.py` 硬编码 `~/.dhole`，不跟随 `DHOLE_HOME`（`updater.py` 却跟随） | 产品内部写法分叉 |
| KB-8 | 本机 `site-packages` 装的是 **14.6 旧轮子** | 不带 `PYTHONPATH=src` 会静默验证旧代码 |
| KB-9 | `410` 被列在 archive 回退条件里但 `_should_try_archive` 对 410 返回 False | 只改了注释使其与代码一致，**未改代码** |

**主动排除的误报**（避免把"没写全"当错误）：`crawl.py` 的 transient docstring 不完整、
`paths.py` 两处 MB 数字指代不同模型、两份 `_VERTICAL_BACKENDS` 是有意重复且被测试钉住、
`pdf_extractor` 与 `server.py` 的重复代码块风险大于收益。

---

## 8. 行为差异

**逐字节级**：`tools/list`（键排序后）、`instructions`（1399 字符）、`tools/call` 错误面计数
—— **全部一致，无差异**。

**无法做到字节级、已证明语义一致并记录差异的项**：
1. **token 数无法比对**。README 记的是 token（333 / 2931），本轮只能测字符
   （1399 / 11499 compact），因为仓库里**没有记录作者用的 tokenizer**，离线也无可用 tokenizer。
   `字符/4` 粗估为 350 / 2877，与 README 同量级但不等 —— 这与"不同 tokenizer 给出不同数"一致，
   **不能据此断定负载漂移**。已记入 `DOC_CODE_DRIFT.md` D-2。
2. **JSON 对象键顺序**。`server.py:4127` 用 `Tool(**td)` 构造，MCP SDK 会重排键为
   `annotations, description, inputSchema, name`（数组元素顺序不变）。键顺序无语义，已记入 D-6。

**行为差异结论：无。**

---

## 9. 回滚方法（具体命令）

```bash
cd /d/tools/dhole-mcp

# 方式 A：整体回到重构前（最彻底）
git switch wip-before-refactor          # 这个分支只有 74524dc 一个提交 = 原样
# 若想留在重构分支但丢弃全部改动：
git switch refactor/dhole-mcp-behavior-preserving
git reset --hard 74524dc                # ⚠ 会丢弃该分支上全部 commit（含 artifacts）

# 方式 B：逐个回滚生产代码改动（推荐，保留 artifacts）
git revert 9391ca0    # 复活 _MAIN_XPATH / _IMPERSONATE_POOL，并还原 links.py docstring
git revert 6e248fe    # 还原 4 处注释/docstring
git revert ebc51dc    # 复活 _reranker_model_present

# 方式 C：只回滚某一个文件到重构前
git restore --source=74524dc -- src/dhole_mcp/updater.py

# 验证回滚成功（三条都应回到基线值）
python -m pytest -q                      # 期望 1128 passed, 2 skipped, 15 deselected
python -m ruff check .                   # 期望 All checks passed!
git diff --stat 74524dc -- src/          # 期望：无输出
```

**注意**：`refactor_artifacts/` 与 `.refactor_backup/` 是本任务的产物/备份，
`.refactor_backup/` 已加入 `.git/info/exclude`（不会进仓库）；`refactor_artifacts/` 是有意提交的。
回滚生产代码时**不需要**删除它们。

---

## 10. 剩余风险与建议人工检查点

| 风险 | 级别 | 建议的人工检查 |
| --- | --- | --- |
| 无测试 gate 的路径（429 / 压缩 / 非 UTF-8 / 并发取消 / `<base href>` / 爬虫端到端） | 中 | 这些路径本轮**未被测试覆盖**，"行为保持"靠"代码未改动"保证。若要更强的保证，先补测试再动代码 |
| 4 个生产 commit 只改了注释与死代码，但 `server.py` 的 `stealthy_fetch` docstring 是我手改的 | 低 | `git show 6e248fe` 复核那 9 行；确认没有误伤 `_TOOL_DEFS` 里的 `smart_fetch` description（已用逐字节 diff 证明没有） |
| KB-6 明文凭据未 0600 | **高（安全）** | 建议单开分支修（仅 `os.chmod`，不改公开行为），**不要并入本重构分支** |
| KB-3/4/5 真实外网流量与模型下载 | 中 | 决定是"文档说明"还是"改成惰性/可关闭"；后者是行为变更，需产品决策 |
| KB-1/KB-2 的语义缺陷 | 中 | 两者都会误导 agent；修它们会改 `tools/call` 内容，必须单独评审 |
| 本机装的是 14.6 旧轮子（KB-8） | 中 | 建议 `pip install -e .`，或在 CI 断言 `dhole_mcp.__file__` 落在 `src/` |
| README/CHANGELOG 仍与代码有 6 处不一致 | 低 | 见 `DOC_CODE_DRIFT.md`；**本轮按任务要求一律不改文档** |
| `python -m pytest -m live` 一次都没跑 | 中 | 在有网环境由维护者跑 `-m live --engine-fixtures check`，验证真实 SERP 解析未退化 |

---

## 11. 未解决问题

1. **README 中未被本轮独立核对的承诺**（时间不足，已在 `DOC_CODE_DRIFT.md` 明确标出"未独立验证"）：
   robots.txt 的正面行为、SSRF 残余覆盖面、PDF 口令三态的实现细节、
   sogou_weixin 的排序与早退规则、`dhole proxy/engines/model/--doctor/-v` 的逐字段输出、
   "4 个索引家族"的家族计数。
2. **CHANGELOG 完全未被用作检查清单**（按任务要求）。因此若 CHANGELOG 声称修过而代码未修，
   本轮不负责发现。
3. **`refactor_artifacts/tmp_home/` 与 `tools/` 下的一次性守卫脚本**（`pyshim/sitecustomize.py`）
   是快照器的运行残留，属于工具产物。**2026-09-22 已清理** —— 二者都由
   `tools/mcp_snapshot.py` 自己在运行时生成（`:376` 写 shim、`:380` 建 `run-*`），
   所以删掉不影响复现，重跑该脚本即可重建。`dist/`（gitignored 构建产物）也已删除，
   见 `DECISIONS.md` D-18。
4. **子代理产物被覆盖的事件**：一个独立 README↔代码审查子代理原本写了 659 行、21 条发现
   （D-01…D-21）到 `DOC_CODE_DRIFT.md`；我在它完成前用自己 124 行的版本**覆盖了它**
   （`Write` 报告"updated"而非"created"，我当下未察觉文件已存在）。原正文不可恢复。
   **后续处理**：其完成报告中的 5 条最高优先级发现 + 1 条环境发现已转述保留，
   并已于追加章节 1 **逐条实测复核完毕（7 条全部证实，其中 2 条转述有误已更正）**；
   未转述的其余条目（D-08…D-21）仍随原正文丢失，不可恢复。
5. **阶段 2 的第 5/6 项（爬虫队列、搜索后端）没有做任何改动**，因为它们没有离线 gate
   （见 `ADVERSARIAL_REVIEW.md` 第五节）。这不是遗漏，是 `FIRST_PRINCIPLES.md` §7 的明确决定。

---

## 附：本报告的核验入口

```bash
cd /d/tools/dhole-mcp
git log --oneline --decorate -8
python -m pytest -q
python -m ruff check .
python refactor_artifacts/tools/mcp_snapshot.py      # 重跑协议快照，与 baseline/ 对比
python refactor_artifacts/tools/tool_payload_measure.py

# 追加工作的复核入口
PYTHONPATH=src python refactor_artifacts/tools/verify_d_findings.py          # D-01…D-07
PYTHONPATH=src python refactor_artifacts/tools/measure_state_perms.py check  # KB-6 现状
git log --oneline fix/kb-6-state-file-permissions -3                        # KB-6 的修复分支
```

`refactor_artifacts/` 内共 11 份交付文档：`TOOLING.md`、`BASELINE.md`、`FIRST_PRINCIPLES.md`、
`CONTRACTS.md`、`DECISIONS.md`、`DOC_CODE_DRIFT.md`、`STAGE_X_REVIEW.md`、
`ADVERSARIAL_REVIEW.md`、`KNOWN_BUGS.md`、`REFACTOR_REPORT.md`、`STATE.json`，
外加 `baseline/`（冻结的原始证据）、`analysis/`（AST 分析原始输出）、`tools/`（可重跑脚本）。

---

# 追加章节：原任务完成后的两项后续工作

> 本任务（行为保持重构）已在阶段 5 收尾。以下两项是**用户在原任务完成后新授权**的工作，
> 记录在此以保持证据链完整。它们**不在**"行为保持"的范围内，也**不在**重构分支的代码里。

## 追加 1：D-01…D-07 逐条复核（**7 条全部证实**）

- **做了什么**：新增 `tools/verify_d_findings.py`，把 `DOC_CODE_DRIFT.md` 附录里 7 条"待复核"
  的转述发现逐条实测。脚本自带 socket 守卫（只放行回环）与 `DHOLE_HOME` 重定向，
  实测外发尝试记录为 `[]`。
- **命令**：`PYTHONPATH=src python refactor_artifacts/tools/verify_d_findings.py`
- **结果**：`analysis/verify_d_findings.txt`

| 编号 | 判定 | 关键实测 |
| --- | --- | --- |
| D-01 | 已证实 | 驱动真实 `cache_clear(engine_state=True)`：重置前 snapshot `['bing','duckduckgo']`，重置后 `engine_health={}`；释放的冷却改由 `message` 文本承载 |
| D-02 | 已证实 | `DHOLE_DEFAULT_ENGINES=bing` → 执行池 `['bing']`，但 `_family_universe(None,[])` 前后都是 `(4, 0, 'single_family')`，分母不动 |
| D-03 | 已证实 | `search.py:1087` 的 `_clamp_note` 确实并入 `fetch_hint`（README 的"静默钳制"已过时） |
| D-04 | 已证实**且缺口更大** | 描述只列 `.html/.docx/.xlsx/.csv/.pdf`，`.htm` 与 `.xhtml` **两个都缺席**（转述只说漏了 `.pdf` 相关） |
| D-05 | 已证实 | `search_proxy.py:271` 的 `health_check` 由 `_kick_health_check()` 自动调度；但**是条件触发**（有代理池时才探活），非无条件启动流量 |
| D-06 | 已证实 | `save_proxies()` 落盘后文件 mode 读作 `0o666`，写入点无 `harden_file`；对照 `circuit_breaker.json` 有 |
| D-07 | 已证实，**但出处描述有误** | `cli.py:124` 硬编码 `expanduser("~")/.dhole` 为真；但 `src/dhole_mcp/repair.py` **这个模块不存在**（`exists=False`），它是运行时生成到 `~/.dhole/` 的脚本 |

- **复核本身抓到的两处转述错误**：D-07 的模块引用是错的；D-04 的范围是漏的。
  **结论：独立子代理可作线索来源，其定位与引用必须复核后才可行动。**
- **落点**：结论已写回 `DOC_CODE_DRIFT.md`（附录表格逐条加"已证实"与实测依据）与
  `KNOWN_BUGS.md`（KB-1/KB-2/KB-3/KB-6/KB-7 补上复核证据与更正）。

## 追加 2：KB-6 权限加固（**单开分支** `fix/kb-6-state-file-permissions`）

- **为什么单开分支**：它是安全加固，**会改变文件模式**，不属于行为保持；且重构分支的
  全部价值就是"可证明不改任何可观察行为"，把 chmod 挂上去会毁掉这个前提。见 `DECISIONS.md` D-12。
- **基线**：从 `wip-before-refactor`（`74524dc`）切出，**不是** `master` ——
  `master` 比 `origin/master` 落后 22 个提交、与工作树差 59 个文件，不是"当前代码"。
- **改动**：新增 39 行、删除 2 行，涉及 6 个 `src/` 文件 + 1 个新测试文件。
  新增 `paths.harden_dir()`（只 chmod、不 mkdir，`harden_file` 的 0700 镜像）；
  在 proxy 配置、`usage.jsonl`、`search_feedback.json`、两个引擎状态文件、`last_version`
  与 updater 的 home 创建处补上收紧调用。
  **零处修改 `mkdir`/`open` 的调用与顺序** —— 建目录失败仍旧照原样抛错（见 `DECISIONS.md` D-13）。
- **commit**：`8dfd6a9`
- **验证**：
  - 全量 `pytest -q` → `1143 passed, 5 skipped, 15 deselected`（基线 1128 + 新增 15）
  - `ruff check .` → `All checks passed!`
  - **反向验证**（新测试拿到修复前源码上跑）：`9 failed, 6 passed, 3 skipped` ——
    失败的 9 条正是"新行为"断言，通过的 6 条正是"行为保持"断言，分组与设计吻合。
    证据：`analysis/kb6_negative_check.txt`
  - before/after 调用计数：`harden_file` 1 → 4，`harden_dir` — → 3。证据：`analysis/kb6_before.txt`、`analysis/kb6_after.txt`
- **诚实边界（2026-09-22 已关闭）**：当时写的是"POSIX 真实权限位没有被本机实测过"——本机是 Windows，
  实测连已有的 `harden_file` 对照组都读作 `0o666`，且没有可用 POSIX 环境。
  **用户随后启动了 Docker，这个缺口已经补上**，见追加章节 5。原先的验证链
  （mock `os.chmod` 断言调用与参数 → 反向验证证明非空转 → 真实 mode 断言标 `skipif`）
  现在最后一环也执行了：**Linux 上 18 passed，修复前的树上 12 failed, 6 passed**。
- **回滚**：`git branch -D fix/kb-6-state-file-permissions`（该分支未合并、未推送）。

## 追加 3：跨分支集成验证（两条线合并后是否仍绿）

- **做法**：临时 `git worktree`（detached）指向重构分支 HEAD，`git merge --no-commit --no-ff`
  合入 KB-6 分支，跑全量与 ruff，然后销毁 worktree。**重构分支本身没有被写入任何合并提交。**
- **冲突探测**：`git merge-tree --write-tree` 退出码 `0`，无冲突；真合并时
  `search_metasearch.py` / `server.py` / `updater.py` 自动合并成功（两边的改动在不同行）。
- **结果**：`ruff check .` → `All checks passed!`；
  `pytest -q` → **1142 passed, 5 skipped, 1 failed**。
- **那 1 条失败不是合并引入的，是 KB-11**：`test_every_fixture_is_content_addressed`。
  我做了决定性实验：在**不含合并**、直接指向同一分支的干净 worktree 上，它**同样失败**。
  根因是 fixture 的行尾：git blob 已按 `.gitattributes` 归一化为 LF，而 `.meta.json` 里
  记录的 sha256 是按 CRLF 字节算的；只有本机这个保留了 CRLF 残留的主工作树才通过。
  **取证与来历已登记为 `KNOWN_BUGS.md` KB-11（含我自己的责任：该仓库此前无提交，
  是我在阶段 0 的首次提交把归一化固化下来的）。**
- **净结论**：**合并是干净的、无回归** —— 1142 = 1143（KB-6 分支全绿数）− 1（KB-11 那条）。
- **顺带得到的一条限定**：本报告所有 `1128 passed` 之类的基线数字，
  **有效性绑定在本机这个主工作树**上；KB-11 会让任何全新克隆跑出不同的红。
  读基线数字时请连同这一条一起读。

## 追加 4：KB-1 + KB-7 修复，以及把版本折回 14.6（分支 `release/14.6`）

- **分支**：`release/14.6`，从 `wip-before-refactor`（`74524dc`）切出 —— 与 KB-6 分开，
  因为这三件事（两个行为修复 + 版本折叠）都不属于"行为保持"。
- **为什么会有这一批**：用户在同一次对话里给了三个判断：① 按建议把 KB-1/KB-7 合起来修；
  ② KB-2 结案为设计取舍；③ **14.7 从未发布**（tag 只到 `v14.6`，14.7 的提交从未推送），
  所以把 14.7 的内容并进 14.6、版本号也改回 14.6。

| commit | 内容 |
| --- | --- |
| `791e061` | **KB-1**：`cache_clear(engine_state=true)` 的 `engine_health` 恒为空 —— 把快照移到 `engine_state_reset()` **之前**。同时收紧 `test_bug_report_regressions.py` 里 `... or out.engine_health == {}` 这个逃生口 |
| `debf82c` | **KB-7**：`cli._run_repair()` 改走 `paths.home()`，与 `updater.repair_script_path()` 一致；两处 docstring 与模块自愈流程说明同步 |
| `c8f11c7` | **版本折叠**：`__version__` 14.7 → 14.6；CHANGELOG 的 `[14.7]` 段并入 `[14.6]`（两个 `### 测试` 合并为一个、正文里的版本引用改写、新增 `### 修复（复验后追加）` 记这两个修复）；README 与 `test_tool_descriptions.py` 里残留的旧版本号同步 |

- **KB-1 的语义选择**：字段描述说的是"dhole 当前看到的池子状态"，而 reset 之后"当前"已经什么都没有了 ——
  所以"取重置后"也能自我辩解。但那样这个字段在 `engine_state=true` 时**结构上只能是空**，
  而它被加进来的目的正是让调用方看到"重置释放了什么"（14.7 自己的说明）。
  取重置前还让 `engine_state=true/false` 报告同一件事（调用当刻的池子）。释放的冷却原本就在 `message` 文本里，信息没丢。
- **验证**：`1129 passed, 2 skipped`（基线 1128 + KB-7 的 1 例）；`ruff` 全绿；
  `tests/test_tool_descriptions.py` 35 例全绿（README 那处改动只换了版本号，没有拿它去对齐代码）。
  全仓 `14.7` 残留为 0。
- **诚实边界**：CHANGELOG 按本任务的规则只是背景材料，我引它只为说明发布惯例。另：
  **KB-6 的 CHANGELOG 条目没有写** —— 那条修复不在这个分支的代码里，等它合并时再补，
  否则会写出一条代码里并不存在的东西。

## 追加 5：用 Docker 补上 POSIX 真实权限实证（关闭 KB-6 的最后一个缺口）

- **网络请求记录**（按任务要求）：`docker pull python:3.12-slim`（179MB，Docker Hub，一次）。
  容器内 `pip install` 一次性拉了 mcp/pydantic/trafilatura/aiosqlite/primp/httpx/lxml/cssselect/
  markdownify/beautifulsoup4/h2/httpcore/pytest 等测试所需依赖。此外无任何外网请求。
- **做法**：`git archive` 出**修复前后两棵源码树**（`74524dc` vs KB-6 分支），
  用同一个 harness（`tools/check_posix_modes.py`，只依赖 stdlib + `paths`，所以裸镜像即可）
  在 `python:3.12-slim`（`os.name=posix`，umask 固定 022）里各跑一遍。
- **结果（真实 POSIX mode）**：

  | 场景 | 修复前 | 修复后 |
  | --- | --- | --- |
  | 全新 home | 目录 `0o755` / 文件 `0o644` | 目录 `0o700` / 文件 `0o600` |
  | 已存在的松目录 `0777` | 保持 `0o777` | 收紧为 `0o700` |
  | 已存在的松文件 `0666` | 保持 `0o666` | 收紧为 `0o600` |

  → **修复前，那台 Linux 上含明文代理凭据的 `search_proxies.json` 是 0644，同机任何用户可读。**
  这不再是推断，是实测。文件内容前后逐字节一致（只改模式）。
- **pytest 端**：同一测试文件在 Linux 上 **修复后 18 passed（0 skipped）**、
  **修复前 12 failed, 6 passed** —— 那 3 条在 Windows 上被 `skipif` 跳过的真实 mode 断言，
  这次真的跑了，而且真的能区分修复前后。
- **附带确认 KB-11 与平台无关**：`git archive` 出来的树即"新克隆"的样子（blob 原样 = LF），
  在 Linux 上 `TestFixtureAntiRot` 同样失败（`yandex.html be8729f06d59112e → 6aa68cc8433ab2f1`），
  4 条里 1 失败 3 通过。**假绿只在保留 CRLF 残留的本机工作树成立**，这条现在有跨平台证据。
- **仍未覆盖**：容器里只跑了 `test_state_file_permissions.py` 与 `TestFixtureAntiRot`，
  **没有**在 Linux 上跑全量 `pytest`。全套在 Linux 上的状态仍然未知。
- **证据**：`analysis/kb6_posix_before_after.txt`、`analysis/kb6_posix_pytest.txt`（A/B/C 三段）。

## 追加 6：三条分支的最终集成验证

- **做法**：临时 detached worktree → 依次合入 `fix/kb-6-state-file-permissions` 与 `release/14.6`
  （每次都提交合并，worktree 用完即销毁，**四个分支本身都没有被写入合并提交**）。
- **冲突**：**无**。三个文件自动合并（`updater.py` / `server.py` / `search_metasearch.py` 等改在不相邻区域）。
- **合并后核对**：`__version__` = **14.6**；`paths.harden_dir` 存在；`cli.py` 里有 2 处 `paths.home()`；
  `server.py` 的快照前置注释在位。
- **全量**：`1143 passed, 5 skipped, 1 failed`，`ruff check .` → `All checks passed!`
  - 账目对得上：1128（基线）+ 15（KB-6 新增）+ 1（KB-7 新增）− 1（KB-11 那条失败）= 1143；
    5 skipped = 2（基线）+ 3（KB-6 的 POSIX 断言在 Windows 上跳过）。
  - **那 1 条失败就是 KB-11**（fixture 行尾哈希），与本轮任何改动无关，在干净 worktree 上同样失败，已在 Linux 上复现。
- **仍未做**：没有把任何分支合并进 DEFAULT 分支、没有推送、没有删分支（按任务禁令）。
