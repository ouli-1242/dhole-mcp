# DOC_CODE_DRIFT.md — README/CHANGELOG 与代码的不一致清单

> 阶段 1/3 产出。**本轮只记录，不修**（除非阻止绿色基线 —— 未发生）。
> 唯一契约来源是代码与实测；README 是"作者希望的行为"，CHANGELOG 只是背景。
> 每条都附**实际执行过的命令**；无法证实的标"未独立验证"，不猜测。

---

## D-1 README 提到的测试守卫 `_legacy_migrated` 不存在这个名称

- **文档位置**：`README.md`（测试守卫小节，随 README 版本移动；用 `grep -n "_legacy_migrated" README.md` 定位）
- **文档声称**：`tests/conftest.py` 中存在守卫 `_no_real_home_state_writes` / `_legacy_migrated` / autouse 固定 `getaddrinfo`。
- **代码/实测**：`tests/conftest.py` 里三个 autouse fixture 的真实名称是
  `_no_real_home_migration`、`_offline_dns`、`_no_real_home_state_writes`。
  第三个名字对得上；**没有叫 `_legacy_migrated` 的 fixture** ——
  `_legacy_migrated` 是一个**被设置的标志**：`monkeypatch.setattr(paths, "_legacy_migrate_done", True)`。
- **证据**：`grep -n "^def _\|^@pytest.fixture" tests/conftest.py`
  → `_no_real_home_migration`(L10) / `_offline_dns`(L25) / `_no_real_home_state_writes`(L69)；
  `grep -rn "_legacy_migrated" tests/` → 无命中；`grep -n "_legacy_migrate_done" tests/conftest.py` → 命中。
- **影响**：不影响外部可观察行为；不影响 agent 路由；**影响测试文档的可信度**（按 README 找守卫会找不到）。
- **建议**：改文档（把 `_legacy_migrated` 改成"`_no_real_home_migration` 设置的 `_legacy_migrate_done` 标志"）。
- **本轮处置**：记录，不修。

## D-2 README 的 token 表停留在 14.6，无法与当前值直接比较

- **文档位置**：README 的 token 表（自述"尚未按 14.7 重新测"）。
- **文档声称**：v14.6 实测 `instructions` 333 tokens / `tools/list` 2,931 tokens / 合计 3,264 tokens。
- **代码/实测（本轮重测，两种独立方法互证）**：

  | 项 | 协议层实测（`tools/mcp_snapshot.py`） | 源码 AST 实测（`tools/tool_payload_measure.py`） |
  | --- | --- | --- |
  | `instructions` 字符数 | 1399 | 1399 |
  | `tools/list`（compact JSON）字符数 | 11499 | 11499 |
  | `tools/list`（默认分隔符 JSON）字符数 | 11846 | 11846 |
  | 工具数 | 8 | 8 |

  两法完全一致，说明测量可信。按 `字符数/4` 粗估：`instructions` ≈ 350、`tools/list` ≈ 2877。
- **证据**：`python refactor_artifacts/tools/tool_payload_measure.py`；`python refactor_artifacts/tools/mcp_snapshot.py`；
  原始输出 `baseline/tool_payload_measure.txt`、`baseline/mcp_size_metrics.json`。
- **影响**：不影响外部可观察行为。**但无法声称与 README 字节级一致** ——
  README 记的是 token 数，本轮只有字符数；仓库里**没有任何地方记录作者用的是哪个 tokenizer**，
  离线环境也没有可用 tokenizer。char/4 粗估（350 / 2877）与 README 的 333 / 2931 同量级但不等，
  这与"不同 tokenizer 会给出不同数"是一致的，**不能据此断定负载漂移**。
- **建议**：改文档（在 README 里补测 14.7 的 token 表，并注明所用 tokenizer）。
- **本轮处置**：记录，不修；实际字符数已报告在 `BASELINE.md` B5 与本表。

## D-3 "默认 6 个免密引擎"已证实；"4 个索引家族"只部分证实

- **文档位置**：README 的"默认池 6 个引擎 / 4 个索引家族"。
- **代码/实测**：
  - **6 个引擎：证实。** `src/dhole_mcp/search_engines.py:60`
    `DEFAULT_ENGINES = ("bing", "duckduckgo", "brave", "yahoo", "yandex", "sogou_weixin")` —— 恰好 6 个。
    且 `smart_search` 的 description 文本（`server.py:4054`）与之一致，实测 `tools/list` 也如此。
  - **4 个索引家族：未独立验证。** 只证实存在 `_INDEX_FAMILY` 常量（`search_engines.py`，AST 实测存在于模块级赋值）
    与 `consensus_basis` 的取值 `full|single_family|partial_pool|degraded_pool`。
    **本轮没有对家族数量计数**，因此不声称"4"是对是错。
- **证据**：`grep -n "DEFAULT_ENGINES" src/dhole_mcp/search_engines.py`；
  `analysis/module_inventory.txt` 的模块级状态清单含 `search_engines: _FRESHNESS_TO_TIMELIMIT, _INDEX_FAMILY`。
- **影响**：6 个引擎与 agent 路由相关（已在 `CONTRACTS.md` C2.4 固化）；家族数只影响 `consensus_basis` 的解读。
- **建议**：仅记录（若要较真，需先定义"索引家族"的判据再计数）。
- **本轮处置**：记录，不修。

## D-4 错误面不对称：README 未描述，但它是真实且被测试钉住的契约

- **文档位置**：README（未见描述）；本条属于"实测发现，文档未覆盖"。
- **代码/实测**：实测 34 次 `tools/call` 后发现**错误封装方式按工具分裂**：
  - `isError=false` 承载校验/SSRF 类错误：`smart_fetch`、`smart_crawl`、`smart_search`、`parse`、`resolve_url`
  - `isError=true` 承载同类错误：`screenshot`、`feed_fetch`
- **证据**：`refactor_artifacts/baseline/mcp_tools_call_index.json`（34 条；16 条 `isError=true`、18 条 `false`）；
  `baseline/mcp_calls_log.txt` 含逐条入参与原始响应。
  另有 `tests/test_bug_report_regressions.py::TestFailureContentIsEmpty` 钉住部分语义。
- **影响**：**影响外部可观察行为** —— 客户端若按 `isError` 判断失败，对 `smart_fetch` 会误判为成功。
  这是既有设计/历史行为，不属于本轮引入。
- **建议**：改文档（在 README 里明确哪些工具用 `isError` 报错），或改代码统一 —— 后者是行为变更，需产品决策。
- **本轮处置**：记录，不修；并已列为 `CONTRACTS.md` C3 必须保持的契约（不许"顺手统一"）。

## D-5 实测到 README 未提及的两个副作用（对"离线/零网络"承诺有影响）

- **文档位置**：README 的安装/隐私/网络相关小节（未见描述这两项）。
- **代码/实测**：
  1. **启动即预热外网**：服务器启动阶段会 preflight `1.1.1.1:443`。快照器用 socket 守卫拦下，
     但**一次裸启动 `python -m dhole_mcp` 会产生真实外发连接**。
  2. **首次搜索会触发模型下载**：空 `DHOLE_HOME` 下第一次有效 `smart_search` 会尝试从
     `huggingface.co` / `hf-mirror.com` 下载 reranker 权重（本次用指向死端口的本地代理让其快速失败，
     并在 `tmp_home` 留下了空的 `models/bge-zh` 目录）。
- **证据**：`refactor_artifacts/tools/mcp_snapshot.py` 的 socket 守卫与运行日志；
  `baseline/mcp_server_stderr.log`；`baseline/mcp_size_metrics.json` 的 `dhole_home_used` 字段。
- **影响**：**影响外部可观察行为**（有网络流量、有磁盘写入）。这不是本轮引入的，但 README 未说明，
  而任务要求"所有网络测试优先本地 mock" —— 正因如此，本轮的所有 MCP 实测都必须在**有守卫**的前提下进行。
- **建议**：改文档（说明"首次搜索会下载模型"与启动预热），或改代码把两者变为惰性/可关闭。
- **本轮处置**：记录，不修；已在 `KNOWN_BUGS.md` 立项，并在 `TOOLING.md` §6 记录本轮如何抑制。

## D-6 `_TOOL_DEFS` 的键顺序不等于 wire 上的键顺序（我自己的文档曾写错，已修）

- **文档位置**：`refactor_artifacts/CONTRACTS.md` C2.0（**本轮自建文档**，非 README）。
- **文档声称（原文）**："`tools/list` 返回顺序即此列表顺序"——容易被读成"键顺序也一致"。
- **代码/实测**：`server.py:4127` 用 `Tool(**td)` 构造，MCP SDK 会把每个工具对象**重新序列化**，
  实测 wire 上的键顺序是 SDK 归一化后的 `annotations, description, inputSchema, name`，
  **不是** `_TOOL_DEFS` 里的 `name, description, inputSchema, annotations`。
  数组内的**元素顺序**（8 个工具的先后）确实与源码一致。
- **证据**：`baseline/mcp_raw_wire.txt` 首行；`baseline/mcp_tools_list_result_pretty.json`。
- **影响**：不影响行为（JSON 对象键顺序无语义）；只影响"逐字节比对"的做法是否可行。
- **建议**：改文档（已改）。
- **本轮处置**：**已修**（`CONTRACTS.md` C2.0 补一句说明键顺序由 SDK 归一化）。

---

## 已核对且**一致**的项（证明覆盖是真的，不是只挑错）

| 项 | 结论 | 证据 |
| --- | --- | --- |
| 8 个工具名与顺序 | 一致 | 源码 `_TOOL_DEFS` 与实测 `tools/list` 完全一致 |
| `cache_clear` 的 `engine_state` 参数 | 存在 | `server.py:4071`；实测 schema 含该键 |
| `cache_clear` 的注解 | 一致 | `readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False` |
| 各工具 `required` | 一致 | 实测：smart_crawl/screenshot/smart_search/parse/resolve_url 各自 required；smart_fetch/cache_clear 无 required |
| 版本单一来源 | 一致 | `__version__` = 14.7；`pyproject.toml` 为 `dynamic=["version"]` + hatch `path=src/dhole_mcp/__init__.py` |
| 协议版本 | 一致 | 请求 `2025-06-18`，返回原样回显，无重协商（`baseline/mcp_initialize.json`） |
| 测试默认离线 | 一致 | `addopts = -m "not e2e and not live"`；1128 passed / 2 skipped / 15 deselected |
| `LICENSE` / `NOTICE.ddgs.txt` | 存在且未改 | 本轮 4 次提交均未触及这两个文件（`git show --stat` 可查） |
| README 点名的 8 个测试文件 | 全部存在 | `tests/test_tool_descriptions.py`(35)、`test_bug_report_regressions.py`(68)、`test_schema_param.py`(15)、`test_pdf_real.py`(6)、`test_proxy_cli.py`(39)、`test_doctor.py`(13)、`test_browser_ssrf.py`(15)、`engine_fixtures/` 目录 |

**未独立验证（不声称对错）**：README 关于 robots.txt 的具体承诺、SSRF 残余面、
PDF 口令三态的实现细节、sogou_weixin 的排序与早退规则、`dhole proxy/engines/model/--doctor/-v`
的逐字段输出。这些都需要单独的命令逐条核对，本轮时间不足以完成，**已在 REFACTOR_REPORT 的"未解决问题"中列出**。

---

# 附：另一路独立审查的发现（D-01…D-21）

> **重要披露**：本附录来自一个独立的 README↔代码交叉核对子代理。它原本把 21 条发现
> （D-01…D-21，659 行）写进了本文件；我在它完成**之前**用自己那份 124 行的版本覆盖了它
> （`Write` 报告"updated"而非"created"，我当下未察觉文件已被创建）。**这是我的操作失误**，
> 原始 659 行正文已不可恢复（子代理的原始输出文件是本会话的 transcript，按规则不读取）。
> 下面是从该子代理**完成报告**中原样转述的 5 条最高优先级发现与 1 条环境发现；
> 它们**未经我逐条复核**，标注为"来自独立审查，待复核"。

| 编号 | 文档声称 | 代码/实测 | 影响 | 建议 |
| --- | --- | --- | --- | --- |
| D-01（待复核） | README#L125 与 `cache_clear` 工具描述都承诺：`engine_state=true` 时响应回报 `engine_health` | 实测实现顺序是**先** `engine_state_reset()` 清空内存字典、**再** `engine_state_snapshot()`，因此 `engine_health` 恒为 `{}` | **外部可观察**（agent 会误判引擎池已恢复） | 改代码（先快照后重置）；已登记 `KNOWN_BUGS.md` KB-1 |
| D-02（待复核） | README 让 agent 读 `engines_consensus` / `consensus_basis` 判断池是否降级 | `DHOLE_DEFAULT_ENGINES` 只换执行引擎，不改 `engines_consensus` 分母（实测 `_family_universe(None,…)` 仍返回 `(4,2)`） | **外部可观察**（自建小池会被读成"池降级"） | 改代码；已登记 KB-2 |
| D-03（待复核） | README#L172 称 `max_results` 越界是"静默钳制" | 代码会把 `max_results=100 is outside the supported 1-50 range…` 追加进 `fetch_hint` | 影响 agent 看到的提示文本（属内容，非结构） | 改文档 |
| D-04（待复核） | README#L122 的 `parse` 行只列部分扩展名 | 代码 `SUPPORTED_EXTENSIONS` 还含 `.pdf/.htm/.xhtml`；且 `tests/test_tool_descriptions.py` **专门钉住"描述必须写 .pdf"** | README 是唯一说"本地 PDF 不行"的地方；改 README 可能破测试 | 改文档（谨慎：先跑 `test_tool_descriptions.py`） |
| D-05（待复核） | README 有"不发后台真实请求"一类绝对措辞 | 有代理配置时进程会 fire-and-forget 真实探测 `example.com` | **外部可观察**（真实外网流量） | 改文档或改代码；已登记 KB-3 |
| D-06（待复核） | README 的状态文件权限叙事（`0700` 目录 / `0600` 文件）自洽 | `search_proxies.json`（**明文代理凭据**）与 `usage.jsonl` 未套用权限收紧 | **安全面**；不改公开行为即可修（仅 chmod） | 改代码（安全加固，另开 PR）；已登记 KB-6 |
| D-07（待复核） | README 的 `DHOLE_HOME` 叙事 | `cli.py:124`→`repair.py` 硬编码 `~/.dhole`，不跟随 `DHOLE_HOME`（而 `updater.py` 跟随）——**产品内部两种写法不一致** | **外部可观察**（修repair 会动真实 `~/.dhole`） | 改代码；已登记 KB-7 |

**环境发现（此条我已用自己的证据独立确认过）**：本机 `site-packages` 里装的是 **14.6 的旧轮子**，
而仓库是 14.7。**任何不带 `PYTHONPATH=src` 的 `import dhole_mcp` 都会静默加载旧代码**——
`pyproject.toml:103-109` 的注释正是为此而写（自述此坑已咬过项目 4 次以上）。
本轮所有验证都规避了它（pytest 走 `pythonpath=["src"]`；我的分析脚本只做 AST 不 import；
需要 import 的命令一律显式 `PYTHONPATH=src`），详见 `KNOWN_BUGS.md` KB-8。

**子代理的判断（供参考，非结论）**：它认为 D-01、D-02 看起来是**代码瑕疵**（README 的描述更合理），
D-06、D-07 则是 README 自洽而代码两处漏做/写法分叉。**这些都需要人工确认后才应动手**。
