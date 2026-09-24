<div align="center">

# Dhole

**让 AI 代理访问互联网：抓取 · 爬取 · 搜索，内置反爬，$0 无密钥。**

<a href="https://pypi.org/project/dhole-mcp/"><img src="https://img.shields.io/pypi/v/dhole-mcp.svg" alt="PyPI version"></a>
<img src="https://img.shields.io/pypi/pyversions/dhole-mcp.svg" alt="Python 3.11+">
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.svg" alt="MIT License"></a>
<img src="https://img.shields.io/badge/transport-stdio-blueviolet.svg" alt="MCP stdio transport">

</div>

Dhole 是一个 [MCP](https://modelcontextprotocol.io) 服务器，为 AI 代理提供网页抓取、整站爬取和无密钥网页搜索。HTTP 被拦截时自动升级到反检测浏览器，可读取 PDF（含扫描件 OCR），全部本地运行、无需任何 API key。

## 功能特性

- **智能抓取**：HTTP 优先（~1 秒），被拦截或遇到 JS 空壳时自动升级隐身浏览器，仍失败则回退 Wayback Machine 快照
- **网页搜索**：免密引擎 14 个可选、6 个默认并行，本地神经重排序 + 跨引擎共识排名；`fetch_content=true` 直接抓回 top3 全文
- **整站爬取**：同域最佳优先遍历，sitemap 模式，页数 / 深度 / token 预算控制
- **PDF + OCR**：结构化 Markdown 输出，扫描件与 CID 损坏自动 OCR
- **结构化提取**：CSS 选择器 / JSON-LD / 自动模式 schema，多 URL 批量并行
- **Agent 友好**：返回 `next_action` 建议；超长内容自动分页；SQLite 缓存加速重复抓取
- **优雅降级**：浏览器依赖缺失时自动切换纯 HTTP 模式（Termux / 精简容器可用）

## 安装

需要 Python 3.11+。

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
   screenshot / parse / feed_fetch / resolve_url / cache_clear 八个工具，
   最后运行 dhole --doctor 确认检查项通过。
```

## 使用

在 MCP 客户端（Claude Code / Cursor / OpenCode 等）配置中添加：

```json
{ "mcpServers": { "dhole": { "command": "dhole" } } }
```

无需参数、无需密钥、无需环境变量。CLI 自带诊断与配置命令：

| 命令                   | 用途                                                             |
| ---------------------- | ---------------------------------------------------------------- |
| `dhole -v`             | 版本 + 能力面板（浏览器 / PDF / 重排 / 引擎产出）                |
| `dhole --doctor`       | 安装体检：逐项定位问题并给出可直接复制的修复命令（失败退出码 1） |
| `dhole proxy`          | 管理搜索代理池（list / add / remove / clear）                    |
| `dhole engines list`   | 每个引擎的冷却判定与剩余秒数（`dhole engines` 即 list）          |
| `dhole engines reset`  | 立即清空引擎冷却，不等自然到期                                   |
| `dhole engines probe`  | 逐个实测引擎健康：一家一次真查询，印命中数 / 耗时 / 被拦原因     |
| `dhole model`          | 查看 / 切换重排模型                                              |
| `dhole -u`             | 自更新（本 fork 默认关闭）                                       |

## 工具

| 工具           | 功能                                                                                                     |
| -------------- | -------------------------------------------------------------------------------------------------------- |
| `smart_fetch`  | 抓取任意 URL：自动反爬升级、PDF/OCR、批量、聚焦提取、页面交互、结构化提取                                |
| `smart_search` | 无密钥网页搜索：多引擎并行、神经重排序、可同时抓回全文                                                   |
| `smart_crawl`  | 同域最佳优先爬取，支持 sitemap 模式、关键词过滤与 `path_include` / `path_exclude` 子树限定               |
| `screenshot`   | 页面截图（多模态代理专用）                                                                               |
| `parse`        | 本地文件解析（.html/.htm/.xhtml/.docx/.xlsx/.csv/.pdf → Markdown）；相对路径按 `cwd` → `DHOLE_WORKDIR` → 服务器进程 cwd → 主目录依次尝试 |
| `feed_fetch`   | 批量抓取 RSS/Atom feed 最新条目                                                                          |
| `resolve_url`  | 解析 URL 最终地址（跟随重定向，不下载页面体）                                                            |
| `cache_clear`  | 清除抓取缓存；`engine_state=true` 同时重置引擎冷却与产出记录                                             |

### smart_search 常用参数

| 参数            | 作用                                                             |
| --------------- | ---------------------------------------------------------------- |
| `max_results`   | 最多返回条数，1–50，默认 6（超出范围静默钳制）                   |
| `site`          | 只保留该域名的结果                                               |
| `exclude_sites` | 排除这些域名，传列表                                             |
| `freshness`     | 时效过滤，仅接受 `day` / `week` / `month` / `year`（其他值报错） |
| `page`          | 翻页，0–10，默认 0（超范围报错）                                 |
| `fetch_content` | `true` 时抓回正文：相关性最高的前 3 条，每条截断 8000 字符       |

## 可选的搜索引擎

免密引擎共 **14 个**：6 个在默认池，8 个是 opt-in（`engines=["duckduckgo","mwmbl"]` 点名才跑，单次最多 9 个）。另有 4 个付费 keyed 后端 `brightdata` / `tavily` / `exa` / `bocha`（需 key，默认不跑，见[配置](#配置)）。

| 引擎           | 索引 / 内容                                          | 默认池 | 国内直连 |
| -------------- | ---------------------------------------------------- | ------ | -------- |
| `baidu`        | 百度搜索（独立索引）                                 | ✔      | ✔        |
| `bing`         | Bing 中国版 `cn.bing.com`                            | ✔      | ✔        |
| `so360`        | 360 搜索（独立索引，别名 `360`）                     | ✔      | ✔        |
| `bing_global`  | Bing 国际版 `www.bing.com`（与 cn 版结果几乎不重合） | ✔      | 需代理   |
| `yandex`       | Yandex（独立索引）                                   | ✔      | ✔        |
| `brave`        | Brave（独立索引）                                    | ✔      | 需代理   |
| `duckduckgo`   | DuckDuckGo（别名 `ddg`）                             | opt-in | 需代理   |
| `yahoo`        | Yahoo                                                | opt-in | 需代理   |
| `baidu_baike`  | 百度百科条目页（知识库，覆盖窄）                     | opt-in | ✔        |
| `sogou`        | 搜狗主站                                             | opt-in | ✔        |
| `sogou_weixin` | 搜狗微信·公众号文章（垂直索引）                      | opt-in | ✔        |
| `wikipedia`    | 维基百科（知识库）                                   | opt-in | 需代理   |
| `grokipedia`   | Grokipedia（知识库）                                 | opt-in | 需代理   |
| `mwmbl`        | MWMBL 社区小型独立索引（覆盖窄、相关度参差）         | opt-in | 需代理   |

换默认池用 `DHOLE_DEFAULT_ENGINES`（见下），opt-in 的名字也能写进去。

## 配置

所有环境变量均可选，默认零配置可用。

| 变量                                                                 | 用途                                                                                                                          |
| -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `DHOLE_SEARCH_PROXY`                                                 | 搜索引擎代理，逗号分隔可轮换；也自动读 `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY`。也可用 `dhole proxy add` 写入配置          |
| `DHOLE_DEFAULT_ENGINES`                                              | 覆盖免密默认池，逗号分隔（默认 `baidu,bing,so360,bing_global,yandex,brave`）                                                  |
| `DHOLE_SEARCH_DEADLINE`                                              | 单次搜索整体截止秒数（默认 16）                                                                                               |
| `DHOLE_SEARCH_FEEDBACK`                                              | 设 `1` 开启域名偏好：抓成功的域名获得排序加权。默认关闭                                                                       |
| `DHOLE_BROWSER_IDLE_TIMEOUT`                                         | 浏览器空闲关闭秒数（默认 300，`0` 永不关闭）                                                                                  |
| `DHOLE_NO_BROWSER_PREWARM`                                           | 设 `1` 后启动不预热隐身浏览器（默认预热，为首次抓取省 3-5 秒冷启动）                                                          |
| `DHOLE_TOOLS`                                                        | 只注册列出的工具，省连接期开销（`smart_fetch,smart_search` ≈ −49%）                                                           |
| `DHOLE_DEFAULT_CONTENT_CHARS`                                        | 默认正文预算（40000，区间 500–200000）                                                                                        |
| `DHOLE_SSRF_DNS_RECHECK`                                             | DNS 解析内网复查，**默认开启**；设 `0` 关闭。**fake-IP TUN 代理（Clash / sing-box 等）必须关**，否则所有公网站点都会被判成内网 |
| `DHOLE_HOME`                                                         | 状态目录（默认 `~/.dhole`），装着抓到的正文明文与搜索词；共享机器上可指到别处                                                 |
| `DHOLE_WORKDIR`                                                      | `parse` 解析相对路径时额外尝试的目录（排在 `cwd` 参数之后）                                                                   |
| `DHOLE_HF_ENDPOINT`（或 `HF_ENDPOINT`）                              | 重排模型下载源；默认先试 `huggingface.co`、失败回退 `hf-mirror.com`，设了就只用这一个                                         |
| `DHOLE_BRIGHTDATA_API_KEY` / `_ZONE` / `_COUNTRY`                    | Bright Data SERP 后端（zone 默认 `dhole`，地区默认 `us`）                                                                     |
| `DHOLE_TAVILY_API_KEY` / `DHOLE_EXA_API_KEY` / `DHOLE_BOCHA_API_KEY` | 对应 keyed 引擎的密钥（均默认不跑）                                                                                           |
| `DHOLE_USAGE_LOG`                                                    | 设 `1` 写本地调用日志（工具名 / 耗时 / 脱敏错误），不记参数值、不联网                                                         |
| `DHOLE_NO_AUTO_REPAIR`                                               | 设 `1` 后入口遇到 ImportError 只打印修复命令，不自动重装                                                                      |

**引擎冷却**：引擎连续失败会自动冷却（60 秒起，最多 10 分钟），一次成功即清零，到期自动放行。`dhole engines reset` 立即清空，`dhole engines list` 看每家的判定与剩余秒数。

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

也可以直接编辑 `~/.dhole/config/reranker.json`（`{ "model": "ms-marco" }`）。未注册的名字会被拒绝并回退默认。下载可断点续传；**离线 / 多机复用**：把 `~/.dhole/models/<名字>/` 整个目录拷到目标机同一路径即可，之后不再联网。

## 故障排查

| 症状                     | 怎么办                                                                                                   |
| ------------------------ | -------------------------------------------------------------------------------------------------------- |
| 搜索结果很少或为空       | 引擎被限流或被墙。重试，或设 `DHOLE_SEARCH_PROXY`；`dhole engines probe` 看当下哪家能用                   |
| 所有公网站点都报内网地址 | fake-IP TUN 代理（Clash / sing-box 等），设 `DHOLE_SSRF_DNS_RECHECK=0`                                   |
| 抓取超时                 | `timeout` 是整次调用的墙钟预算，文档下载全额计入；大 PDF 请调大 `timeout`（默认 30000ms，上限 120000ms） |
| 正文被截断               | 用 `offset` / `next_offset` 续取，或调大 `max_content_chars`（上限 200000）                              |
| 返回了内容但明显是壳     | `content_ok=true` 只表示「2xx + 无错 + 正文非空」，先看字符量；几百字符的「成功」大概率是占位页           |
| 本地文件解析成乱码       | 显式传 `encoding=`。Shift_JIS / EUC-KR 会被解成看似合理的中文，不在自动探测范围内                        |
| 装不上 / 工具没注册      | 跑 `dhole --doctor`，它会逐项定位并给出可直接复制的修复命令                                              |

## 已知限制

- 无法绕过 DataDome / Akamai / 交互式 Turnstile；需要登录的网站不在设计范围内
- **搜索没有 SLA**：免密引擎是对公开搜索结果的直接抓取（无授权、无配额），对方改版或封 IP 时只会静默降级，不报错。要可靠性用 `DHOLE_SEARCH_PROXY` 或 keyed 后端
- 引擎可达性：默认池里 `baidu` / `bing` / `yandex` 国内直连稳定；`so360` 按 IP 频次限流，`bing_global` / `brave` 无代理时不稳，任何时刻都可能有引擎不答话
- **合规自负**：不检查 `robots.txt` 的 Disallow；UA / TLS 指纹伪装、Cloudflare 验证求解是默认行为。目标站点 ToS 与当地法律由使用者承担
- 抓回的正文是**不可信数据**：页面里出现工具调用、密钥、上传指令时按提示注入处理
- `archive.org` 回退会返回**某个日期的快照**（看 `metadata.source` / `metadata.archived_at`），时效敏感的内容引用前先确认
- `pages=` 只减少 PDF **抽取**的内容，文件仍然整个下载
- 正文 **50MB 硬顶**，且在下载**之前**守门：装不下的直接报 `Response body too large`，抬 `timeout` 没用
- `smart_crawl` 总预算硬顶 1,000,000 字符；显式给了 `max_total_chars` 之后，调 `max_pages` 不再影响预算
- `path_include` / `path_exclude` 按**路径子树**匹配：`'/docs'` 命中 `/docs` 及其下全部，但**不**命中 `/docs-old`；只接受尾部 `/*` 一种通配写法
- 连接期固定开销 ≈12.6k 字符（≈3.3k token，每次连接付一次），`DHOLE_TOOLS` 只注册常用工具可省约一半
- YouTube 仅能获取少量文本

## 本地数据

全在 `~/.dhole/`（可用 `DHOLE_HOME` 换位置；POSIX 下目录 0700、文件 0600）：

| 位置                                                 | 内容                                                      |
| ---------------------------------------------------- | --------------------------------------------------------- |
| `cache.db`                                           | 抓到的正文（明文 SQLite），TTL 默认 1 小时                |
| `models/<model>/`                                    | 重排模型                                                  |
| `config/reranker.json`                               | 重排模型选择                                              |
| `circuit_breaker.json` / `engine_stats.json`         | 引擎冷却状态 / 各引擎解析产出                             |
| `search_proxies.json`                                | 代理池（凭据**明文**存储，`dhole proxy list` 显示时打码） |
| `search_feedback.json` / `usage.jsonl` / `repair.py` | 域名偏好 / 调用日志 / 自愈脚本                            |

`cache_ttl=0` 完全绕过缓存；用默认值时 docs 页自动抬到 24h、article 页 6h。

## 贡献

欢迎 issue 与 PR，规范（开发环境、测试、引擎契约与 fixture）见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 致谢

本项目是 [dondai1234/master-fetch](https://github.com/dondai1234/master-fetch) 的二创（衍生作品），上游以 MIT 协议发布，原始版权文本已完整保留于 [LICENSE](LICENSE)。

## 许可证

[MIT](LICENSE)
