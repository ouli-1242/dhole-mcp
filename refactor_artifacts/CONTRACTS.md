# CONTRACTS.md — Dhole MCP 外部契约清单

> 阶段 1 产出。本文件是本轮重构的**验收基准**：任何一步改动，若触及下表任一条目，
> 即视为"改变外部可观察行为"，必须回滚。
>
> 契约来源优先级（严格按任务要求）：① 代码 `src/dhole_mcp/` ② 实测 MCP 响应
> ③ README 中被前两者证实的描述 ④ 其余文档仅作背景。
> **本文件的每一条都标注了来源；来自 README 而未获代码证实的条目一律进 `DOC_CODE_DRIFT.md`，不列入此表。**

---

## C1. MCP 协议面

| 项 | 值 | 来源 |
| --- | --- | --- |
| 传输 | stdio（默认）；亦支持 streamable HTTP | `server.py:4114 serve()` |
| HTTP 端点 | `http://{host}:{port}/mcp`，host 默认 `127.0.0.1`，port 默认 `8765` | `server.py:4114` |
| SSE 传输 | **已移除**（代码注释明示 spec 已废弃） | `server.py:4122` docstring |
| 实现层 | 低层 `mcp.server.Server`（非 FastMCP，为省 token） | `server.py:4123` |
| 版本来源 | `src/dhole_mcp/__init__.py::__version__` = **14.7** | 实测 `python -c "import dhole_mcp;print(dhole_mcp.__version__)"` |
| `instructions` | 由 `DHOLE_INSTRUCTIONS` 常量提供（`server.py:254`），可被同名环境变量覆盖 | `server.py:254, 4150` |

---

## C2. `tools/list` 契约（8 个工具）

> 逐字段来源：`src/dhole_mcp/server.py:3997-4112` 的 `_TOOL_DEFS`。
> **数组内的元素顺序**（8 个工具的先后）与源码一致（`[Tool(**td) for td in self._TOOL_DEFS]`，`server.py:4127`）。
> **注意**：`Tool(**td)` 会被 MCP SDK 重新序列化，因此**每个工具对象内部的键顺序**
> 实测为归一化后的 `annotations, description, inputSchema, name`，**不是**源码里的书写顺序。
> 键顺序在 JSON 里无语义；这条只影响"能否逐字节比对"，见 `DOC_CODE_DRIFT.md` D-6。

### C2.0 全局

| 项 | 值 |
| --- | --- |
| 工具数 | **8**（实测 `len(_TOOL_DEFS)`，顺序：`smart_fetch`, `smart_crawl`, `screenshot`, `smart_search`, `cache_clear`, `parse`, `feed_fetch`, `resolve_url`） |
| Schema 生成方式 | 手写 `inputSchema` 字典，**无** Pydantic 自动生成（省约 69% token，见 `server.py:3995` 注释） |
| 所有工具共有 | 顶层键 `name` / `description` / `inputSchema` / `annotations` |

### C2.1 `smart_fetch`

- `required`: **无**（`inputSchema` 不含 `required` 键）
- properties: `url`(string), `urls`(array[string]), `extraction_type`(string, enum `markdown|html|text|article|structured`), `css_selector`(string), `max_content_chars`(integer), `timeout`(integer), `cache_ttl`(integer), `force_fetcher`(string, enum `http|stealthy`), `offset`(integer), `pages`(string), `password`(string), `focus`(string), `actions`(array[object]), `schema`(object), `options`(object)
- annotations: `readOnlyHint=True, idempotentHint=True, openWorldHint=True`
- **默认值写在 description 文本里，不在 schema 里**：`max_content_chars` default 40000 / min 500；`timeout` default 30000 ms；`cache_ttl` default 3600。

### C2.2 `smart_crawl`

- `required`: `["url"]`
- properties: `url`(string), `discover_only`(boolean), `focus`(string), `crawl_urls`(array[string]), `search`(string), `options`(object)
- annotations: 同 C2.1
- description 内承诺的 `options` 默认：`max_pages` 10（1-100）、`max_depth` 2（0-5）、`max_content_chars_per` 8000、`concurrency` 3（1-5）、`cache_ttl` 3600、`timeout` 30000 ms、`deadline_ms` 120000。

### C2.3 `screenshot`

- `required`: `["url"]`
- properties: `url`(string), `session_id`(string), `options`(object)
- annotations: 同 C2.1
- 返回 `ImageContent`（多模态）；description 明示"text agents use smart_fetch"。

### C2.4 `smart_search`

- `required`: `["query"]`
- properties: `query`(string), `options`(object)
- annotations: 同 C2.1
- description 内承诺：默认引擎池 `bing,duckduckgo,brave,yahoo,yandex,sogou_weixin`；可选 `wikipedia`/`grokipedia`；`options` 含 `max_results`(1-50,6)、`cache_ttl`(300)、`mode`(auto|neural|find_similar)、`engines`(最多 9)、`site`、`exclude_sites`、`location`、`language`(2 字母)、`region`、`page`(0-10)、`freshness`(day|week|month|year)、`url`、`fetch_content`(bool,false)。
- 响应字段承诺：`relevance_score`、`fetch_relevance`(high/med/low)、`engines_consensus`、`consensus_basis`(full|single_family|partial_pool|degraded_pool)、`related_queries`；sogou_weixin 返回 weixin.sogou.com 包装链接而非 canonical URL。

### C2.5 `cache_clear`

- `required`: **无**
- properties: `all`(boolean), `engine_state`(boolean)
- annotations: **`readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False`**（全 8 个工具里唯一非只读、唯一 `openWorldHint=False`）
- description 内承诺：默认只清过期项，`all=true` 清全部；`engine_state=true` 额外重置引擎冷却与产出历史，回复中报告 `engine_health`；默认 TTL 1h。

### C2.6 `parse`

- `required`: `["file_path"]`
- properties: `file_path`(string)
- annotations: `readOnlyHint=True, idempotentHint=True, openWorldHint=False`
- description 内承诺：支持 `.html/.htm/.docx/.xlsx/.csv/.pdf`；相对路径依次对 cwd、`$DHOLE_WORKDIR`、home 试探，失败时列出尝试过的路径。

### C2.7 `feed_fetch`

- `required`: `["urls"]`
- properties: `urls`(array[string]), `max_items`(integer), `timeout`(integer)
- annotations: 同 C2.1
- description 内承诺：`max_items` default 20（`0` = 全部）；`timeout` default 20 **秒**；feed 间相互独立，单个 feed 失败不影响整批。

### C2.8 `resolve_url`

- `required`: `["url"]`
- properties: `url`(string), `timeout`(integer)
- annotations: 同 C2.1
- description 内承诺：跟随重定向但**不下载正文**；返回 `final_url` + `status` + `content_type`；`timeout` default 15 **秒**。

> **注意单位不一致是契约的一部分**：`smart_fetch.timeout` / `smart_crawl.options.timeout` /
> `screenshot.options.timeout` 是**毫秒**（30000），而 `feed_fetch.timeout`（20）与
> `resolve_url.timeout`（15）是**秒**。重构中任何"统一单位"的整理都是行为变更。

---

## C3. `tools/call` 契约

| 项 | 契约 | 来源 |
| --- | --- | --- |
| 成功返回 | `content` 为 `TextContent` 列表；`screenshot` 为 `ImageContent` | `server.py:4124` 导入的 `TextContent` / `ImageContent` |
| 未知工具名 | 由 MCP SDK 分发层处理；未观察到服务器崩溃 | 实测 34 次 `tools/call` |
| 参数校验失败 | **结构化拒绝**（不是抛异常） | `tests/test_schema_param.py`(15) + `test_bug_report_regressions.py::TestStructuredInputErrors` |
| **`isError` 语义按工具分裂** | `isError=false` 承载校验/SSRF 类错误：`smart_fetch`、`smart_crawl`、`smart_search`、`parse`、`resolve_url`；`isError=true` 承载同类错误：`screenshot`、`feed_fetch` | **实测**：34 次调用中 16 条 `isError=true`、18 条 `false`；原始响应见 `baseline/mcp_tools_call_index.json` 与 `baseline/mcp_calls_log.txt` |

> **D-3 的补充**：上表最后一行是**重构前后都成立**的既有行为，且**不是**缺陷判定的对象。
> 它在 `DOC_CODE_DRIFT.md` D-4 中作为"README 未描述的事实"登记。
> **本轮明令禁止"顺手统一"它** —— 统一会改 `isError`，是外部可观察行为变更。

---

## C4. 环境变量契约

`src/` 实测读取 24 个变量（AST 扫描 + grep 交叉验证）。`DHOLE_*` 前缀为产品配置面：

| 变量 | 读取位置 | 备注 |
| --- | --- | --- |
| `DHOLE_HOME` | `paths.py` | 重定向状态目录（默认 `~/.dhole`） |
| `DHOLE_WORKDIR` | `server.py` | `parse` 相对路径试探目录之一 |
| `DHOLE_USAGE_LOG` | `server.py` | 使用日志路径 |
| `DHOLE_NO_AUTO_REPAIR` | `cli.py` | 关闭自动修复 |
| `DHOLE_UPDATE_INDEX_URL` | `cli.py`, `updater.py` | 更新索引地址 |
| `DHOLE_UPDATE_PACKAGE` | `cli.py`, `updater.py` | 更新包名 |
| `DHOLE_DEFAULT_ENGINES` | `search_metasearch.py`, `updater.py` | 覆盖默认引擎池 |
| `DHOLE_SEARCH_DEADLINE` | `search_metasearch.py` | 搜索截止时间 |
| `DHOLE_SEARCH_FEEDBACK` | `search.py` | 搜索反馈文件 |
| `DHOLE_SEARCH_PROXY` | `search_proxy.py`, `server.py` | 代理配置 |
| `DHOLE_SSRF_DNS_RECHECK` | `security.py` | **默认开启**；`=0` 关闭 DNS 二次校验（`security.py:73-76`） |
| `DHOLE_HF_ENDPOINT` | `reranker.py` | HuggingFace 镜像端点 |
| `DHOLE_BRIGHTDATA_COUNTRY` / `_ZONE` | `search_metasearch.py` | 有密钥后端 |
| `DHOLE_BROWSER_IDLE_TIMEOUT` | （grep 命中，模块未在 AST 字面量扫描中） | 浏览器空闲超时 |
| `DHOLE_INSTRUCTIONS` | （grep 命中） | 覆盖 `instructions` 文本 |
| `DHOLE_TO_BACKEND` | `search_metasearch.py`（`_DHOLE_TO_BACKEND`） | 后端映射 |
| `DHOLE_BOCHA_API_KEY` / `_BRIGHTDATA_API_KEY` / `_EXA_API_KEY` / `_TAVILY_API_KEY` | 各后端 | 动态拼名读取（`f"DHOLE_{name}_API_KEY"`） |

非 `DHOLE_` 但被读取的：`HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`（`search_proxy.py`, `server.py`）、`NO_COLOR` / `FORCE_COLOR`（`cli_ui.py`）、`HF_ENDPOINT`（`reranker.py`）、`LOCALAPPDATA` / `PROGRAMFILES` / `PROGRAMFILES(X86)`（`browser.py`）、`SystemRoot`（`security.py`）。

---

## C5. CLI 契约

入口：`[project.scripts] dhole = "dhole_mcp.cli:main"`（`pyproject.toml:90`）。子命令实测存在于 `cli.py`：
`dhole`（启动 MCP）、`dhole -v`、`dhole --doctor`、`dhole proxy`、`dhole engines`（`list|reset`）、`dhole model`。
> README 对这些命令的输出字段有详细承诺（含 `engine cooldowns` 行）；逐条核对结果见 `DOC_CODE_DRIFT.md`。
> **本表只登记"命令存在"这一已核实事实，输出字段以实测为准。**

---

## C6. 状态文件契约

`~/.dhole/`（`DHOLE_HOME` 可重定向）下，全部**惰性写入**：

| 文件 | 写入方 | 触发时机 |
| --- | --- | --- |
| `engine_stats.json` | `search_metasearch.py` (`_engine_stats_file`) | 引擎产出统计落盘 |
| `circuit_breaker.json` | `search_metasearch.py` (`_circuit_state_file`) | 引擎连接冷却 |
| `search_feedback.json` | `search.py` (`_feedback_file`) | 搜索反馈 |
| `search_proxies.json` | `search_proxy.py` (`_config_path`) | `dhole proxy add/remove/clear` |
| 使用日志 | `server.py` (`DHOLE_USAGE_LOG`) | 工具调用记录 |

---

## C7. 测试守卫契约（不得削弱）

来源：`tests/conftest.py`（实测存在）

| fixture | 作用域 | 作用 |
| --- | --- | --- |
| `_no_real_home_migration` | autouse | 置 `paths._legacy_migrate_done = True`，禁止真实 home 迁移 |
| `_no_real_home_state_writes` | autouse | 把 4 个状态文件重定向到 `tmp_path`；`real_state_paths` 标记的用例可退出 |
| `_offline_dns` | autouse | 固定 `socket.getaddrinfo` → `93.184.216.34`；`live` 标记的用例例外 |

`pyproject.toml` 的 pytest 配置：`testpaths=["tests"]`、`pythonpath=["src"]`、
`addopts=["-m","not e2e and not live"]`，markers: `e2e` / `real_state_paths` / `live`。

---

## C8. 本轮的改动允许面（与 FIRST_PRINCIPLES.md §7 一致）

只允许：**S-4 死代码删除**、**S-6 说谎的类型标注修正**、**S-7 误导性注释重写**、
**S-5 同模块内逐字节相同的重复字面量收敛**。
禁止：一切触及 C1–C7 的改动；格式化；依赖变更；拆分 God Module；打断导入环；改惰性导入。
