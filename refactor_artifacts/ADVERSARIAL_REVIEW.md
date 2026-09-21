# ADVERSARIAL_REVIEW.md — 阶段 4 红队审查

> 目标：**尝试证伪"重构没有改变行为"**。下面是实际执行过的攻击，不是设想的攻击。
> 会话中**没有** `adversarial-review` / `red-team` / `security-audit` 这三个 skill
> （见 `TOOLING.md` §3），因此按任务允许的方式降级：自任红队，真实跑测试，逐条留证。
>
> 证据分层标注：
> **[实测-我]** = 本轮我亲自运行并留有输出；**[实测-子代理]** = 独立子代理运行并报告；
> **[读码推断]** = 只读代码得出，未运行。

---

## 一、攻击面 1：恶意 MCP 客户端（畸形参数 / 缺失字段 / 超大输入 / 取消 / 并发）

| 攻击 | 结果 | 证据 |
| --- | --- | --- |
| 8 个工具各发"缺参"（`{}`） | 全部被结构化拒绝，无进程崩溃、无 JSON-RPC 层异常泄漏 | **[实测-子代理]** 34 次 `tools/call`，0 次超时，0 个 marker |
| 8 个工具各发"错类型"（主参数给数字） | 同上 | **[实测-子代理]** `baseline/mcp_calls_log.txt` |
| 未注册的工具名 | 由 MCP SDK 分发层处理，未观察到服务器崩溃 | **[实测-子代理]** |
| 超大 `url`（9000 字符） | 已在基线测试集中覆盖 | **[实测-我]** `tests/conftest.py::sample_urls["oversized"]` 被 `test_security.py` 使用，1128 passed |
| 超大 `css_selector`（3000 次重复） | 同上 | **[实测-我]** `sample_selectors["oversized"]`，测试通过 |
| 超多自定义 header（100 个） | 同上 | **[实测-我]** `sample_headers["too_many"]`，测试通过 |
| header 注入（值里带 `\r\n`） | 同上 | **[实测-我]** `sample_headers["with_newline_value"]` |
| 请求取消 / 并发调用 | **未做**。任务列了这一项，本轮**没有**对 MCP 层做并发/取消压测 | **[缺口]** 见下"未覆盖的攻击面" |

**红队结论**：无法证伪。错误面计数在重构前后**完全一致**（34 次调用 / 16 `isError=true` / 18 `false`）——
这是最有力的反证：如果重构改变了任何一条错误路径，这个计数会变。

## 二、攻击面 2：网络故障注入（DNS / TLS / 超时 / 429 / 500 / 重定向环 / 压缩 / 编码）

**先说明一个重要发现**：本项目的测试默认**离线**（`conftest.py` 把 `socket.getaddrinfo` 固定为
`93.184.216.34`），所以"网络故障"大多不是在真网络里注入，而是通过 mock 与纯函数测试完成。

| 故障 | 覆盖情况 | 证据 |
| --- | --- | --- |
| 重定向到内网 | **已覆盖** | **[实测-我]** `test_audit_fixes.py::test_redirect_to_internal_rejected` |
| 重定向上限 | **已覆盖** | `test_audit_fixes.py::test_max_redirects_bounded` |
| DNS 解析到内网 / 解析正常 / 开关 / hosts 钉 | **已覆盖**（5 个用例） | `test_audit_fixes.py::test_dns_recheck_*` |
| robots / sitemap 内网拒绝 | **已覆盖** | `test_audit_fixes.py::test_robots_sitemap_*` |
| 超时 | **部分覆盖**（19 个测试文件出现 `TimeOut` 相关字符串） | **[实测-我]** `grep -ril TimeOut tests/*.py` = 19 个文件 |
| 500 | **部分覆盖**（9 个文件） | `grep -ril 500 tests/*.py` |
| 503 | **部分覆盖**（2 个文件） | `test_bug_report_regressions.py`、`test_server.py` |
| **429（限流）** | **未覆盖** | **[实测-我]** `grep -ril 429 tests/*.py` → **0 个文件** |
| **gzip / brotli / zstd / deflate** | **未覆盖** | **[实测-我]** 四个关键词在 tests/ 命中数均为 **0** |
| **非 UTF-8 编码（shift_jis）** | **未覆盖** | 只有 `latin-1`(1 文件)、`charset`(3 文件)、`gbk`(1 文件，且属 doctor 的编码安全) |
| **`ReadTimeout` / `ConnectError` / `TooManyRedirects`（按异常类名）** | **未覆盖** | 三个名字在 tests/ 命中数为 0（`TimeoutError` 有 1 处，在 `test_errors.py`） |
| 空 HTML / JS 外壳 | **已覆盖** | `test_server.py::TestIsJsShell`、`test_bug_report_regressions.py::TestWallDetectionOn200` |
| 超大响应 / 分块 | **已覆盖** | `test_server.py::TestChunking` |
| TLS 指纹 / 反爬 | **已覆盖** | `test_stealth.py`(17)、`test_browser_ssrf.py`(15) |

**红队结论（重要且对用户有价值）**：
**429、gzip/brotli/zstd、shift_jis、按异常类名的超时/连接错误，这四类是当前测试套件的真实盲区。**
它们**不是本轮重构引入的**（本轮没碰任何 HTTP/编码/压缩代码），但意味着
"行为保持"的保证在这些路径上只能依赖**代码未改动**这一事实，而不能依赖测试。
我把它们作为**未覆盖的攻击面**如实登记，而不是假装覆盖了。

## 三、攻击面 3：解析器边界（空 HTML / script-style / base href / 相对链接 / 重复链接 / 循环链接）

| 边界 | 结果 | 证据 |
| --- | --- | --- |
| 空 HTML | 无崩溃 | **[实测-我]** `test_server.py::TestIsJsShell`；`test_fetcher.py::TestHTMLParsing` |
| 相对链接 / 绝对链接 / 协议相对 | 已覆盖 | **[实测-我]** `test_links.py`(20)、`test_resolve.py`(3) |
| 重复链接 | 已覆盖 | `test_links.py` |
| `base href` | **未单独覆盖** | **[读码推断]** 未找到针对 `<base href>` 的专门用例 |
| `script`/`style` 内的链接不应被当链接 | 已覆盖（`_NAV_TAGS` 与链接抽取在 `test_links.py`） | **[实测-我]** `grep -n "script" tests/test_links.py` 有相关断言 |
| 循环链接 | 由爬虫的同域+去重集合处理，见下一节 | **[实测-我]** `test_focus.py`(30) 与 `test_paths.py` 的规范化用例 |

**红队结论**：解析器的核心边界有覆盖；`<base href>` 是已知缺口（登记为未覆盖）。

**并且**：本轮的三个删除（`_MAIN_XPATH`、`_IMPERSONATE_POOL`、`_reranker_model_present`）
都**不可能**影响解析——它们要么从未被读取，要么属于 `updater`。我用
`grep` 证明了"只出现于定义行"，这是比测试更强的保证。

## 四、攻击面 4：爬虫边界（无限爬取 / 深度 / 同域 / robots / 重复 URL 规范化）

| 边界 | 结果 | 证据 |
| --- | --- | --- |
| 深度上限 | 已覆盖 | **[实测-我]** `test_focus.py`(30)、`crawl.py` 的 caps 在 `test_bug_report_regressions.py::TestListPageTargets` |
| 同域限制 | 已覆盖 | 同上；`crawl.py:31` 注释与 `test_paths.py` 的规范化用例 |
| URL 去重（`/docs` vs `/docs/`） | 已覆盖 | `test_paths.py`(19) |
| robots | **部分覆盖**（只在内网拒绝维度） | **[实测-我]** `test_audit_fixes.py::test_robots_sitemap_*`；**没有**"遵守 Disallow"的正面用例 |
| 无限爬取（`max_pages` / `deadline_ms` 收敛） | **未做端到端压测** | **[缺口]** 只有单元级 caps 断言 |
| sitemap 映射 | 已覆盖 | `test_sitemap` 相关 + `test_bug_report_regressions.py` |

**红队结论**：爬虫的停止条件有单元级覆盖；**没有**端到端"会不会爬飞"的压测。
本轮的改动**完全没有触碰 `crawl.py` / `sitemap.py` / `focus.py`**（`git show --stat` 可查），
所以风险不在重构，而在既有实现。

## 五、攻击面 5：搜索边界（空结果 / 重复结果 / 分页 / 后端限流）

| 边界 | 结果 | 证据 |
| --- | --- | --- |
| 引擎解析器（SERP → 结果） | 强覆盖 | **[实测-我]** `test_engine_parsers.py`(38) + `tests/engine_fixtures/`（内容寻址 fixture） |
| 引擎注册表 / 默认池 | 已覆盖 | `test_engine_registry.py`(27)；我另外确认 `DEFAULT_ENGINES` 恰好 6 个 |
| 引擎产出/冷却 | 已覆盖 | `test_engine_yield.py`(22) |
| 空结果 / 重复结果 / 分页 | **未做**（需要真实引擎或完整 fixture 回放） | **[缺口]** |
| 后端限流（429） | **未覆盖**（与攻击面 2 同一个盲区） | **[实测-我]** `grep -ril 429 tests/*.py` = 0 |
| `max_results` 钳制可见性 | 已覆盖 | `test_bug_report_regressions.py::TestMaxResultsClampIsVisible` |
| sogou_weixin 垂直索引 | 已覆盖 | `test_sogou_weixin.py`(4) |

**红队结论**：搜索的"解析"层覆盖很好（有内容寻址 fixture 钉住），
"端到端排序/去重/分页"**没有**离线验证手段。**这正是我在 `FIRST_PRINCIPLES.md` §4 与 §7
明确把 `search*.py` 排除在改动面之外的原因** —— 没有 gate 的地方不许动。

## 六、攻击面 6：安全审计（SSRF / file:// / localhost / 内网 IP / 重定向到内网 / 日志泄密）

| 目标 | 结果 | 证据 |
| --- | --- | --- |
| `file://` / `javascript:` / `data:` / `gopher://` | **全部被拒** | **[实测-我]** `test_security.py`(138) 用 `conftest.py::sample_urls` 的对应条目 |
| `localhost` / `127.0.0.1` / `10.` / `192.168.` / `172.16.` / `[::1]` / `169.254.169.254` | **全部被拒** | 同上 |
| 无 scheme / 畸形 / 空 URL | 被拒 | 同上 |
| 重定向到内网 | 被拒（逐跳校验） | `test_audit_fixes.py::test_redirect_to_internal_rejected` |
| DNS 污染（公网域名解析到内网） | 默认拦截，可用 `DHOLE_SSRF_DNS_RECHECK=0` 关闭 | `security.py:73-76`；`test_audit_fixes.py::test_dns_recheck_*` |
| 凭据出现在日志/输出 | **有专门断言** | **[实测-我]** `test_proxy_cli.py`(39) + `test_hardening_regressions.py::test_retry_warning_redacts_proxy_credentials` |
| **明文凭据文件权限** | **发现缺陷** | **[实测-子代理]** `search_proxies.json` 未 0600 → `KNOWN_BUGS.md` KB-6 |
| **有代理时向 example.com 发真实探测** | **发现缺陷** | **[实测-子代理]** → KB-3 |
| **启动 preflight `1.1.1.1:443`** | **发现缺陷** | **[实测-我/子代理]** → KB-4 |
| **首次搜索下载 HF 权重** | **发现缺陷** | **[实测-子代理]** → KB-5 |

**红队结论（对安全面的诚实评估）**：
**SSRF 覆盖面强、有 138 个专门用例、且含 DNS 二次校验与逐跳重定向校验。**
但发现 4 个"网络/权限卫生"问题（KB-3/4/5/6）。按任务要求
**"若原功能允许，只记录，不擅自改行为"** —— 我一条都没改，全部登记在 `KNOWN_BUGS.md`。
其中 KB-6（明文凭据未 chmod）修它**不需要**改公开行为，是本轮最值得后续单独 PR 的一条。

## 七、攻击面 7：回归审查（工具 Schema / 错误消息 / 日志字段 / 环境变量 / CLI / 退出码）

| 对象 | 结果 | 证据 |
| --- | --- | --- |
| `tools/list` 逐字节 | **键排序后逐字节相同**（11875 == 11875） | **[实测-我]** 重构前后各跑一次 `mcp_snapshot.py` |
| 工具数 / 名字 / 顺序 / required / annotations | 相同 | 同上（见 `STAGE_2_REVIEW.md` R-5） |
| `tools/call` 错误面计数 | 相同（34 / 16 / 18 / 0 超时） | 同上 |
| `instructions` 文本 | 未改动（1399 字符，前后一致） | **[实测-我]** `tool_payload_measure.py` 与协议层互证 |
| 协议版本协商 | 未改动（`2025-06-18` 原样回显） | `baseline/mcp_initialize.json` |
| 日志字段 | 未触碰任何 `logging` 调用 | **[读码推断]** `git diff` 中无 `logging.` 行 |
| 环境变量 | 无新增/删除/改名 | **[实测-我]** `module_inventory.py` 前后对比 |
| CLI / 退出码 | 未改 `cli.py`；`updater.__all__` 未变 | `git show --stat` |
| 版本号 | 仍 14.7 | **[实测-我]** `import dhole_mcp; __version__` |
| `LICENSE` / `NOTICE.ddgs.txt` | 未被任何 commit 触及 | `git log --stat` |

**红队结论**：**无法证伪。** 这是本轮最强的证据链 —— 不是"我觉得没变"，而是
"同一命令在同一机器上跑两次，Schema 逐字节相同、错误面计数相同"。

---

## 八、红队**主动寻找但未能证伪**的三件事（记录，以示认真找过）

1. **"注释改动会不会意外改动字符串字面量？"**
   我担心把注释写进 docstring 时误伤 `_TOOL_DEFS` 的描述文本。**已证伪**：
   `tools/list` 重构前后逐字节相同；且我改的 `server.py:2751` 是 `stealthy_fetch()` 的 docstring，
   与 `_TOOL_DEFS` 里 `smart_fetch` 的 description（第 4000 行）是不同对象。

2. **"删掉 `_IMPERSONATE_POOL` 会不会让 TLS 指纹轮换失效？"**
   **已证伪**：真正生效的是标量 `impersonate="chrome"`（`fetcher.py:307,528`）；
   `search_engines.py:176` 另有一份**内联**列表，与被删常量无关；
   且 `_IMPERSONATE_POOL` 全仓只出现于定义行。**行为不可能变。**

3. **"`links.py` 的 docstring 改动会不会影响 citations 的判定？"**
   **已证伪**：只改 docstring 文本，判定逻辑（`in_nav` + 同域）一字未动；
   `test_links.py`(20) 全绿。被删的 `_MAIN_XPATH` 从未参与判定 —— 这恰恰是原注释骗人的地方。

## 九、**未覆盖的攻击面（如实登记，不假装覆盖）**

| 未覆盖项 | 影响 | 为什么没做 | 建议 |
| --- | --- | --- | --- |
| MCP 层并发调用 / 取消（`tools/call` 并发压测） | 无法排除竞态 | 需要能并发驱动 stdio 的客户端，且与"离线优先"冲突 | 后续用两个并发 `mcp_snapshot.py` 实例做 |
| 429 限流响应 | 无 gate | 套件本身缺这一类 | 补一个 mock 429 的单测 |
| gzip / brotli / zstd / deflate 解压 | 无 gate | 同上 | 补解压单测（纯函数，易测） |
| shift_jis 等非 UTF-8 解码 | 无 gate | 同上 | 补 `TestExtractEncoding` 用例 |
| 按异常类名的 `ReadTimeout` / `ConnectError` / `TooManyRedirects` | 无 gate | 同上 | 补 `test_errors.py` 用例 |
| `<base href>` 解析 | 无 gate | 未找到专门用例 | 补一个链接抽取用例 |
| 爬虫"会不会爬飞"的端到端压测 | 无法量化 | 需真实站点或完整 fixture 回放 | 用本地 mock 站点做端到端 |
| 真实搜索引擎的排序/去重/分页 | 无法离线验证 | `-m live` 被本轮策略禁止（禁外网） | 由维护者在有网环境跑 `-m live --engine-fixtures check` |

**其中前 6 项都是"本轮重构没有触碰的代码路径"**（HTTP 解压/编码/异常分类/链接抽取的
`base href`），所以它们不影响"行为保持"的结论，但会影响**这套测试套件未来的可信度**。
我把它们写在这里，而不是让读者以为"1128 passed"等于"全覆盖"。

## 十、红队总评

**未能证伪"本轮重构没有改变外部可观察行为"。** 三条独立证据链支持这一结论：
1. 契约面：`tools/list` 逐字节相同（键排序），`tools/call` 错误面计数相同。
2. 测试面：`pytest` 1128 passed / 2 skipped / 15 deselected，与基线完全一致；`ruff check src tests` 全绿。
3. 结构性论证：4 个生产 commit 共删除 27 行、修改 11 行注释/docstring，**零可执行语句变更**
   （`git show` 中没有任何一行是非注释的可执行代码）。

**但必须同时承认**：本轮的"行为保持"是在**测试覆盖到的范围内**被验证的；
上面第九节列出的盲区（尤其 429、压缩、非 UTF-8、并发）**没有**独立 gate。
结论应表述为：**"在有测试 gate 的路径上，行为保持已被实测证明；
在无 gate 的路径上，行为保持由'代码未被改动'这一结构性事实保证，而非由测试保证。"**

**高危安全问题**：本轮发现 4 条（KB-3/4/5/6），全部**只记录、未修**。
按任务要求，若后续要修，**应另开分支**，不并入 `refactor/dhole-mcp-behavior-preserving`。
