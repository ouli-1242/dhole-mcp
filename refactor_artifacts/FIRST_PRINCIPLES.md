# FIRST_PRINCIPLES.md — Dhole MCP 服务器第一性原理建模

> 阶段 1 产出。目的不是复述代码，而是把"什么必须不变 / 什么可以安全替换"这条分界线
> 用第一性原理推导出来，作为阶段 2 每一步改动的判据。
>
> 所有事实均有命令或代码位置支撑；推测项显式标注为"推测"。
> 数据来源：`refactor_artifacts/analysis/module_inventory.txt`（由
> `refactor_artifacts/tools/module_inventory.py` 经 AST 静态分析生成，命令见文末）。

---

## 1. MCP 服务器的本质

MCP（Model Context Protocol）服务器不是 Web 服务，它是**一个由客户端拉起的子进程，
通过 stdin/stdout 交换 JSON-RPC 2.0 报文**。这决定了几件事：

1. **stdout 是协议信道，不是日志信道。** 任何向 stdout 的 `print()` 都会污染 JSON-RPC 流，
   使客户端解析失败。因此"日志级别/日志去处"是外部可观察行为的一部分，
   而**日志文件的内部格式**不是。
2. **协议面只有四个可观察动词**：`initialize`（含 `instructions` 与协议版本协商）、
   `tools/list`（工具发现）、`tools/call`（调用分发）、以及协议层错误。
   任何重构只要这四个动词的**输入→输出映射**不变，对客户端就是不可见的。
3. **错误封装是契约的一部分。** 工具执行失败有两种表达：JSON-RPC 层 `error`，
   或 `tools/call` 结果里的 `isError: true`。哪个用在哪，客户端可见，因此必须保持不变。
4. **生命周期由客户端决定。** 服务器不能假设自己有"退出时机"可以刷盘；惰性写入的
   状态文件（见 §5）必须容忍进程被随时杀掉。

**推论**：本轮的"行为保持"边界 = {4 个动词的输入输出映射} ∪ {工具元数据} ∪
{env/CLI/状态文件位置}。**内部的函数名、模块划分、局部变量、私有数据结构的形状，
全都不是契约**，可以自由重构。

---

## 2. 网页抓取（fetch）的本质

本质是一条无损管道：

```
URL 字符串
  → 校验（scheme 白名单 / SSRF / 长度）
  → DNS（可选二次校验，防 DNS 污染）
  → HTTP/TLS（TLS 指纹 impersonation，浏览器 UA）
  → 重定向（逐跳重新校验，防"重定向到内网"绕过）
  → 解压 + 编码探测（gzip/br/zstd；非 UTF-8 需正确解码）
  → 字节流 → HTML
  → 正文提取（trafilatura 优先，失败回退 markdownify/自定义）
  → 结构化输出（Markdown 正文 + 元数据 + 链接）
```

**这个管道里对客户端可观察的**：正文文本、Markdown 结构、元数据字段、链接列表、
截断行为与截断标记、错误语义（超时 vs 4xx vs 5xx vs 被 SSRF 拒绝）。
**不可观察的**：解析器内部用哪个 CSS 选择器、临时变量名、是否多一次函数调用。

**反直觉但重要**：`trafilatura` 与 `markdownify` 的输出差异是**可观察**的。
因此"换一个正文提取器"不是重构，是行为变更 —— 本轮**禁止**。

---

## 3. 整站爬取（crawl）的本质

本质是**在受限图上做受控的广度优先遍历**：

| 机制 | 作用 | 是否可观察 |
| --- | --- | --- |
| 队列（FIFO） | 决定访问顺序 | 间接可观察（结果顺序） |
| URL 规范化 + 去重集合 | 防止重复抓取 | 可观察（`pages` 数量与去重结果） |
| 深度上限 | 停止条件 | 可观察（`depth` 参数语义） |
| 同域限制 | 停止条件 | 可观察（`same_domain` 语义） |
| 并发度 | 性能与顺序 | 可观察（顺序抖动、超时预算） |
| 全局截止时间 | 停止条件 | 可观察（`deadline`/`max_pages` 命中后的截断标记） |
| 错误收集 | 部分失败不致命 | 可观察（`errors` 字段） |

**关键不变量**：停止条件必须**确定性地产出相同集合**（在无网络抖动前提下）。
任何重构若把"先入先出"改成"后入先出"，或在去重前做额外过滤，都会改变返回的页面集合
—— 这属于行为变更。

---

## 4. 无密钥搜索（search）的本质

```
query
  → 意图扩展（关键词扩展表 / 同义词 / 停用词裁剪）
  → 并发查询 N 个后端（6 个免密引擎，见 CONTRACTS.md）
  → 每个后端：HTTP → SERP HTML → 引擎专属解析器 → 原始结果
  → 归一化（title/url/snippet/时间）
  → 跨引擎合并
  → 去重（URL 规范化后）
  → 打分/排序（引擎产出统计 + 排序器 reranker）
  → 分页切片
```

**可观察**：结果的**顺序与集合**、每条的字段、总数、分页边界语义、`fetch_hint` 文本、
引擎失败时的降级行为（部分引擎失败仍要返回其余结果）。
**不可观察**：用 `asyncio.gather` 还是 `TaskGroup`、中间容器的类型。

**搜索是本项目里最容易"重构出行为差异"的地方**：排序依赖多重 tie-break，
去重依赖 URL 规范化细节。因此阶段 2 对 `search*.py` 只做**注释/死代码/类型标注**级别
的改动，不触碰任何排序、去重、并发、超时、阈值逻辑。

---

## 5. 数据流、共享状态、配置入口

### 5.1 模块依赖图（AST 实测，`module_inventory.py`）

```
                          ┌──────────────┐
                          │  __main__    │
                          └──────┬───────┘
                                 ▼
   ┌────────────────────────► server.py ◄───────────────┐  (导入 21 个模块)
   │                             │                        │
   │   ┌─────────────────────────┼──────────────────┐     │
   │   ▼                         ▼                  ▼     │
   │ crawl.py ──► search_proxy   search.py      browser.py│
   │   │            │              │  │             │     │
   │   │            ▼              │  │             ▼     │
   │   │         fetcher.py ◄──────┘  │         fetcher    │
   │   │            │                 ▼             │     │
   │   │            ▼            reranker.py        │     │
   │   │        security.py ◄──┐     │              │     │
   │   │         (fan-in 8)    │     ▼              │     │
   │   ▼                       │  reranker_config   │     │
   │ sitemap / focus /         │                     │     │
   │ trafilatura_extractor /   │  search_engines.py ─┘     │
   │ pdf_extractor ◄─ ocr      │      │                    │
   │                           │      ▼                    │
   │                       search_metasearch.py ◄──────────┘
   │                          (fan-in 3, 含 6 引擎常量)
   └── cli.py ──► server.py（CLI 复用同一 server 实例）
```

**AST 实测的高风险节点（按 fan-in）**：
| 模块 | 被导入次数 | 风险 |
| --- | --- | --- |
| `security.py` | 8 | **最高**：SSRF/URL 校验被 8 个模块依赖，改动会横向扩散到 fetch/crawl/sitemap/actions/browser/search/search_metasearch/search_proxy |
| `fetcher.py` | 5 | 高：HTTP 语义（UA/超时/重定向/impersonation）的单一出口 |
| `server.py` | 4 | 高：工具 Schema 与分发；`crawl.py` 反向导入它（存在环） |
| `trafilatura_extractor.py` | 4 | 中：正文提取，输出直接进 `content` |
| `errors.py` / `reranker.py` / `pdf_extractor.py` / `search_metasearch.py` | 3 | 中 |

**已识别的结构性问题（仅记录，本轮到阶段 2 第 8 步再评估）**：
- `server.py` 4,587 行 + 导入 21 个模块，是典型的 God Module。
- `crawl.py` → `server.py` 与 `server.py` → `crawl.py` 构成**导入环**，靠函数内延迟导入
  （`from dhole_mcp.crawl import CrawlResponseModel` 出现在函数体里）维持可导入性。
  **这是设计约束而非 bug**：拆掉环等于改导入时序，属于行为风险，本轮不动。

### 5.2 模块级可变状态（共享状态清单，AST 实测）

以下变量在**模块加载时**创建、跨请求存活。它们是最容易"重构出竞态"的地方：

| 模块 | 可变全局 | 语义 |
| --- | --- | --- |
| `search_metasearch` | `_BACKEND_HEALTH`, `_CONN_FAIL_COUNTS`, `_ENGINE_YIELD`, `_DEFAULT_BACKENDS`, `KEYED_ENGINES`, `_TEXT_ENGINES`, `_DEFAULT_CIPHERS`, `_GITHUB_REPO_HOSTS`, `_SEARCH_TRACKING_PARAMS`, `_DHOLE_TO_BACKEND` | 引擎健康/冷却/产出统计（**跨请求累积**） |
| `security` | `_DNS_CHECK_CACHE`, `_PRIVATE_NETWORKS` | DNS 校验缓存（有 TTL 语义） |
| `server` | `_DOMAIN_LATENCY` | 域名延迟画像，影响超时决策 |
| `cache` | `_db_initialized` | 惰性初始化标志（**并发下有竞态风险**，见 KNOWN_BUGS） |
| `fetcher` | `_IMPERSONATE_POOL` | TLS 指纹轮换池（顺序影响行为） |
| `browser` | `_FINGERPRINT_PROFILES`, `DISABLED_RESOURCE_TYPES` | 浏览器指纹 |
| `search` | `_INTENT_EXPANSIONS`, `_INTENT_PATTERNS`, `_STOPWORDS` | 查询扩展表 |
| 其余 | `_HINTS`/`_PATTERNS`（errors）、`_NAV_TAGS`（links）、`_KEY_MAP`（metadata）、`_TRACKING_PARAMS`（crawl）、`MODELS`（reranker）、`SUPPORTED_EXTENSIONS`（parse）、`_FRESHNESS_TO_TIMELIMIT`/`_INDEX_FAMILY`（search_engines）、`_VALID_KEYS`（actions）、`_CF_CHALLENGE_SIGNALS`/`_GEO_REDIRECT_SIGNALS`/`_JS_SHELL_SIGNALS`/`_PARSE_CONTENT_TYPES`（server） | 纯查表常量，**实际只读** |

**判据**：上表中"纯查表常量"那批，虽然用 `dict`/`list` 字面量声明（因此被 AST 归为可变），
但代码从不修改它们 —— 它们是**偶然实现细节**，可以安全改成 `frozenset`/`MappingProxyType`
之外的形式，也可以保持原样。**第一性原理结论：不值得为它们改动任何一行。**

### 5.3 配置入口（唯二入口）

1. **环境变量**：`src/` 中实测读取 24 个变量。其中 `DHOLE_*` 前缀的是产品配置面；
   其余（`HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_COLOR`、`FORCE_COLOR`、
   `LOCALAPPDATA`、`PROGRAMFILES`、`SystemRoot`、`HF_ENDPOINT`）是平台/生态约定。
   分布：`server`(5) > `search_metasearch`(4)、`updater`(3)、`cli`(3)、`search_proxy`(4)、
   `security`(2)、`reranker`(2)、`paths`(1)、`search`(1)、`browser`(3)、`cli_ui`(2)。
2. **CLI 子命令**（`cli.py`，188 行）：`dhole`（启动 MCP）、`dhole proxy`、`dhole engines`、
   `dhole model`、`dhole --doctor`、`dhole -v`。CLI 是 `server.py` 的**第二个调用方**，
   与 MCP 客户端共享同一实现 —— 因此"只改 MCP 路径"的想法是错的：**改 server.py 会同时影响 CLI。**

### 5.4 状态文件（外部可观察的落盘面）

`~/.dhole/`（可用 `DHOLE_HOME` 重定向）下的惰性写入文件：
`engine_stats.json`、`circuit_breaker.json`、`search_feedback.json`、`search_proxies.json`，
以及 `DHOLE_USAGE_LOG` 指向的使用日志。**测试守卫把它们全部重定向到 `tmp_path`**
（`tests/conftest.py::_no_real_home_state_writes`），这是基线能离线且可重复的前提。

---

## 6. 必须保持的行为不变量（I-系列）

| 编号 | 不变量 | 依据 |
| --- | --- | --- |
| I-1 | 8 个工具名、顺序无关但**集合与描述文本**不变 | `server.py:3997 _TOOL_DEFS`；`tests/test_tool_descriptions.py` 断言 |
| I-2 | 每个工具的 `inputSchema`（参数名/类型/默认值/required/enum）不变 | 同上；wire 体积预算测试钉住 |
| I-3 | `initialize` 返回的 `instructions` 文本不变 | `server.py:254 DHOLE_INSTRUCTIONS` |
| I-4 | `tools/call` 的 `content` 类型（`TextContent`/`ImageContent`）与 `isError` 语义不变 | 客户端契约 |
| I-5 | 错误消息语义不变（超时/被拒/解析失败的可区分性） | 客户端依赖其做降级 |
| I-6 | URL 校验与 SSRF 覆盖面不变（含重定向逐跳校验、DNS 二次校验默认开启） | `security.py`，fan-in 8 |
| I-7 | HTTP 语义不变：UA、TLS impersonation、超时、重试、重定向上限 | `fetcher.py` |
| I-8 | 缓存键/命中/失效语义不变 | `cache.py` + `_TOOL_DEFS.cache_clear` |
| I-9 | 爬取的深度/同域/去重/并发/截止时间语义与产出集合不变 | `crawl.py` |
| I-10 | 搜索的排序/去重/分页/降级语义与产出顺序不变 | `search*.py` |
| I-11 | 环境变量名与默认值不变（含未文档化的） | §5.3 |
| I-12 | CLI 子命令、输出字段、退出码不变 | `cli.py` |
| I-13 | 状态文件路径与惰性写入时机不变 | §5.4 |
| I-14 | 测试守卫不被削弱（3 个 autouse fixture） | `tests/conftest.py` |
| I-15 | 版本号唯一来源仍是 `src/dhole_mcp/__init__.py::__version__` | `pyproject.toml` hatch dynamic |

## 7. 可以安全替换的偶然实现细节（S-系列）

| 编号 | 可实现细节 | 为什么安全 |
| --- | --- | --- |
| S-1 | 私有函数的名称、参数名、局部变量名 | 无外部引用（需 grep 证明无测试引用） |
| S-2 | 模块内部的语句顺序、空行、行内注释 | 不影响 AST 语义 |
| S-3 | 纯只读查表常量（§5.2 下半表）的容器类型 | 从不上写 |
| S-4 | 已证明无引用的私有函数/类/常量（死代码） | 需全仓 grep 为证 |
| S-5 | 明显的重复字面量抽成常量（**同一模块内、且值逐字节相同**） | 值不变则行为不变 |
| S-6 | 类型标注的补全/修正（仅当现标注**说谎**时） | 运行时不作校验，不改变行为 |
| S-7 | 误导性注释的重写为准确注释 | 注释不参与执行 |
| S-8 | 文档（README 等）与代码不一致的记录（**不是修正**） | 见 DOC_CODE_DRIFT.md |

**明确禁止（本轮不做）**：换正文提取器、改排序/去重/并发/超时/重试参数、拆分 God Module、
打断导入环、改格式化（仓库基线本就不是 ruff-format 过的，见 BASELINE.md）、
把惰性导入改成顶层导入（会改变冷启动与降级行为）。

---

## 8. 画给阶段 2 的落点

结合 §6/§7，阶段 2 的安全改动面**收敛为**：
`S-4 死代码` + `S-6 说谎的类型标注` + `S-7 误导性注释` + `S-5 同模块内重复字面量`。
这些都满足"可回滚、diff 小、不需要改测试、不需要碰 I-系列"。

**证据命令**（可复现本文件全部结论）：

```bash
cd /d/tools/dhole-mcp
python refactor_artifacts/tools/module_inventory.py > refactor_artifacts/analysis/module_inventory.txt
python -m pytest -q                 # 基线：1128 passed, 2 skipped, 15 deselected
python -m ruff check .              # 基线：All checks passed!
```

> 推测项声明：§5.1 的"导入环靠函数内延迟导入维持"是**基于 AST 中 `ImportFrom` 出现在函数体内**
> 的推断（`module_inventory.py` 会遍历全部节点，不区分层级）；未做运行时验证。
> 该推断只影响"是否值得拆 ring"的建议，不影响本轮改动。
