<div align="center">

<img src="https://raw.githubusercontent.com/ouli-1242/dhole-mcp/main/assets/cover.png" alt="Dhole —— 让 AI 真的上网" width="100%">

<p><strong>给 AI 一副能真正上网的手。</strong></p>

<p>抓取 · 搜索 · 爬站 · 读 PDF · 页面操作 · 变更监控<br>
13 个工具，一个本地进程 · 零密钥 · 零模型 · 零遥测</p>

<p>
<a href="https://pypi.org/project/dhole-mcp/"><img src="https://img.shields.io/pypi/v/dhole-mcp" alt="PyPI"></a>
<a href="https://pepy.tech/projects/dhole-mcp"><img src="https://static.pepy.tech/personalized-badge/dhole-mcp?period=total&amp;units=INTERNATIONAL_SYSTEM&amp;left_color=BLACK&amp;right_color=GREEN&amp;left_text=downloads" alt="PyPI Downloads"></a>
<img src="https://img.shields.io/pypi/pyversions/dhole-mcp" alt="Python 3.11+">
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT"></a>
</p>

<p>English: <a href="README.en.md">README.en.md</a></p>

</div>

## 为什么要有它

模型会思考，可它看不见今天的网。

训练数据有截止日期。搜索 API 要密钥、要额度。抓一页，可能只回来一串 403。
Dhole 把这条断掉的路接回来：一个 [MCP](https://modelcontextprotocol.io) 服务器，装进任何宿主，AI 自己挑工具，你不用记命令。

## 安装

```bash
pip install dhole-mcp  # 完整版：浏览器 / PDF / OCR / 解析器 / 重排，一次装齐
dhole doctor           # 体检：起服务、起浏览器、真搜一次、真抓一次
```

Python 3.11+。只剩一样东西 pip 装不到这一步：Chromium 本体（另一份约 193MB 的下载，不在 wheel 里）。

- 什么都不用做：第一次真正需要渲染时，dhole 自己会装它要用的那一份。
- 想提前装好、或上一次没装完，补一句 `python -m patchright install chromium` 就行——可重复执行，只补缺的那一份。
- 装不上也不影响其余能力：缺浏览器时自动降级为纯 HTTP，降级不是故障。`dhole doctor` 会点名缺的是哪一件，并给出可复制的修复命令。

接进宿主（Claude Desktop / Cursor / Codex / Cherry Studio 字段名各异，认 `command`）：

```json
{"mcpServers": {"dhole": {"command": "dhole", "args": []}}}
```

没有参数，没有密钥，没有环境变量。重启宿主，然后正常提要求。

### 只用命令行，不接宿主

CLI 与 MCP 服务器在同一个包里：上面那句 `pip install dhole-mcp` 已经把 `dhole` 命令装好，**不需要**写 `mcpServers` 配置，也不用启动任何宿主：

```bash
dhole read https://example.com
dhole search "python asyncio"
```

想让它独立成一个全局工具、不混进项目环境，用 pipx 或 uv 装同一个包（入口仍然是 `dhole`）：

```bash
pipx install dhole-mcp        # 或：uv tool install dhole-mcp
dhole doctor
```

每条 CLI 命令都是独立进程：不常驻、不开端口、没有宿主也能用；浏览器 / PDF / OCR 的按需安装与降级说明与上面相同。升级用 `pip install -U dhole-mcp`（pipx 装的就 `pipx upgrade dhole-mcp`，uv 装的就 `uv tool upgrade dhole-mcp`）。

<details>
<summary>也可以把安装交给 agent 自己做</summary>

```
在这台机器上安装 Dhole MCP 服务器，逐步执行，失败就停下来报告。

1. 认清你所在的宿主，读出它的 MCP 配置位置与添加本地服务器的格式（读文档，不要猜）。
2. pip install dhole-mcp（已经是完整版）。
3. 备份配置文件，添加名为 "dhole" 的服务器：command 为 "dhole"，无参数、无密钥、无 env。
4. 让用户重启宿主，再跑 dhole doctor 确认体检通过；若报浏览器缺失，补一次 python -m patchright install chromium（可重复执行）。
```

</details>

常驻或远程使用，改走 streamable HTTP：

```bash
export DHOLE_HTTP_TOKEN="$(openssl rand -hex 32)"   # 设了即强制 Bearer 校验
dhole --http                                        # 127.0.0.1:8765/mcp
```

远程暴露前必须套 TLS 反代。dhole 没有内置 OAuth，令牌只是端口门禁，不做主体隔离——要按人隔离就一人一进程。

## 宣传片

从模型看不见今天的网，到它自己抓回证据。

<video controls width="100%" poster="https://raw.githubusercontent.com/ouli-1242/dhole-mcp/main/assets/consent.png" src="https://github.com/user-attachments/assets/b59a7b13-fab2-41df-a029-cbc41d8a378e"></video>

播放器起不来（登录态偶发被浏览器 CSP 拦）？[点此直接看同一个文件](https://github.com/user-attachments/assets/b59a7b13-fab2-41df-a029-cbc41d8a378e)。（720p，86 秒，带旁白）

## 它做什么

13 颗工具默认全部注册，装完即用，没有「还要额外打开」的能力。

| 工具 | 做什么 |
|---|---|
| `web_read` | 抓任意 URL：反爬阶梯、PDF / OCR、批量、聚焦提取；产出正文、**字节**（`save_to`）或**证据**（状态行 / 响应头 / 302 链）；`check` 回答「这页变了吗」并附一份 unified diff |
| `web_act` | 在页面上动手：点击、填写、**选下拉框**、按键、拖拽、滚动、截图、**上传文件**、**应答弹窗**、**多页签**、**历史后退**、**打印成 PDF**；提交默认暂停，等你点头 |
| `web_request` | 发一次非 GET（POST / PUT / PATCH / DELETE / HEAD），只发一次：不重试、不缓存、不升浏览器 |
| `web_search` | 10 个免密引擎并行 + 本地重排，可顺带抓回前三条全文；另开七类纵向源（新闻 / 论文（arXiv + Crossref）/ 问答 / 代码 / 社区 / 包元数据 / GitHub 仓库） |
| `web_research` | 一个问题，一次调用，一份带 `[n] URL` 引用的证据包；坏的源自己说明为什么，从不拖垮整包。`cross_check=true` 另交一份来源一致性报告（claim / 支持与反对域名 / `agreement_score`）——是一致性信号，不是事实核查 |
| `web_recall` | 检索**本机读过的页**：零网络、零模型，回指针、摘录与过期标注 |
| `web_crawl` | 同域爬取：sitemap、关键词过滤、路径子树 |
| `web_discover` | 出站点的 URL 地图，先看清有什么再挑着读 |
| `doc_parse` | 本地 docx / xlsx / pdf / pptx / odt / csv / html / json / yaml → Markdown |
| `feed_read` | 批量 RSS / Atom，`since=` 只取增量 |
| `cred_defaults` | 设一次的默认身份：代理、浏览器 profile、CDP 端点、cookie、auth |
| `sys_state` | 看与清本地状态：正文缓存、会话 cookie、引擎冷却 |
| `sys_guide` | 读 dhole 自己的说明书：选工具、写参数、字段含义、失败怎么修 |

装好之后直接说人话：

- 「抓这个页面的价格，列成表」→ `web_read`
- 「就这个问题给我一份带引用的答案」→ `web_research`
- 「`/guide` 下面都收一遍」→ `web_crawl`
- 「之前读过的那篇讲注意力机制的文章找回来」→ `web_recall`
- 「在这个后台把这条记录删掉」→ `web_act`
- 「这页一变就告诉我」→ `dhole watch`

## 它和别的抓取器不一样在哪

- **零密钥**：12 个免密搜索引擎、七类纵向源、反爬升级、重排模型，没有一项需要注册或充值。可选的 `tavily` / `bocha` / `exa` / `deepseek` / `firecrawl` / `brave_api` 只在你自己配了 key 并且点名时才调用。
- **零模型**：判断归外层 agent，dhole 只做确定的工具层。同一份页面两次抓取，同一个结果；不会有一个小模型在背后替你决定「这页大概讲什么」。
- **全本地**：正文缓存、cookie、重排模型都在 `~/.dhole`；除了你让它抓的目标站点，进程不往外发任何数据。
- **失败说真话**：每次调用都回同一份 JSON——抽了多少字、走的哪一档、下一步该调什么。抽空了就说空了，不会把一张反爬灰页包装成「成功正文」。

## 同类 MCP 成本对比（2026-10-26 同台实测）

**口径先说清**：connect 成本 = `initialize` 下发的 server instructions + `tools/list` 的紧凑 JSON 字符数（没有 prompt cache 时每次模型请求都要重付，有 cache 约一折）。各家取当天 npm/PyPI 最新版；dhole 按自己实测的 4.16 字符/token 折算（统一按 4.0 则各家等比上浮，排序不变），**跨家倍数一律用字符比**。dhole 那一行随时可复跑：`python tests/tool_payload_measure.py`；对手那几行钉的是 2026-10-26 的版本号，不随发版复测。

| MCP | 工具数 | connect 紧凑字符 | ≈tokens | 计费 |
|---|---|---|---|---|
| Brave Search | 8 | 110,719 | ~27.7k | API key，按量 |
| Firecrawl | 28 | 94,937 | ~23.7k | credits |
| Playwright（全开 caps） | 72 | 45,602 | ~11.4k | 免费 |
| **dhole-mcp** | **13** | **33,612**（30,885 工具 + 2,727 说明书） | **~8.1k** | 免费 keyless |
| Playwright（默认） | 25 | 20,286 | ~5.1k | 免费 |
| Tavily | 5 | 7,694 | ~1.9k | API key，按量 |
| Exa（hosted，仅测 tools/list） | 2 | 2,419 | ~0.6k | keyless 有限额 |
| Fetch（官方参考实现） | 1 | 1,150 | ~0.3k | 免费 |

- 七个产品里**第 4 便宜**（不计 Exa 则第 3；Playwright 两档分开算则第 5），比 Brave / Firecrawl 这两个热门方案便宜 **2.9~3.3 倍**（字符比 3.35 与 2.87）。
- **工具数少不等于描述省**：Brave 8 颗吃掉 27.7k（平均每颗 ~3.5k token），dhole 13 颗平均每颗 ~0.6k，覆盖 fetch / search / crawl / act / research / feed / PDF / recall / 本地文档。
- 那 2,727 字符（≈656 token）的说明书是连接期唯一的用法导引；深度文档不进上下文，谁问谁付（`sys_guide` 或 `dhole skill install`）。裁到 `DHOLE_TOOLS=web_read,web_search` 时，connect 口径降到 11,664 字符（≈2.8k）。

**变动成本**——同一个高密度文档页（`docs.python.org/3/library/asyncio-task.html`），一次调用拿回多少：

| 方案 | 返回字符 | ≈tokens |
|---|---|---|
| dhole `web_read` @`max_content_chars=8000` | 9,839 | 2.5k |
| dhole `web_read` + `focus=` | 23,131 | 5.8k |
| dhole `web_read` 默认 | 41,860（截断 + `next_offset` 续读） | 10.5k |
| Tavily `tavily_extract` 默认 | 62,523 | 15.6k |
| Firecrawl `scrape` 默认 | 80,824 | 20.2k |
| Playwright `browser_snapshot` | 137,246 | 34.3k |

同一个搜索词（`model context protocol`）：Tavily basic 4,605 字符（最省）· dhole `web_search` 7,916（最快，3.6s；行内带 `relevance` 与 `engines_consensus`）· Firecrawl 摘要 11,201 · Tavily advanced+raw 60,486 · Firecrawl 内联正文 152,696。同一个研究问题一发调用：dhole `web_research` 10,130 字符 / 3 源证据包，Tavily `research` 10,014 字符 / 30.5s 成文答案——**单次样本打平**，形态不同（可引用的原文 vs 已成文的结论），复跑波动大，不写成稳定结论。

**不粉饰的部分**：截断与分页不是 dhole 独有（官方 Fetch 有 `max_length` / `start_index`；Tavily 有 `query` 重排，Firecrawl 有 `queryOptions` 与 `includeTags`/`excludeTags`），独有的是「免费 + 无 key + 截断分页 + 词法 focus」这套组合。纯 token 口径下 Tavily basic 的搜索行确实比 dhole 省；Playwright 的 snapshot 是整棵可访问性树、每次操作后通常要重拍，但它换来的是 dhole 没有的自动化深度。

**什么时候不该用 dhole**：前端 E2E、网络拦截 mock、录像取证 → Playwright；大规模托管爬取与定时监控 → Firecrawl；要 SLA 或企业级索引 → Tavily / Brave / Exa；只是抓一个 URL → 官方 Fetch。

## 配置

两条门：宿主配置里的 `env` 块（改完要重启），或 `dhole config set NAME value` 写进 `~/.dhole/config.json`（下一次调用就生效）。**env 永远优先**。默认行为通常就是你要的，不确定就先别设。

<details>
<summary>全部 21 个旋钮</summary>

| 变量 | 什么时候设 |
|---|---|
| `DHOLE_SEARCH_PROXY` | 搜索引擎被限流 / 被墙时（也自动读 `HTTPS_PROXY`） |
| `DHOLE_IGNORE_ROBOTS` | 关掉进程级的 `robots.txt` 遵从；单次豁免用 `ignore_robots=true` |
| `DHOLE_ALLOW_PRIVATE_HOSTS` | 放行内网 / 本机地址，默认一律拒绝 |
| `DHOLE_HTTP_TOKEN` | `--http` 传输的 bearer 门；不设则端口不设防 |
| `DHOLE_PERSIST_CREDENTIALS` | 把会话 cookie 与凭据落盘（Windows DPAPI 加密）。默认只在内存，进程结束即清 |
| `DHOLE_USAGE_LOG` | 本地调用与写操作日志，不记参数值、不联网 |
| `DHOLE_QUERY_LOG` | 把真实搜索过的查询词落到本地（默认关；只记查询词与模式/结果数，不含结果/URL/摘要，不联网），用于真实查询评测 |
| `DHOLE_TOOLS` | 只注册列出的工具，**写了就等于关掉其余** |
| `DHOLE_DEFAULT_ENGINES` | 覆盖默认搜索池 |
| `DHOLE_SEARCH_ROW_FILTER` | `off` 关掉 SEO 农场行过滤（默认开） |
| `DHOLE_DEFAULT_CONTENT_CHARS` | 默认正文预算，40000（500–200000） |
| `DHOLE_BROWSER_IDLE_TIMEOUT` | 热浏览器闲置多少秒后关闭，默认 1800（30 分钟）；`0` = 永久保活（重启宿主生效） |
| `DHOLE_TAVILY_API_KEY` / `DHOLE_BOCHA_API_KEY` / `DHOLE_EXA_API_KEY` / `DHOLE_DEEPSEEK_API_KEY` / `DHOLE_FIRECRAWL_API_KEY` / `DHOLE_BRAVE_API_KEY` | 六颗 keyed 引擎的密钥，配了才启用、点名才调用 |
| `DHOLE_HF_ENDPOINT` | 重排模型下载源，默认 huggingface.co，失败回退镜像 |
| `DHOLE_RERANK_MODEL` | `bge-zh`（默认，中文更强）或 `mmarco`（快档，约 1.8×） |
| `DHOLE_HOME` | 状态目录，默认 `~/.dhole` |

</details>

<details>
<summary>搜索引擎与重排</summary>

默认池 12 个免密引擎，**池序即可信度分层**（连通稳定的主力在前，按 IP 限流、常被反爬/地区挡的居中，小众浅索引垫底；合并序也按这张表稳定分层，主力先说话）：`baidu` / `bing` / `bing_global` / `duckduckgo`（`ddg`）/ `yahoo` / `brave` / `sogou` / `so360`（`360`）/ `shenma` / `yandex` / `seznam` / `marginalia`。`engines=` 只能收窄，不能新增。索引家族是 9 个：`yahoo` 与 `bing` / `bing_global` / `duckduckgo` 同属 Bing（多入口、不多家族），`shenma` 是阿里系移动自有索引（夸克同一套基础设施），`seznam` 是捷克门户的自有索引，`marginalia` 是独立小网索引。国内直连五席：`baidu` / `bing` / `sogou` / `so360` / `shenma`。另有 `tavily` / `bocha` / `exa` / `deepseek` / `firecrawl` / `brave_api` 六颗 keyed 后端（`brave_api` 与免密 `brave` 同索引，是付费的稳定入口）。

重排用本地 cross-encoder，首次用到才下载（`bge-zh` ~279MB / `mmarco` ~119MB），换档要重启宿主。想在第一次搜索前就下完，用 `dhole models fetch`（可选 `--model mmarco`；进度在 stderr、断了重跑续传、下完核对发布方 sha256）；搜索路径本身不等下载，模型没就位时按查询词覆盖率分桶排序，不丢行。冻结语料上的公开评测：`python evals/vertical_search_baseline.py`、`python evals/ranking_baseline.py --tiers`，方法与两次采样见 [evals/README.md](evals/README.md)。

**我们不为免密公共 API 承诺 SLA**；能承诺的是失败如实进报告、不冒充「没结果」。

</details>

<details>
<summary>上下文成本</summary>

全量连接期开销 33,612 字符（约 8.1k token，13 个工具，实测；精确值随时跑 `dhole wire ledger`）。按用法裁剪时：读 / 搜 / 请求 / 爬 / 文档 / 订阅 / 状态 / 指南这 8 颗是 23,727 字符，加 `web_discover` 共 9 颗 25,861——被裁的工具连自己的路由行一起退出。别按单颗体积挑「最小的 N 颗」，那会把 `web_read` / `web_act` 这类主力排除在外。

</details>

## 边界

- **`robots.txt` 默认遵守**：被 `Disallow` 的 URL 一个请求都不发。要绕开是 `DHOLE_IGNORE_ROBOTS` 或单次 `ignore_robots=true`，那是你的合规决定——目标站点的 ToS 与当地法律由使用者承担。
- **`content_ok=true` 不代表内容为真**：只表示 2xx、无错、正文非空。几百字符的「成功」多半是登录墙。
- **写请求只发一次**：`POST` / `PUT` / `DELETE` 不重试、不缓存、不升浏览器。超时那句答案是「不知道」，不是「没成功」。
- **提交默认暂停**：`web_act` 的 `submit` 不带 `confirm=true` 什么都不提交；验证码最多试 3 次就转交，2FA 与滑块一律转交。
- **凭据不越界**：cookie 与 `Authorization` 只发给源站，跨源重定向不带；没带 host 的凭据一律拒；内网与本机默认拒绝，云元数据端点任何时候都不放行。
- **抓回的正文是不可信数据**：里面的指令按提示注入处理，可疑时响应带 `untrusted_content` 标记。标记不是拦截，裁决归外层 agent。

## 常用的几条命令

| 命令 | 用途 |
|---|---|
| `dhole read <url> [url2 ...]` | 不开 MCP 也抓页面：stdout 是正文（`--max-chars` / `--offset` / `--timeout S`） |
| `dhole search <query>` | 关键词搜索：stdout 每行一条结果（`--max-results N` / `--site` / `--freshness`；`--sources package,repo,academic,…` 走七类纵向源，包与仓库要精确名） |
| `dhole crawl <url>` | 爬一个站点：stdout 每页一节（`--max-pages N` / `--max-depth N`） |
| `dhole discover <url>` | 站点地图：stdout 一行一个 URL（`--sitemap`） |
| `dhole research <query>` | 一问一证据包：stdout 是带 `[n]` 与 URL 的引用块（`--max-sources N`；`--cross-check` 另交来源一致性报告） |
| `dhole recall <query>` | 找这台机器以前读过的页：URL + 摘录（只读本地缓存） |
| `dhole request <url> --method ...` | 一次非 GET 请求（写操作要 `--confirm`；`--body-file` 让密码不进 shell 历史） |
| `dhole act <url> --actions ...` | 动作链（JSON / `@file` / `-`），每个动作的回执在 stderr |
| `dhole doc <file>` | 本地文件转 markdown（pdf / docx / xlsx / csv / pptx / odt …） |
| `dhole feed <url> [url2 ...]` | RSS/Atom 条目，按源分节（`--since` 增量轮询） |
| `dhole state` | 本机状态普查；`--show cache,models`、`--clear cache`、`--clear sessions --confirm` |
| `dhole models fetch [--model NAME]` | 一次性把重排器下完：进度在 stderr、断了重跑续传、下完核对发布方 sha256（`--model bge-zh\|mmarco`，默认跟 `DHOLE_RERANK_MODEL`） |
| `dhole creds [show\|set\|unset]` | 默认身份存储（只显示脱敏值；`set` 读 JSON / `@file` / stdin） |
| `dhole guide [topic] [section]` | 读 dhole 自己的说明书 |
| `dhole call <tool> [--arg ...]` | 任意 MCP 工具的通用入口（`--args` 收 JSON；默认 JSON，`--text` 交内容通道） |
| `dhole tools [--json]` | 13 颗工具的活参数面（`call` 接受的名字以它为准） |
| `dhole help [command]` / `dhole <command> --help` | 完整帮助：每个旗标、示例、退出码 |
| `dhole commands [--json]` | 机器可读的命令目录（旗标、位置参数、工具映射） |
| `dhole completion bash\|zsh\|fish\|powershell` | 打印对应的 shell 补全脚本 |
| `dhole doctor [--json]` | 安装体检，逐项给修复命令 |
| `dhole config list` / `get` / `set` / `unset` | 改旋钮，看当前值与来源（均支持 `--json`） |
| `dhole engines list` / `probe` / `reset` | 引擎冷却状态、实测健康、清空（均支持 `--json`） |
| `dhole watch add` / `list` / `run` | 页面变更清单；`run` 退出码 0 无变化 / 1 有变化 / 2 检查失败，`--notify` 弹桌面通知 |
| `dhole skill status` / `install` | 把随包说明书装成宿主原生 skill（默认 `~/.agents/skills`，`--target DIR` 装进宿主的技能根） |
| `dhole wire ledger` / `lock` | 连接面成本账与锁（`--json`） |
| `dhole version` / `dhole -v` | 版本 + 能力面板（`version --json` 是机器可读形态） |

13 条内容命令与 MCP 的 13 颗工具一一对应：stdout 只有内容、stderr 只有元数据，可以直接进管道；退出码 0 = 成功且有内容，1 = 抓到了但内容不对（`content_ok=false` 或 `error` 非空），2 = 用法错误。`--json` 时 stdout 是完整回执（未加 `--pretty` 时与 MCP 同一份字节）；`dhole guide` 是纯散文，`--json` 只把整篇文档包成一个 JSON 字符串。没列出的工具参数走 `--arg NAME=VALUE`（值是 JSON 就按 JSON 解析，例如 `--arg include_links=true`）。

**输出与自动化**：`--ndjson` 把批量读、爬行、搜索、订阅、研究与本机召回拆成一行一个 JSON 对象（带 `type`；搜索的纵向源失败是 `source_error` 行，研究的引用、失败源与交叉印证分别是 `evidence` / `source_failed` / `cross_check` 行）；`--pretty` 缩进 JSON；`--quiet` 关掉 stderr 元数据（错误仍然打印）；`--verbose` 加摘要与下一跳；`--progress` 在 TTY 上显示长任务的耗时；`--no-color` / `--color=auto|always|never` 控制颜色；`--dry-run` 只把绑定结果交成 `{dry_run, tool, args}` 并退出 0，一次请求都不发——旗标怎么落成工具参数，先看见再决定要不要跑。全局旗标放在命令前后都行（`dhole --json read URL`）。`--urls-file FILE` 或位置参数 `-` 从文件 / stdin 读 URL 清单，批量读与 `feed` 都支持。`--json` 下的用法错误会交 `{"error": "...", "exit_code": 2}`，不再只是一句自然语言。

纵向源与交叉印证的人读出口同样在 stderr：`dhole search --sources package` 逐后端报 `empty` / `blocked` 与 `requested/delivered`，行尾带上后端与发布日期；`dhole research --cross-check` 给出 claim 判词、`cross_check_basis`、`agreement` 与 warnings（一致性信号，不是事实核查）。重排模型没就位时 CLI 与 MCP 走同一条冷启动路：这一轮按跨引擎共识排完，stderr 明说原因；`dhole state --show models --verbose` 看下载状态。

`dhole call` 是参数面的逃生口：命名旗标仍然优先，只有当某颗参数还没有旗标时才用它。例如 `dhole call web_read --arg url=https://example.com --arg nested_max=3`。

**CLI 每条命令都是新进程**：会话 cookie 与 `cred_defaults` 默认只在内存里，跨命令使用要 `DHOLE_PERSIST_CREDENTIALS=1`（`dhole creds set/unset` 在未开启时直接拒绝，而不是假装写入）。`dhole request` / `dhole act` 的写操作遵循工具自己的 `confirm` 门。

监控是轮询不是推送：频率归你的 cron / systemd timer。没有 webhook 出口，是决定不是缺口——dhole 是只读的本地工具层，不变成拿着页面内容往外 POST 的进程。

## 本地数据

全在 `~/.dhole/`（`DHOLE_HOME` 可换；POSIX 下目录 0700、文件 0600）。正文缓存 `cache.db` 是明文 SQLite，TTL 1 小时（article 6h）——`web_recall` 搜的就是它。会话与凭据默认只在内存，`profiles/` 存的是持久浏览器 profile，**含登录态**，删目录即注销。

共享浏览器默认就落在 `profiles/default/`：cookie 与登录态跨进程、跨天活着，今天登录过的站点明天直接就是登录态；`cred_defaults(set={'profile': '<name>'})` 换一份，`profile=''` 要一次性空会话（退出即焚）。浏览器被空闲回收关掉不等于丢登录态——下一次抓取打开的还是那一份 profile。没有任何遥测。

## 文档

- [CONTRIBUTING.md](CONTRIBUTING.md) —— 开发环境、测试、引擎契约
- [evals/README.md](evals/README.md) —— 公开评测的方法与读数
- [CHANGELOG.md](CHANGELOG.md) —— 每个版本改了什么，以及为什么
- 手册两个入口，同一份语料：`sys_guide(topic=...)` 或 `dhole skill install`

## 致谢

本项目是 [dondai1234/master-fetch](https://github.com/dondai1234/master-fetch) 的二创（衍生作品），上游以 MIT 协议发布，原始版权文本完整保留于 [LICENSE](LICENSE)。

## 许可证

[MIT](LICENSE)
