<div align="center">

# Dhole

**让 AI 代理访问互联网：抓取 · 爬取 · 搜索，内置反爬，$0 无密钥。**

<a href="https://pypi.org/project/dhole-mcp/"><img src="https://img.shields.io/pypi/v/dhole-mcp.svg" alt="PyPI version"></a>
<img src="https://img.shields.io/pypi/pyversions/dhole-mcp.svg" alt="Python 3.11+">
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.svg" alt="MIT License"></a>
<img src="https://img.shields.io/badge/transport-stdio%20%C2%B7%20HTTP-blueviolet.svg" alt="MCP stdio and streamable HTTP transports">

</div>

Dhole 是一个 [MCP](https://modelcontextprotocol.io) 服务器，为 AI 代理提供网页抓取、整站爬取和无密钥网页搜索。HTTP 被拦截时自动升级到反检测浏览器，可读取 PDF（含扫描件 OCR），全部本地运行、无需任何 API key。

## 功能特性

- **智能抓取**：HTTP 优先（~1 秒），被拦截或遇到 JS 空壳时自动升级隐身浏览器，仍失败则回退 Wayback Machine 快照
- **网页搜索**：免密引擎 14 个可选、6 个默认并行，本地神经重排序 + 跨引擎共识排名；也可以顺带抓回前三条全文
- **整站爬取**：同域最佳优先遍历，sitemap 模式，页数 / 深度 / token 预算控制
- **PDF + OCR**：结构化 Markdown 输出，扫描件与 CID 损坏自动 OCR
- **结构化提取**：CSS 选择器 / JSON-LD / 自动模式 schema，多 URL 批量并行
- **Agent 友好**：返回 `next_action` 建议；超长内容自动分页；SQLite 缓存加速重复抓取
- **优雅降级**：浏览器依赖缺失时自动切换纯 HTTP 模式（Termux / 精简容器可用）

## 安装

需要 Python 3.11+（本版在 Python 3.14 上开发与验证）。

```bash
pip install "dhole-mcp[all]"     # 完整版（反爬浏览器 / PDF / OCR / 解析器）
playwright install chromium      # 反检测浏览器引擎（~150MB，完整版需要）
```

精简版（纯 HTTP + 搜索，无浏览器依赖）：`pip install dhole-mcp`
源码安装：`git clone https://github.com/ouli-1242/dhole-mcp.git && cd dhole-mcp && pip install .[all] && playwright install chromium`
卸载：`pip uninstall dhole-mcp`；运行时数据全在 `~/.dhole`，删掉该目录即清理干净

### 让 agent 自己装

把下面整块粘给你的编码 agent，它会自己完成安装与配置：

```
在这台机器上安装 Dhole MCP 服务器。逐步执行，不要跳步。

1. 先判断你运行在哪个 agent 宿主（Claude Code / Cursor / OpenCode / Pi 等），
   找出 (a) MCP 配置文件的位置，(b) 它添加本地 MCP 服务器所需的格式。必要时读宿主文档，不要猜。
2. 运行 pip install "dhole-mcp[all]"，再运行 playwright install chromium（已装则跳过）。
   任一步失败就停下并告诉用户，不要继续。
3. 备份第 1 步找到的配置文件，按宿主要求的格式添加名为 "dhole" 的服务器，
   命令为 "dhole"，不带参数、不需要 API key、不需要环境变量。
4. 让用户重启 agent。重启后应能看到 smart_fetch / smart_search / smart_crawl /
   screenshot / parse / feed_fetch / resolve_url / cache_clear / close_session 九个工具，
   最后运行 dhole --doctor 确认检查项通过。
5. 可选：运行 dhole skill install 把随包分发的 agent 技能装到 ~/.agents/skills/dhole-web
   （教 agent 怎么选工具、防坑与排查的手册；宿主重启后生效）。
```

## 使用

在 MCP 客户端（Claude Code / Cursor / OpenCode 等）配置中添加：

```json
{ "mcpServers": { "dhole": { "command": "dhole" } } }
```

无需参数、无需密钥、无需环境变量。要改行为就在同一层加 `env`（下面[配置](#配置)表里的任何一个变量都可以写在这里，键名原样、值一律字符串）：

```json
{
  "mcpServers": {
    "dhole": {
      "command": "dhole",
      "env": {
        "DHOLE_IGNORE_ROBOTS": "1"
      }
    }
  }
}
```

`DHOLE_IGNORE_ROBOTS` 是关掉默认开启的 `robots.txt` 遵从的**进程级**开关；只想放行某一次请求就在调用时传 `options.ignore_robots=true`，不必改配置。

能调的东西大致管六件事，逐条在下面的[配置](#配置)：**搜索池与引擎冷却退避**、**代理**、**token 开销**（正文预算 / 只注册常用工具 / 响应形状退回旧写法）、**robots 与 SSRF 的豁免边界**、**隐身浏览器的预热与空闲关闭**、**状态目录与本地调用日志**。

CLI 自带诊断与配置命令：

| 命令                  | 用途                                                             |
| --------------------- | ---------------------------------------------------------------- |
| `dhole -v`            | 版本 + 能力面板（浏览器 / PDF / 重排 / 引擎产出）                |
| `dhole --doctor`      | 安装体检：逐项定位问题并给出可直接复制的修复命令（失败退出码 1） |
| `dhole proxy`         | 管理搜索代理池（list / add / remove / clear）                    |
| `dhole engines list`  | 每个引擎的冷却判定与剩余秒数（`dhole engines` 即 list）          |
| `dhole engines reset` | 立即清空引擎冷却，不等自然到期                                   |
| `dhole engines probe` | 逐个实测引擎健康：一家一次真查询，印命中数 / 耗时 / 被拦原因     |
| `dhole model`         | 查看 / 切换重排模型                                              |
| `dhole skill`         | 安装 / 查看随包分发的 agent 技能（status / install [--force]）   |
| `dhole -u`            | 自更新（本 fork 默认关闭）                                       |
| `dhole --http`        | 改用 streamable HTTP 传输对外 serve（默认是 stdio，见下方「传输方式」） |

### 第一次调用

装好并重启宿主后，直接对 agent 说人话就行，由它挑工具：

- 「抓 https://example.com/pricing，把价格列成表」→ `smart_fetch`
- 「查这个库的官方文档，挑两条最相关的读给我」→ `smart_search` 再 `smart_fetch`
- 「把 docs.example.com 的 `/guide` 下面都收一遍」→ `smart_crawl`（`path_include=["/guide"]`）
- 「这份 report.pdf 讲了什么」→ `parse`（本地文件）或 `smart_fetch`（在线 PDF）

想先确认工具在不在，看宿主的工具列表，或跑 `dhole --doctor`。

### 传输方式

默认 **stdio**：agent 把 `dhole` 当子进程拉起来，上面的 `mcpServers` 配置就是这个用法。要让多台机器或几个宿主共用同一个 dhole，用 `--http` 切成 streamable HTTP（MCP 2025-03-26），端点是 `http://<host>:<port>/mcp`：

```bash
dhole --http --host 0.0.0.0 --port 8765     # 默认只监听 127.0.0.1:8765
```

这一层**不带任何鉴权**，也不校验来源：`--host 0.0.0.0` 就等于把「替你上网抓任意 URL」的能力开放给网络上任何人（SSRF 守卫只管内网地址，不管它替谁去抓公网）。要放在共享网络里，请先自行套上反向代理 / 认证 / 防火墙规则。

## 配置

所有环境变量均可选，默认零配置可用。

| 变量                                                                 | 用途                                                                                                                                                                                                              |
| -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `DHOLE_SEARCH_PROXY`                                                 | 搜索引擎代理，逗号分隔可轮换；也自动读 `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY`。也可用 `dhole proxy add` 写入配置                                                                                              |
| `DHOLE_DEFAULT_ENGINES`                                              | 覆盖免密默认池，逗号分隔（默认 `baidu,bing,sogou,bing_global,yandex,brave`）                                                                                                                                      |
| `DHOLE_SEARCH_DEADLINE`                                              | 单次搜索整体截止秒数（默认 16）                                                                                                                                                                                   |
| `DHOLE_SEARCH_FEEDBACK`                                              | 设 `1` 开启域名偏好：抓成功的域名获得排序加权。默认关闭                                                                                                                                                           |
| `DHOLE_BROWSER_IDLE_TIMEOUT`                                         | 浏览器空闲关闭秒数（默认 300，`0` 永不关闭）                                                                                                                                                                      |
| `DHOLE_NO_BROWSER_PREWARM`                                           | 设 `1` 后启动不预热隐身浏览器（默认预热，为首次抓取省 3-5 秒冷启动）                                                                                                                                              |
| `DHOLE_IGNORE_ROBOTS`                                                | 设 `1` 后不再检查 `robots.txt`（默认检查，见[已知限制](#已知限制)）；单次豁免用 `ignore_robots=true`                                                                                                              |
| `DHOLE_TOOLS`                                                        | 只注册列出的工具，省连接期开销（`smart_fetch,smart_search` ≈ −49%）                                                                                                                                               |
| `DHOLE_DEFAULT_CONTENT_CHARS`                                        | 默认正文预算（40000，区间 500–200000）                                                                                                                                                                            |
| `DHOLE_STRUCTURED_CONTENT`                                           | 设 `1` 时每次调用**额外**再发一份 `structuredContent`（同一份 JSON 的第二份拷贝）。默认只发文本通道：本服务器不声明 `outputSchema`，严格客户端本来就有权忽略它，而会渲染它的客户端每次调用体积翻倍。只有当下游确实按结构化字段解析结果时才开 |
| `DHOLE_OUTPUT_SCHEMA`                                                | 设 `1` 时在 `tools/list` 里声明每个工具的 **`outputSchema`**：字段集合由响应压缩策略**反推**得出（`required` = 压缩永不移除的那几个字段），所以"缺席即默认"这条语义从 `instructions` 里的散文变成客户端可机器校验的契约。**代价实测（stdio 线上量）：tools/list 19,141 → 22,242 字符，多 3,101，连接期 +14.6%**；朴素地把整个 envelope 写进 schema 要 2.1 万字符，所以只声明核心。按规范它与 `structuredContent` 是同一个决定（声明了就必须发），因此设 `1` 会**隐含打开** `DHOLE_STRUCTURED_CONTENT`，无法配出自相矛盾的组合。默认关，agent 用不到它；面向人的界面或要做校验的客户端才值这 14.6% |
| `DHOLE_WIRE_FULL`                                                    | 设 `1` 时**恢复瘦身前的响应形状**：每个字段恒定存在（含 `is_truncated:false`、`page_type:"unknown"` 这类默认值），`duration_ms` / `fetched_at` 也回到完整精度。给按 `result["字段"]` 直接取值、或不理解「缺席即默认」的调用方用——省字节因此是一行配置可逆的选择，而不是必须改代码。范围只到线上格式策略：地图模式（`discover_only`）的按列裁剪属于响应语义，不受它影响 |
| `DHOLE_SSRF_DNS_RECHECK`                                             | DNS 解析内网复查，**默认开启**；设 `0` 关闭。**fake-IP TUN 代理（Clash / sing-box 等）必须关**，否则所有公网站点都会被判成内网                                                                                    |
| `DHOLE_ALLOW_PRIVATE_HOSTS`                                          | 逗号分隔的主机白名单，让这些**内网 / 本机**地址绕过 SSRF 守卫（`localhost,my-service.local,192.168.1.50`）。默认空 = 一律拒绝；单次用 `options.allow_private`。云元数据端点（`169.254.169.254` 等）**永远**不放行 |
| `DHOLE_ENGINE_COOLDOWN`                                          | 普通被拒的冷却秒数（默认 60，区间 5–1800）。越界会被夹紧并在 `dhole engines list` 里说明                                                                        |
| `DHOLE_ENGINE_CHALLENGE_COOLDOWN` / `DHOLE_ENGINE_CONN_COOLDOWN`    | 连续 3 次「校验页」/ 连续 3 次「连不上」的冷却秒数（各默认 600，区间 60–7200）                                                                                     |
| `DHOLE_ENGINE_HEARTBEAT`                                           | 主动巡检还在冷却中的引擎的间隔秒数（默认 300；`0` = 关）。探测失败**不会**延长惩罚，原到期时间原样放回                                                             |
| `DHOLE_HOME`                                                         | 状态目录（默认 `~/.dhole`），装着抓到的正文明文与搜索词；共享机器上可指到别处                                                                                                                                     |
| `DHOLE_WORKDIR`                                                      | `parse` 解析相对路径时额外尝试的目录（排在 `cwd` 参数之后）                                                                                                                                                       |
| `DHOLE_HF_ENDPOINT`（或 `HF_ENDPOINT`）                              | 重排模型下载源；默认先试 `huggingface.co`、失败回退 `hf-mirror.com`，设了就只用这一个                                                                                                                             |
| `DHOLE_BRIGHTDATA_API_KEY` / `_ZONE` / `_COUNTRY`                    | Bright Data SERP 后端（zone 默认 `dhole`，地区默认 `us`）                                                                                                                                                         |
| `DHOLE_TAVILY_API_KEY` / `DHOLE_EXA_API_KEY` / `DHOLE_BOCHA_API_KEY` | 对应 keyed 引擎的密钥（均默认不跑）                                                                                                                                                                               |
| `DHOLE_USAGE_LOG`                                                    | 设 `1` 写本地调用日志（工具名 / 耗时 / 脱敏错误），不记参数值、不联网                                                                                                                                             |
| `DHOLE_NO_AUTO_REPAIR`                                               | 设 `1` 后入口遇到 ImportError 只打印修复命令，不自动重装                                                                                                                                                          |

**引擎冷却与退避**：引擎被拒或连续连不上会自动冷却，一次成功即清零，到期自动放行；`dhole engines list` 印出每家的判定、剩余秒数**以及当前生效的三档退避**，`dhole engines reset` 立即清空。三档都可调（默认值就是过去的硬编码值）：`DHOLE_ENGINE_COOLDOWN`（普通被拒，60 秒，5–1800）、`DHOLE_ENGINE_CHALLENGE_COOLDOWN`（连续 3 次校验页，600，60–7200）、`DHOLE_ENGINE_CONN_COOLDOWN`（连续 3 次连不上，600）。越界的值会被**夹紧并说明**——`DHOLE_ENGINE_COOLDOWN=60000` 是 16 小时的死池，没人是这个意思。

**主动巡检**：`DHOLE_ENGINE_HEARTBEAT`（秒，默认 300，`0` = 关）到点会单独敲一次仍在冷却中的引擎（一家一次查询，不并进搜索轮次），答得上来就当场解除冷却，不必等窗口走完。两条边界：探测**失败绝不延长惩罚**（原到期时间原样放回——否则每几分钟探一次等于替站方的封锁续期，实测有站点静默 20 分钟仍回校验页）；池子健康时一条请求都不发。

## 工具

| 工具           | 功能                                                                                                                                     |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `smart_fetch`  | 抓取任意 URL：自动反爬升级、PDF/OCR、批量、聚焦提取、页面交互、结构化提取、`method`/`body`（POST/PUT/DELETE/PATCH/HEAD）、`auth`（basic/bearer/API-key 头）、条件请求 `if_none_match`/`if_modified_since`（→ `not_modified`）；正文里的 `<meta refresh>` 跳转会跟着走 |
| `smart_search` | 无密钥网页搜索：多引擎并行、神经重排序、可同时抓回全文；`after`/`freshness` 日期窗口（`date_filter` 说明实际发到哪一档、哪几家不支持）                    |
| `smart_crawl`  | 同域最佳优先爬取，支持 sitemap 模式、关键词过滤与 `path_include` / `path_exclude` 子树限定；`options.delay` + 站点 `Crawl-delay` 控节奏，`crawl_urls` 里被丢掉的条目如实报数        |
| `screenshot`   | 页面截图。多模态代理直接收图；纯文本代理传 `options.save_to` 拿文件路径，再用自己的读图工具接上                                          |
| `parse`        | 本地文件解析（.html/.htm/.xhtml/.docx/.xlsx/.csv/.pdf/.md/.markdown/.txt/.json/.yaml/.yml/.pptx/.odt → Markdown）；相对路径按 `cwd` → `DHOLE_WORKDIR` → 服务器进程 cwd → 主目录依次尝试 |
| `feed_fetch`   | 批量抓取 RSS/Atom feed 最新条目，`since=<日期>` 只取增量（被过滤掉多少条会报出来）；传站点首页也行，会顺着页面自己声明的 feed 链接找到 feed（`discovered_from` 记录这一步）  |
| `resolve_url`  | 解析 URL 最终地址（跟随 HTTP 重定向与正文里的 `<meta refresh>` 跳转，不抽取页面正文）                                                            |
| `cache_clear`  | 清除抓取缓存（连 robots 结论与全部会话 cookie jar 一起忘掉，只要清某个会话请用 `close_session`）；`engine_state=true` 同时重置引擎冷却与产出记录                                             |
| `close_session` | 看有哪些会话还带着站方凭据（**无参数就是名册**：id、主机、cookie 名字、还剩多久过期、浏览器开没开），或点名 `session_id=` 忘掉一个、`all=true` 全清——不等 24 小时自然过期。cookie 值一律不出境 |

参数摆在哪里**分两类**，每个工具自己在 `tools/list` 里都写清了接受哪些键（README 不再逐参数抄一遍，抄一次就漂一次）：`smart_fetch` / `smart_crawl` / `smart_search` / `screenshot` 把旋钮收在一个 `options` 对象里，顶层同名参数也接受——那是兼容读法，两处都给时**顶层优先**；`parse` / `feed_fetch` / `resolve_url` / `cache_clear` / `close_session` 参数少，只有顶层这一层，塞 `options` 会被拒绝并列出它真正接受的键。两个方向相反的例外值得点名：`smart_fetch` 的 `url` 必须摆在外面（放进 `options` 不会被读取，调用会回一句「Either 'url' or 'urls' must be provided」）；`smart_crawl` 的 `discover_only` / `crawl_urls` / `focus` 反过来只能放顶层，放进 `options` 会被拒绝——报错里会说明它是顶层参数，不是没有这个能力。

## 使用要点

以下这些约定**会改变你怎么行动**（字段的含义、什么时候别信答复），所以集中列在这里；参数的定义与取值范围仍以 `tools/list` 为准。

### 抓取与请求（`smart_fetch` / `screenshot`）

- 显式传 `options.proxy` 时先做 **5 秒 TCP 预检**：代理不应答就返回 `error=proxy_unreachable`（不发请求、不升浏览器、不回退快照）。抬 `timeout` 没用，失败发生在请求之前
- `css_selector` **只对 HTML 生效**：XML / JSON / 纯文本响应上它匹配不到东西，会原样返回整份文档，并在 `summary` 里明说「selector was NOT applied」——别把整包内容当成你要的那一片
- `pages=` 只减少 PDF **抽取**的内容，文件仍然整个下载
- **会话 cookie 只覆盖 HTTP 层**：`options.session_id='name'` 之后，同一主机的 cookie 会留住并在下一次同名调用里带回（HTTP 层，24 小时过期，响应只报名字不报值）。隐身浏览器那一层靠它自己的热浏览器上下文保持 cookie，**不会**从 jar 里播种——两边不共享，`force_fetcher='stealthy'` 的调用不会带上 HTTP 层攒下的登录态。想知道到底攒了哪些、或者把它们清掉：`close_session`（无参数=名册，`session_id=` 点名关，`all=true` 全清；`cache_clear(all=true)` 也会清 jar，但连整个正文缓存一起）
- **非 GET 是一次性的**：`method=POST/PUT/DELETE/PATCH` 不重试（上一次很可能已经落库，重发就是写两遍）、不进缓存、不升隐身浏览器、不用 archive 快照——浏览器只会导航（GET），快照只是那一天的一次 GET，它们答的都不是你这个写请求。`HEAD` 只回**状态 + 声明长度 + 类型**，正文按定义为空，`content_ok` 就按这个判。body 上限 256 KB：这个工具是取回东西的，不是上传东西的
- **凭据不跟跨源重定向走**：调用方点名的 `cookies` 与 `Authorization` 只发给请求的那个源站；站点把我们转到别的域时这些头会被摘掉（同域跳转与「那一跳自己主机在 jar 里有 cookie」的情况照带）。抬 `allow_private` 也不会绕过这条
- **`options.auth` 只是把头替你摆好，不做认证流程**：`{type:'basic',username,password}` / `{type:'bearer',token}` / `{type:'header',name,value}`（`X-API-Key` 这类），`user`/`pass` 是简写、`type` 在形状能自证时可省。没有 OAuth 授权码 / client_credentials 那一套，也不会替你刷新 token——token 过期就换一个再调用。凭据的**值**永远不回显（`error` / `summary` / `metadata` 里都没有），带凭据抓回的正文按凭据分别进缓存（不会把管理员看到的内容回放给匿名请求），浏览器层同样把它绑在源站上（页面的第三方子资源拿不到它）。`401` 会分话说清「凭据被拒」和「没发凭据」，这两件事的修法相反
- **`<meta http-equiv=refresh>` 会被跟随（HTTP 层）**：这类页面用 200 + 正文里的跳转声明自己是个指针，隐身浏览器本来就会自己跟，于是同一 URL 两层给出不同正文（实测）。现在两层都落到目标页，共用重定向的跳数预算、目标照旧逐跳过 SSRF 校验、凭据照旧不跨源，`original_url` 与 `metadata.meta_refresh` 说明这一步是从哪页跳来的。浏览器层对**带延迟**的刷新跟不跟取决于当时的时序（5 秒档实测就不跟），这条仍未解决；`HEAD` 与带请求体的方法都不跟（HEAD 没有正文可读，跟下去就是一次「说好不下载」的下载）
- **条件请求只问 HTTP 层**：`if_none_match` / `if_modified_since` 是发给源站的问答，浏览器层不接受这个问题（会把它连同缓存一起丢掉并在 `summary` 里说明）。304 是**成功**：`not_modified=true`、`content_ok=true`、正文为空——把它当失败去开浏览器渲染，只会多烧一次冷启动
- `include_links=true` 时 `links` 的每类上限是 30 / 20 / 20（`max_links` 可调到 1–100）：`links.total_found` 是截断前的真实条数、`links.is_truncated` 说明上限藏掉了东西，别把默认上限当成「这页只有这么多链接」
- **重复记录要写「容器选择器 + 子 schema」**：`schema={"properties":{"quotes":{"type":"array","selector":".quote","properties":{"text":{"selector":".text"},"tags":{"type":"array","selector":".tag"}}}}}`——每个匹配容器出一条记录，子选择器**在该容器内**求值。只写顶层 `.tag` 的话拿到的是整页 40 个标签的并集，「哪个标签属于哪条 quote」这个信息在输出里不存在。`items.properties` 是同一件事的 JSON Schema 写法；一个字段最多 200 条记录；嵌套最深 8 层；子选择器同样过注入校验
- **`scroll` 等的是页面停止变长，不是固定毫秒数**：每一步滚到底再轮询 DOM（增长就追新的底，安静两轮就收），整个动作最多花 20 秒预算——一条永不停止的信息流不能变成一次永不返回的调用。它同时补发一个 `scroll` 事件：视口够高、内容够矮时 `scrollTo` 不会产生任何事件，只听事件的懒加载就不会醒。用 `event.isTrusted` 卡懒加载的站点仍然不动，那种页面要用 `click` 点「加载更多」
- **截图落盘用 `options.save_to`**：给路径就写文件，并在文本段回报绝对路径 + 字节数（看不见图片的模型只有这一条路），父目录会创建；一个**不是图片命名**的既有文件会被拒绝（打错一个字不该吃掉 notes.txt），同名图片文件则允许覆盖重拍

### 整站爬取（`smart_crawl`）

- `smart_crawl` 的 `discover_only=true` 只出 URL 图、不抽正文（`content_ok` 恒为 `false`），但**展开链接图仍是一页一次请求**（最多 `max_pages` 次）：想要一次请求拿到图，用 `max_pages=1`（起始页的链接）或 `sitemap=true`（整站）
- `smart_crawl` 总预算硬顶 1,000,000 字符；显式给了 `max_total_chars` 之后，调 `max_pages` 不再影响预算
- `path_include` / `path_exclude` 按**路径子树**匹配：`'/docs'` 命中 `/docs` 及其下全部，但**不**命中 `/docs-old`；只接受尾部 `/*` 一种通配写法
- **爬取礼貌度是两个旋钮**：`concurrency` 管同时在飞几个请求，`options.delay` 管**同一台主机**多久被敲一次（秒，上限 60）。站点在 `robots.txt` 里写的 `Crawl-delay` 会把间隔抬高（不会压低你要的值），实际用的值写在 `summary` 里；`ignore_robots=true` 连站方这个要求一起忽略。注意 Python 的 `robotparser` 只认**整秒**，站家写 `Crawl-delay: 0.5` 等于没提要求

### 网页搜索（`smart_search`）

- **搜索的日期窗口只能放宽，不能收紧**：引擎只给 day/week/month/year 四档（实测：没有任何一家接受绝对区间），`after` 因此被放大到覆盖它的最窄一档，实际发了哪档、哪几家压根没被问日期，都在响应的 `date_filter` 里——其中 `exact` 的含义是「**发出的档位 == 覆盖窗口的最窄一档**」，不是「没被放宽」：`after=2026-09-01`（28 天窗口）发 month 档时 `exact` 就是 `true`。人话解释以 `date_filter.note` 为准；`before` 直接拒绝而不是静默忽略。`total_estimate` 也**没有实现**——六个引擎的解析器都不读「共约 N 条结果」，拿不到一个自己验不了的数字就不摆一个字段冒充

### 本地文件与 feed（`parse` / `feed_fetch`）

- **`parse` 也吃 .md/.txt/.json/.yaml/.pptx/.odt**：本地文档只有这一个入口——只会说 MCP 的客户端没有文件工具，「用你自己的文件工具读」那句话对它们不成立。.pptx / .odt 用 stdlib 的 zip + XML 读，**不引入新依赖**；文本工具拿不到的部分（备注页、版式、样式、图片、批注）写在输出的第一行里，而不是悄悄消失——一份少了备注的稿件和一份完整的稿件长得一样。`.json` / `.yaml` 除了返回原文还会**校验**：坏文件是一句错误，不是一堆读不懂的字节。未列出的扩展名（.log/.rst/.ini…）会拒绝，并提示改名成 .txt 再解析
- **HTML 表格在三个入口给出同一个 markdown 表格**：`parse`、`smart_fetch`、`smart_crawl` 共用同一个抽取函数，所以同一份 HTML 抽出的正文逐字节相同（此前 `parse` 把表格打成「Region / Q1 / 12」九行散落单元格，而同一工具对 DOCX / XLSX 给的是真表格）。`smart_crawl` 唯一有意的分叉是列表页：那里给的是结构化链接清单而不是正文，`page_type=list` 就是这个信号的字段
- **feed 读不懂时给一句人话**：站点首页也可以直接递给 `feed_fetch`，工具会顺着页面自己的 alternate 链接（最多两个候选）去找 feed；全都失败才报错，而且是一句能读懂的判定（不是 `XMLSyntaxError: Opening and ending tag mismatch: meta line 5`）加上试过哪些候选。发现到的地址**同样过 SSRF 守卫**：一个恶意页面不能借「自动发现」把工具指向内网或云元数据端点

## 可选的搜索引擎

免密引擎共 **14 个**：6 个在默认池，8 个是 opt-in（`engines=["duckduckgo","mwmbl"]` 点名才跑，单次最多 9 个）。另有 4 个付费 keyed 后端 `brightdata` / `tavily` / `exa` / `bocha`（需 key，默认不跑，见[配置](#配置)）。

| 引擎           | 索引 / 内容                                          | 默认池 | 国内直连 |
| -------------- | ---------------------------------------------------- | ------ | -------- |
| `baidu`        | 百度搜索（独立索引）                                 | ✔      | ✔        |
| `bing`         | Bing 中国版 `cn.bing.com`                            | ✔      | ✔        |
| `sogou`        | 搜狗主站（独立索引）                                 | ✔      | ✔        |
| `bing_global`  | Bing 国际版 `www.bing.com`（与 cn 版结果几乎不重合） | ✔      | 需代理   |
| `yandex`       | Yandex（独立索引）                                   | ✔      | ✔        |
| `brave`        | Brave（独立索引）                                    | ✔      | 需代理   |
| `duckduckgo`   | DuckDuckGo（别名 `ddg`）                             | opt-in | 需代理   |
| `yahoo`        | Yahoo                                                | opt-in | 需代理   |
| `baidu_baike`  | 百度百科条目页（知识库，覆盖窄）                     | opt-in | ✔        |
| `so360`        | 360 搜索（独立索引，别名 `360`）                     | opt-in | ✔        |
| `sogou_weixin` | 搜狗微信·公众号文章（垂直索引）                      | opt-in | ✔        |
| `wikipedia`    | 维基百科（知识库）                                   | opt-in | 需代理   |
| `grokipedia`   | Grokipedia（知识库）                                 | opt-in | 需代理   |
| `mwmbl`        | MWMBL 社区小型独立索引（覆盖窄、相关度参差）         | opt-in | 需代理   |

换默认池用 `DHOLE_DEFAULT_ENGINES`（见[配置](#配置)），opt-in 的名字也能写进去。

**日期过滤的能力跟着这张表走**：能被问日期的只有 `bing` / `bing_global` / `brave`（opt-in 的 `duckduckgo` / `yahoo` 与 keyed 的 `bocha` / `tavily` 也能），而且**只给 day / week / month / year 四档，没有任何一家接受绝对区间**。所以 `after=<日期>` 会被放宽到覆盖它的最窄一档，实际发了哪档、哪几家压根没被问日期，都在响应的 `date_filter` 里；`before` 直接拒绝而不是静默忽略（预设只能限定「不早于」，而搜索结果不带发布日期，「不晚于」既问不出来也验不了——要按日期取增量条目用 `feed_fetch(since=)`，feed 的条目是带日期的）。响应总数（`total_estimate`）也没有：这些引擎的页面里那一行「共约 N 条结果」一家都没有解析出来，拿不到又验不了的数字就不摆一个字段冒充。

### Keyed 搜索后端（brightdata / tavily / exa / bocha）

配了 key 即启用，与免费引擎**并行**执行，但**默认不跑**：只有 `engines=[...]` 显式点名才调用。按次计费，请求发出即扣配额。

## 重排模型

搜索排序由一个本地 cross-encoder 负责。模型不进 wheel，首次神经搜索时下载到 `~/.dhole/models/<名字>/`；缺依赖或下载失败时自动退回共识排序、不报错。

| 名字             | 语言                  | 体积   |
| ---------------- | --------------------- | ------ |
| `bge-zh`（默认） | 中英双语              | ~279MB |
| `zh-full`        | 跨语言（含中文/多语） | ~450MB |
| `ms-marco`       | 英文优先              | ~91MB  |

```bash
dhole model                  # 列出可用模型 + 当前生效的那个
dhole model use ms-marco     # 切换
```

**相关性下限要重排器在场才有意义**。`min_relevance` 作用在归一化分上（top 恒为 1.0），所以它只能削掉「有分布的一组结果」的尾部；`min_raw_relevance` 挂在 cross-encoder 的原始 sigmoid 上，才判断得了「这一轮没有一条相关」。原始分的分布是量出来的（bge-zh、真搜索结果）：离题文档 ~1e-4、勉强相关 0.2 上下、真命中 0.93+，所以**建议起点 0.1**；同主题不同侧面的硬负例 p90 只有 0.18，再往上抬就开始误杀。过滤发生时 `fetch_hint`（整轮被清空时是 `error`）会报这一轮原始分实际跨了多远。分布随模型而变，`dhole model use` 换模型后这个数要重新校准。

也可以直接编辑 `~/.dhole/config/reranker.json`（`{ "model": "ms-marco" }`）。未注册的名字会被拒绝并回退默认。下载可断点续传；**离线 / 多机复用**：把 `~/.dhole/models/<名字>/` 整个目录拷到目标机同一路径即可，之后不再联网。

## 故障排查

| 症状                     | 怎么办                                                                                                   |
| ------------------------ | -------------------------------------------------------------------------------------------------------- |
| 搜索结果很少或为空       | 引擎被限流或被墙。重试，或设 `DHOLE_SEARCH_PROXY`；`dhole engines probe` 看当下哪家能用                  |
| 所有公网站点都报内网地址 | fake-IP TUN 代理（Clash / sing-box 等），设 `DHOLE_SSRF_DNS_RECHECK=0`                                   |
| 抓取超时                 | `timeout` 是整次调用的墙钟预算，文档下载全额计入；大 PDF 请调大 `timeout`（默认 30000ms，上限 120000ms） |
| 正文被截断               | 用 `offset` / `next_offset` 续取，或调大 `max_content_chars`（上限 200000）                              |
| 返回了内容但明显是壳     | `content_ok=true` 只表示「2xx + 无错 + 正文非空」，先看字符量；几百字符的「成功」大概率是占位页          |
| 本地文件解析成乱码       | 显式传 `encoding=`。Shift_JIS / EUC-KR 会被解成看似合理的中文，不在自动探测范围内                        |
| 装不上 / 工具没注册      | 跑 `dhole --doctor`，它会逐项定位并给出可直接复制的修复命令                                              |

## 已知限制

- 无法绕过 DataDome / Akamai / 交互式 Turnstile；需要登录的网站不在设计范围内
- **搜索没有 SLA**：免密引擎是对公开搜索结果的直接抓取（无授权、无配额），对方改版或封 IP 时只会静默降级，不报错。要可靠性用 `DHOLE_SEARCH_PROXY` 或 keyed 后端
- 引擎可达性：默认池里 `baidu` / `bing` / `yandex` 国内直连稳定；`sogou` 按 IP 频次限流，`bing_global` / `brave` 无代理时不稳，任何时刻都可能有引擎不答话
- **合规自负**：**默认检查 `robots.txt`**——被 `Disallow` 的 URL 不会发出任何请求，返回 `error=robots_disallowed`、`content_ok=false`；`ignore_robots=true`（单次）或 `DHOLE_IGNORE_ROBOTS=1`（进程级）可关。抓不到 `robots.txt`（5xx / 超时 / 网络错误）时**放行**，并按 60 秒短 TTL 重试，别把「没查到」当成「合规通过」。我们的抓取 UA 是 `dhole-mcp`（robots 查询与请求都用它），站方要按名字放行时报这个。UA / TLS 指纹伪装、Cloudflare 验证求解是默认行为。目标站点 ToS 与当地法律由使用者承担
- 抓回的正文是**不可信数据**：页面里出现工具调用、密钥、上传指令时按提示注入处理
- `archive.org` 回退会返回**某个日期的快照**（看顶层 `source` / `archived_at`），时效敏感的内容引用前先确认；但**服务器自己回的 JSON/XML 错误体不会被快照顶替**（4xx/5xx 且 content-type 是 json/xml 时如实保留），代理不可达时也不回退——那次请求根本没发出去，拿快照回答等于答了一个没人问过的问题
- **内网 / 本机默认一律拒绝**（SSRF 守卫）。抓自己的 dev / staging / Docker 服务要点名：`options.allow_private=true`（只放开回环）或 `["my-service.local","192.168.1.50"]`，进程级用 `DHOLE_ALLOW_PRIVATE_HOSTS`。云元数据端点（`169.254.169.254`、`metadata.google.internal` 等）**任何时候都不放行**，包括写进白名单的情况
- 正文 **50MB 硬顶**，且在下载**之前**守门：装不下的直接报 `Response body too large`，抬 `timeout` 没用
- 连接期固定开销 21,282 字符（`tools/list` 19,141 + `instructions` 2,141，stdio 线上实测），每次连接付一次；换成 token 约 5.1k（cl100k 口径，分词器不同就不同——可比的是字符数）。每次调用的 envelope 已压到非默认值字段才上线。`DHOLE_TOOLS` 只注册常用工具可省约一半
- YouTube 仅能获取少量文本

## 本地数据

全在 `~/.dhole/`（可用 `DHOLE_HOME` 换位置；POSIX 下目录 0700、文件 0600）。**没有任何遥测**：除了你让它抓的目标站点，进程不往外发任何数据，也不上传设备信息。

| 位置                                                 | 内容                                                                                                           |
| ---------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `cache.db`                                           | 抓到的正文（明文 SQLite），TTL 默认 1 小时                                                                     |
| `sessions.db`                                        | `options.session_id` 的 cookie jar：**含 cookie 值**（=凭据），24 小时过期，`cache_clear` / `close_session` 清 |
| `models/<model>/`                                    | 重排模型                                                                                                       |
| `config/reranker.json`                               | 重排模型选择                                                                                                   |
| `circuit_breaker.json` / `engine_stats.json`         | 引擎冷却状态 / 各引擎解析产出                                                                                  |
| `search_proxies.json`                                | 代理池（凭据**明文**存储，`dhole proxy list` 显示时打码）                                                      |
| `search_feedback.json` / `usage.jsonl` / `repair.py` | 域名偏好 / 调用日志 / 自愈脚本                                                                                 |

`cache_ttl=0` 完全绕过缓存；用默认值时 docs 页自动抬到 24h、article 页 6h。

## 贡献

欢迎 issue 与 PR，规范（开发环境、测试、引擎契约与 fixture）见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 致谢

本项目是 [dondai1234/master-fetch](https://github.com/dondai1234/master-fetch) 的二创（衍生作品），上游以 MIT 协议发布，原始版权文本已完整保留于 [LICENSE](LICENSE)。

## 许可证

[MIT](LICENSE)
