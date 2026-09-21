# KNOWN_BUGS.md — 既有缺陷登记（本轮只记录，不修）

> 铁律 4：发现原 bug 只记录，不修（除非它阻止建立测试基线 —— 未发生）。
> 每条都标注**是实测还是读代码推断**，并给出可复现命令。
> 本轮**没有**对以下任何一条动手。

---

## KB-1 `cache_clear(engine_state=true)` 的 `engine_health` 永远是空对象

- **来源**：子代理审查（README↔代码交叉核对），**2026-09-22 已独立实测复核（D-01 已证实）**。
- **现象**：工具描述（`server.py:4068`）与 README 都承诺"`engine_state=true` 时回复里报告 `engine_health`"。
  实测的实现顺序是**先** `engine_state_reset()` 清空内存字典、**再** `engine_state_snapshot()`，
  于是快照必然为空 `{}`。
- **复核证据**（`analysis/verify_d_findings.txt`）：驱动真实 `MasterFetchServer.cache_clear(engine_state=True)`，
  重置前 snapshot 非空 `['bing','duckduckgo']`，重置后 `engine_health={}`，
  而 message 里确实带了 `Engine state forgotten: 1 engine record(s), 1 cooldown(s) released (bing).`
  ——**信息没丢，只是不在 agent 被指引去读的那个字段里。**
- **影响**：外部可观察 —— agent 依赖这个字段判断引擎池是否恢复，拿到 `{}` 会误判。
- **建议**：先快照再重置（或重置前把旧状态传出去）。**属于行为变更，需产品决策。**
- **本轮处置**：原为"仅记录"。**2026-09-22 经用户授权后已修** —— 分支 `release/14.6`，
  commit `791e061`：快照移到 reset 之前；同时把 `tests/test_bug_report_regressions.py` 里
  `... or out.engine_health == {}` 这个掩盖了它的逃生口收紧为"必须非空"。

## KB-2 `DHOLE_DEFAULT_ENGINES` 不改变 `engines_consensus` 的分母

> **2026-09-22 二次复核：本条**不是缺陷**，是设计取舍。原判据已被自己的实测推翻，保留原文并附更正。**
> 触发更正的测量：构造各场景的 `EngineReport` 直接调 `_family_universe()`（命令见文末）。

- **来源**：子代理审查，**2026-09-22 已独立实测复核（D-02 的"机制"部分已证实）**。
- **已证实的事实**：`DHOLE_DEFAULT_ENGINES=bing` 时 `_configured_default_backends()` 确实收敛为 `['bing']`
  （执行池生效），但 `_family_universe(None, [])` 在设 env 前后**都是** `(4, 0, 'single_family')` ——
  分母 4 纹丝不动。根因：`_family_universe` 读的是 `search_engines.DEFAULT_ENGINES`（固定 tuple），
  只对**工具参数** `engines=[...]` 敏感，对 env 不敏感。
- **❌ 被推翻的判断（原文保留以示更正）**：原文写"自建小池会被 agent 读成'池降级'
  （`consensus_basis` 可能落到 `partial_pool`）"。**实测不成立**：
  - 只看 bing → `basis=single_family`（不是 `partial_pool`）。**安全信号本来就生效**：
    agent 已被明确告知"只有一家在说话"。
  - `partial_pool` 需要真有家族被 preempted；`degraded_pool` 需要真有引擎被 blocked。两者的判据
    都取自 `reports`（实际轮次），**与分母用哪个池无关**。
  - 分母是**按家族**算的（6 引擎 → 4 家族：bing←bing/duckduckgo/yahoo），不是按引擎，这个折叠是有意的。
- **❌ 建议也被推翻**：原文建议"让分母跟随实际启用的池"。按此实现，1 引擎的池会输出
  **"1 of 1"** —— 而 `_family_universe` 的 docstring 明确说这正是它要避免的字符串
  （"1 of 1 与全员一致在字符串上完全不可区分"）。**按建议修会比现状更糟。**
  docstring 里"分母 = 本轮**本该**表态的家族数"这个定义是自洽的；env 收窄池属于
  "用户主动缩小了本该表态的范围"，而这件事由 `consensus_basis=single_family` 如实标出。
- **结论**：**不是缺陷。**若仍想动，正确方向是**在文档里说明分母的含义**
  （"默认池的索引家族数"），而不是改分母。属于文档改动 = 改 `tools/list`，仍需产品决策。
- **实测命令**（可复现；用真实 `EngineReport` 构造各场景）：
  ```bash
  PYTHONPATH=src python -c "
  from dhole_mcp.search import _family_universe
  from dhole_mcp.search_engines import EngineReport, DEFAULT_ENGINES
  for label, r in {
    '默认 6 引擎全答': [EngineReport(name=e, ok=True) for e in DEFAULT_ENGINES],
    '只看 bing':      [EngineReport(name='bing', ok=True)],
  }.items(): print(label, _family_universe(None, r))
  "
  ```
- **本轮处置**：更正记录，**未改代码**。

## KB-3 配置了代理时会向 `example.com` 发真实探测请求（fire-and-forget）

- **来源**：子代理审查，**2026-09-22 已独立实测复核（D-05 已证实，附条件限定）**。
- **现象**：存在代理配置时，进程会异步向 `example.com` 发探测请求以验证代理可用性。
  这与 README 中"不做后台真实请求"一类绝对化措辞冲突。
- **复核证据**（`analysis/verify_d_findings.txt`）：`search_proxy.py:271` 定义
  `health_check(probe_url="https://example.com", timeout=10)`，由 `_kick_health_check()`
  → `loop.create_task(pool.health_check())` 自动调度。
- **条件限定（比转述更准）**：它是**条件触发**（存在代理池且需要探活时），
  **不是**无条件启动流量。本轮 MCP 快照未配置代理，所以在 socket 守卫下未观测到这条流量 ——
  这解释得通，不构成反证。
- **影响**：外部可观察（有真实外网流量）。对"离线可复现"的测试承诺是威胁。
- **建议**：改为可关闭，或在文档中明确写出这一例外。
- **本轮处置**：仅记录。**未修**。这也是为什么本轮所有 MCP 实测都必须带 socket 守卫（见 `TOOLING.md` §6）。

## KB-4 启动阶段 preflight `1.1.1.1:443`

- **来源**：本轮 MCP 快照实测（子代理 1 报告）。
- **现象**：一次裸启动 `python -m dhole_mcp` 会产生一次到 `1.1.1.1:443` 的连接（浏览器预热）。
  本轮快照器用 socket 守卫把它拦下了，所以快照是离线的；**但没有守卫的运行会真的外发**。
- **影响**：外部可观察（网络流量）。
- **建议**：延后到首次真正需要浏览器时再做，或提供关闭开关。
- **本轮处置**：仅记录。**未修**。

## KB-5 首次 `smart_search` 会触发 reranker 权重下载

- **来源**：本轮 MCP 快照实测（子代理 1 报告）。
- **现象**：空 `DHOLE_HOME` 下第一次有效 `smart_search` 会尝试从 `huggingface.co` / `hf-mirror.com`
  下载模型权重。本轮用指向死端口的本地代理让它**快速失败**，并在 `tmp_home` 留下空的 `models/bge-zh` 目录。
- **影响**：外部可观察（网络 + 磁盘写入，数百 MB 量级）。README 未说明这一点。
- **建议**：文档明确"首次搜索需要下载模型/需要网络"，或提供预置模型的说明。
- **本轮处置**：仅记录。**未修**。

## KB-6 明文凭据文件缺少 `0600/0700` 权限收紧

- **来源**：子代理审查，**2026-09-22 已独立实测复核（D-06 已证实）**。
- **现象**：`search_proxies.json`（含明文代理凭据）与 `usage.jsonl`、`search_feedback.json`
  未套用 README 叙事里承诺的权限收紧（`0700` 目录 / `0600` 文件）。
- **复核证据**（`analysis/verify_d_findings.txt`）：调 `save_proxies(["http://user:secret@10.0.0.1:8080"])`
  落盘后 `search_proxies.json` 的 mode 是 **`0o666`**（受 umask 影响），文件内容就是明文凭据。
  该写入点走裸 `path.parent.mkdir(...)` + `open(path, "w")`，**没有** `harden_file`。
- **重要限定（比转述更准）**：这不是"README 全篇虚构"——`paths.py` 里
  `ensure_private_dir()`（0700）与 `harden_file()`（0600）**确实存在且被使用**，
  `circuit_breaker.json` / `engine_stats.json` 就在 `os.replace` 之后补了 `paths.harden_file(path)`。
  **缺口是覆盖不全**：恰好漏掉了含真实凭据的那一个文件（以及另外两个状态文件），
  且 `save_proxies` 的父目录 `mkdir` 也没走 `ensure_private_dir`。README#269 的表述是对**全部**状态文件的。
- **影响**：**安全面** —— 多用户机器上同机其他用户可能读到代理凭据。
  但**不改公开行为**即可修复（只改文件模式）。
- **建议**：写入时显式 `os.chmod`。这属于安全加固，**不在"行为保持"重构范围内**。
- **本轮处置**：原计划只记录；**经用户授权后已在独立分支动手修复**
  （分支 `fix/kb-6-state-file-permissions`，commit `8dfd6a9`；决策见 `DECISIONS.md` D-12/D-13/D-14，
  实施与验证证据见 `REFACTOR_REPORT.md` 追加章节 2）。本条保留为缺陷登记原文。
- **✅ POSIX 真实权限已实测（2026-09-22，Docker）**：原先"POSIX 真实 mode 未实测"的缺口**已关闭**。
  在 `python:3.12-slim`（`os.name=posix`，umask 固定 022）里用同一个 harness 跑修复前后两棵树：

  | 场景 | 修复前（`74524dc`） | 修复后（KB-6 分支） |
  | --- | --- | --- |
  | 全新 home | 目录 `0o755` / 文件 **`0o644`** | 目录 `0o700` / 文件 **`0o600`** |
  | 已存在的松目录 `0777` | 保持 `0o777`（不收紧） | 收紧为 `0o700` |
  | 已存在的松文件 `0666` | 保持 `0o666`（不收紧） | 收紧为 `0o600` |

  **即：修复前那台 Linux 上含明文凭据的 `search_proxies.json` 是 0644 —— 同机任何用户可读。**
  文件内容在修复前后逐字节一致（只改模式）。
  另有 pytest 端证据：同一测试文件在 Linux 上 **修复后 18 passed / 修复前 12 failed, 6 passed**。
  证据：`analysis/kb6_posix_before_after.txt`、`analysis/kb6_posix_pytest.txt`；
  harness：`tools/check_posix_modes.py`。

## KB-7 `dhole` 的 repair 路径硬编码 `~/.dhole`，不跟随 `DHOLE_HOME`

- **来源**：子代理审查，**2026-09-22 已独立实测复核（D-07 已证实，但转述的出处有误）**。
- **现象**：`updater.py` 跟随 `DHOLE_HOME`，而 `cli.py` 里 `_run_repair()` 这条路径不跟随。
  **同一产品内部两种写法不一致。**
- **复核证据**（`analysis/verify_d_findings.txt`）：`cli.py:124` 是
  `repair = os.path.join(os.path.expanduser("~"), ".dhole", "repair.py")` ——
  句柄是**字面量拼出来的**，完全不看 `DHOLE_HOME`；`_run_repair` 函数体内
  `paths.home()` / `DHOLE_HOME` 出现次数为 **0**，而 `updater.py` 使用 `paths.home()` 为 `True`。
- **更正转述**：原转述写"`cli.py:124` 引用 `repair.py`，后者硬编码 `~/.dhole`"。
  实测 `src/dhole_mcp/repair.py` **不存在**（`exists=False`）——`repair.py` 是
  **运行时生成到 `~/.dhole/` 的脚本**，不是包内模块。事实成立，出处指错了地方。
- **影响**：外部可观察 —— 设了 `DHOLE_HOME` 的用户，repair 会去动真实 `~/.dhole`，
  与 README 的状态目录叙事矛盾。
- **建议**：统一走 `paths.home()`。
- **本轮处置**：原为"仅记录"。**2026-09-22 经用户授权后已修** —— 分支 `release/14.6`，
  commit `debf82c`：改走 `paths.home()`；新增测试同时钉住"落在 DHOLE_HOME 下"与"不在真实 home 建任何东西"。

## KB-8 本机 `site-packages` 装的是 14.6 旧轮子，不带 `PYTHONPATH=src` 会静默验证旧代码

- **来源**：子代理审查（并在其自己的运行中撞到）。
- **现象**：`D:\Program Files\Python314\Lib\site-packages` 里的 `dhole_mcp` 是 **14.6** 的构建产物，
  而仓库是 **14.7**。任何不经 `PYTHONPATH=src` 的 `import dhole_mcp` 都会加载旧副本。
  `pyproject.toml:103-109` 的注释正是为此而写（`pythonpath = ["src"]`，注释自述此坑已咬过项目 4 次以上）。
- **影响**：**对验证结论的威胁** —— 若验证脚本忘了设 `PYTHONPATH`，会"通过"但验的是旧代码。
- **本轮如何规避**（每条都是可核查的）：
  - pytest 通过 `pyproject.toml` 的 `pythonpath=["src"]` 生效；
  - 我的所有静态分析脚本只做 `ast` 解析，**从不 import** `dhole_mcp`；
  - 需要 import 的命令一律写成 `PYTHONPATH=src python -c ...`；
  - `mcp_snapshot.py` 以子进程方式启动 `PYTHONPATH=src python -m dhole_mcp`。
- **建议**：把开发环境改成 `pip install -e .`（可编辑安装），或在 CI 里断言 `dhole_mcp.__file__` 落在 `src/`。
- **本轮处置**：仅记录。**未修**（装/卸包不在本任务授权内）。

## KB-9 `server.py` 中 `410` 被列在 archive 回退条件里但永远到不了 archive

- **来源**：本轮实测（`_should_try_archive` 直接调用）。
- **现象**：`server.py:3498` 判断 `result.status in (404, 410, 451)`，但闸门 `_should_try_archive()`
  对 410 返回 `False`。实测：`[(404, True), (410, False), (451, True)]`。
- **影响**：外部可观察 —— 410 Gone 的页面**不会**走 Wayback 回退，与代码字面意图不符。
- **本轮处置**：**只改了注释**（在 `6e248fe` 中说明 410 会被拒），**没改代码** ——
  让 410 真的走 archive 属于行为变更，超出本任务边界。
- **建议**：确认这是有意为之（也许 410 被认为不值得回退）还是遗漏；若是遗漏，另开 PR 修。

## KB-10 静态查表常量被 AST 归类为"可变全局"，容易误判为共享状态

- **来源**：本轮 `analysis/module_inventory.txt`。
- **现象**：`search.py` 的 `_INTENT_EXPANSIONS`、`errors.py` 的 `_PATTERNS`、`links.py` 的 `_NAV_TAGS` 等
  用字面量声明，AST 会把它们列为模块级可变容器；但它们**从不上写**，实际是常量。
- **影响**：不是缺陷，是**阅读陷阱** —— 后续维护者可能以为存在竞态而去加锁。
- **本轮处置**：仅记录（已在 `FIRST_PRINCIPLES.md` §5.2 与 §7 S-3 说明为何**不值得**为此改动）。

---

## 本轮**主动排除**的"疑似 bug"（避免误报）

| 疑似 | 结论 | 依据 |
| --- | --- | --- |
| `server.py:1454` 返回文案提到 "dynamic fetcher"（该 tier 已删） | 是**返回内容**，不是注释；无测试钉住 | 未改。改它属于行为变更，需显式授权 |
| `crawl.py:84-88` `_is_transient_error` docstring 未提 `"unknown"` 也按 transient 处理 | **不完整，不虚假** | 排除，避免把"没写全"当成错误注释 |
| `paths.py:121` 写 "~90MB" vs `:132` 写 "90-450MB" | 指代不同模型（legacy vs 当前），未证实为假 | 排除 |
| `search_engines._VERTICAL_BACKENDS` 与 `search_metasearch._VERTICAL_BACKENDS` 重复 | **有意重复**，原地有注释说明，且被 `test_engine_registry.py` 钉住 | 排除，不做"去重" |
| `pdf_extractor.py:260/:290` 的嵌套 `def g(*keys)` 逐字节相同 | 真实重复，但提升为模块级会动到两处调用路径 | 排除，风险大于收益 |
| `server.py` 三处 archive 回退块逐字节相同 | 真实重复，但位于升级/回退关键路径 | 排除，同上 |
| src/ 中是否存在 `TODO/FIXME/XXX/HACK` | `grep -rn` 结果为空 | 无此类标记，无需清理 |
| 空壳 schema 是否仍抛异常 | 现有测试已改为**结构化拒绝** | 由 `test_schema_param.py`(15) 与 `TestStructuredInputErrors` 覆盖 |

## KB-11 `test_every_fixture_is_content_addressed` 在任何全新克隆上都必然失败

- **来源**：本轮**跨分支集成验证时实测发现**（不是从文档或子代理来的）。
- **现象**：`tests/test_engine_parsers.py::TestFixtureAntiRot::test_every_fixture_is_content_addressed`
  把 `sha256(fixture 的字节)[:16]` 与 `.meta.json` 里记录的 `sha256` 对比。记录的哈希
  **是按 CRLF 字节算的**，而 git 里存的 blob 是 **LF**（`.gitattributes` 的 `* text=auto eol=lf`
  在提交时把行尾归一化了）。于是**全新克隆/全新 worktree 一checkout 就失败**。
- **实测数据**（`bing_variant_a.html` 为例）：

  | 位置 | 字节 | sha256[:16] |
  | --- | --- | --- |
  | 本机主工作树（CRLF，`.gitattributes` 之前的残留） | `crlf=3 lf=3` | `aab4462b07e0e5b3` ← 与 `.meta.json` 记录一致 |
  | git 里的 blob（已归一化） | `crlf=0 lf=3` | `3deddbc6ff9df2fc` ← 与记录不一致，测试失败 |

  四个 fixture 在主工作树全部 `OK`（4/4），在全新 worktree 上该测试**失败**。
- **决定性实验**：在一个**不含任何合并**、直接指向同一分支的干净 detached worktree 里，
  这条测试同样失败 → **与我的合并无关**，纯粹是 checkout 行尾问题。
- **根因与来历（含我自己的责任）**：该仓库在本轮之前**没有任何提交**（unborn HEAD）。
  我在阶段 0 用 `74524dc` 把工作树快照成首次提交，git 按 `.gitattributes` 把 fixture 的
  blob 归一化成 LF，而 `.meta.json` 里的哈希是更早按 CRLF 工作树算的。
  **提交前"全新克隆"这个场景从未被走过**，所以这个不一致是被我的首次提交固化下来的。
- **影响**：
  1. **假绿**：本项目"fixture 不许烂"的守卫只在**保留着 CRLF 残留的这个工作树**上为真；
     换任何新环境（新同事、CI、`git clean` 后重checkout）整套测试会红。
  2. **对本轮结论的限定**：报告里"1128 passed"这类基线数字，**其有效性绑定在这个工作树**上。
     这一条应随基线数字一起读。
- **建议**（两条选一，都需产品决策，本轮**未动**）：
  - `tests/engine_fixtures/*.html -text`（禁止行尾转换）——让 checkout 逐字节复现记录哈希时的字节；
  - 或按 LF 字节**重新记录**四个 `.meta.json` 的 sha256。
- **Linux 复现（2026-09-22，Docker）**：`git archive` 出来的树就是"新克隆"的样子（blob 原样 = LF），
  在 `python:3.12-slim` 上跑 `TestFixtureAntiRot` 得到同一条失败：
  `yandex.html 被改动过（be8729f06d59112e → 6aa68cc8433ab2f1）`，
  **4 条里失败 1 条、通过 3 条** —— 证明它与 Windows 无关，就是"记录的哈希按 CRLF、入库的是 LF"。
  证据：`analysis/kb6_posix_pytest.txt` 的 C 段。
- **本轮处置**：**仅记录，未修**。改 `.gitattributes` 或改 fixture 哈希都属于改仓库跟踪内容，
  超出"行为保持重构"与 KB-6 的授权范围。
