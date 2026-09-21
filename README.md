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

- **智能抓取**：HTTP 优先（~1 秒），被拦截或遇到 JS 空壳时自动升级 Patchright 隐身浏览器并求解 Cloudflare 验证；全部失败时回退 Wayback Machine 快照
- **网页搜索**：多引擎并行（bing / duckduckgo / brave / yahoo / yandex 等），本地 ONNX 神经重排序，跨引擎共识排名；`fetch_content=true` 直接抓回 top3 全文。重排模型默认跨语言（含中文），可用 `dhole model use` 或 `~/.dhole/config/reranker.json` 换成英文优先的旧模型；缺依赖或模型没下载时自动退回共识排序，不报错——`dhole -v` 会告诉你现在是哪种
- **整站爬取**：同域最佳优先遍历，内容自适应提取，sitemap 模式，页数 / 深度 / token 预算控制
- **PDF + OCR**：结构化 Markdown 输出，扫描件与 CID 损坏自动 OCR
- **结构化提取**：CSS 选择器 / JSON-LD / 自动模式 schema，支持多 URL 批量并行
- **Agent 友好**：每次调用返回 `next_action` 建议与来源分类；超长内容自动分页；SQLite 缓存加速重复抓取
- **优雅降级**：浏览器依赖缺失时自动切换纯 HTTP 模式（Termux / 精简容器可用）

## 安装

需要 Python 3.11+。

```bash
pip install "dhole-mcp[all]"    # 完整版（含反爬浏览器、PDF/OCR、解析器）
playwright install chromium      # 反检测浏览器引擎（~150MB，完整版需要）
```

精简版（纯 HTTP + 搜索，无浏览器依赖）：

```bash
pip install dhole-mcp
```

从源码安装：

```bash
git clone https://github.com/ouli-1242/dhole-mcp.git
cd dhole-mcp
pip install .[all]
playwright install chromium
```

卸载：

```bash
pip uninstall dhole-mcp
rm -rf ~/.dhole                 # 全部运行时数据：缓存 / 模型 / 状态（Windows: rd /s /q %USERPROFILE%\.dhole）
```

## 使用

在 MCP 客户端（Claude Code / Cursor / OpenCode 等）配置中添加：

```json
{ "mcpServers": { "dhole": { "command": "dhole" } } }
```

无需参数、无需密钥、无需环境变量。CLI 自带诊断命令：

```bash
dhole -v    # 查看版本 + 更新状态
dhole -u    # 自更新（本 fork 默认关闭）
```

## 工具

| 工具 | 功能 |
|------|------|
| `smart_fetch` | 抓取任意 URL：自动反爬升级、PDF/OCR、批量、聚焦提取、页面交互、结构化提取 |
| `smart_search` | 无密钥网页搜索：多引擎并行、神经重排序、可同时抓回全文 |
| `smart_crawl` | 同域最佳优先爬取，支持 sitemap 模式与关键词过滤 |
| `screenshot` | 页面截图（多模态代理专用） |
| `parse` | 本地文件解析（.html/.docx/.xlsx/.csv → Markdown） |
| `feed_fetch` | 批量抓取 RSS/Atom feed 最新条目 |
| `resolve_url` | 解析 URL 最终地址（跟随重定向，不下载页面体） |
| `cache_clear` | 清除抓取缓存 |

### smart_search 常用参数

| 参数 | 作用 |
|------|------|
| `max_results` | 最多返回条数，1–50，默认 6（超出范围**静默钳制**，不报错） |
| `site` | 只保留该域名的结果（按最终 URL 的域名匹配） |
| `exclude_sites` | 排除这些域名，传列表 |
| `freshness` | 时效过滤，仅接受 `day` / `week` / `month` / `year`，其他值**直接报错** |
| `page` | 翻页，0–10，默认 0，超范围**直接报错** |
| `fetch_content` | `true` 时自动抓回正文：取相关性 `high` 的前 3 条（无 `high` 则退化为前 3 条），每条截断 8000 字符，并按 `focus=query` 做聚焦提取 |

## 配置

所有环境变量均可选，默认零配置可用：

| 变量 | 用途 |
|------|------|
| `DHOLE_SEARCH_PROXY` | 搜索引擎代理，逗号分隔可轮换（也自动读取 `HTTPS_PROXY` 等） |
| `DHOLE_BROWSER_IDLE_TIMEOUT` | 浏览器空闲关闭秒数（默认 300，`0` 永不关闭） |
| `DHOLE_SEARCH_DEADLINE` | 单次搜索整体截止秒数（默认 16） |
| `DHOLE_BRIGHTDATA_API_KEY` | 启用 Bright Data SERP 后端 —— **唯一需要密钥的搜索引擎**，行为见下节 |
| `DHOLE_BRIGHTDATA_ZONE` | Bright Data zone 名（默认 `dhole`） |
| `DHOLE_BRIGHTDATA_COUNTRY` | Google 结果地区（默认 `us`） |
| `DHOLE_SSRF_DNS_RECHECK` | DNS 解析内网复查，**默认开启**；设 `0` 关闭。解析到内网段即拒绝（错误信息里带上开关名）。hosts 文件里钉住的域名按**钉到的值**放行（阻断/mirror/分流是本机用户的故意决定，攻击者改不了你的 hosts）：钉到 `127.0.0.1`/`172.16.x` 这类地址仍放行，钉到 `0.0.0.0`/`::` 这种黑洞占位则拒绝——屏蔽类 hosts 会把上千个域名钉到 `0.0.0.0`，而它作为连接目标在 Windows/macOS 上等同回环 |
| `DHOLE_UPDATE_PACKAGE` | 自更新目标发行名（发布自己的发行版后设置以启用） |
| `DHOLE_UPDATE_INDEX_URL` | 自更新/自愈时传给 pip 的 `--index-url`（不设则用 pip 默认源） |
| `DHOLE_TAVILY_API_KEY` / `DHOLE_EXA_API_KEY` / `DHOLE_BOCHA_API_KEY` | 对应 keyed 引擎的密钥（均默认不跑，`engines=` 点名才调用） |
| `DHOLE_DEFAULT_ENGINES` | 覆盖免密默认池，逗号分隔（如 `bing,yandex,sogou_weixin`；被墙引擎不再每轮陪跑）。未设用上游默认 5 个 |
| `DHOLE_SEARCH_FEEDBACK` | 设 `1` 开启隐式域名偏好：`fetch_content` 抓成功的域名**永久** +0.05 排序加权（落盘 `~/.dhole/search_feedback.json`，上限 500 域）。默认关闭——它按「抓到过」而非「有用」改写跨引擎共识排序 |
| `DHOLE_USAGE_LOG` | 设 `1`（或一个路径）写本地调用日志（JSONL）：工具名、成功与否、耗时、脱敏后的错误。**只记这些，不记参数值**，也不联网上传。用来回答「我的客户端到底有没有调用 dhole」 |
| `DHOLE_NO_AUTO_REPAIR` | 设 `1` 后，`dhole` 入口遇到 ImportError 不再自动 `pip install --force-reinstall`（只打印修复命令）。默认开启自动修复；重装目标**钉在当前已装版本**（读不到版本元数据时才退回裸包名） |
| `DHOLE_HOME` | 状态目录位置（默认 `~/.dhole`）。这里装着**抓到的正文明文**、搜索词与模型；共享机器上可指到别处。POSIX 下目录建为 0700、状态文件 0600；**Windows 上 chmod 基本无效（NTFS ACL 说了算），那边的实际手段就是这个变量** |
| `DHOLE_HF_ENDPOINT`（或 `HF_ENDPOINT`） | 神经重排模型的下载源。默认先试 `huggingface.co`、失败自动回退 `hf-mirror.com`（revision 固定；设了就只用这一个）。回退改的是**从哪取字节**，不改取到什么——两个端点是否真给同一份字节，取决于镜像 fidelity，本地默认只做「自记哈希防损坏」；注册表里填入发布方 sha256 后才做真实性校验（不符即拒用），当前三个模型该字段为空 |
| `DHOLE_DEFAULT_ENGINES` 之外 | 重排模型选择见下节（配置文件，非环境变量） |

免密引擎连续 3 次连接失败（DNS/拒连/超时，通常是被墙）会自动冷却 10 分钟并持久化，期间不再参与搜索；任何一次成功即清零。被反爬封（403/503）的冷却仍是 60 秒。 |

### Keyed 搜索后端（brightdata / tavily / exa / bocha）

设了 `DHOLE_BRIGHTDATA_API_KEY` 即启用，没有额外开关。它向 `api.brightdata.com/request`
请求 `google.com/search` 的结果页（`data_format=parsed_light`），与免费引擎**并行**执行。

- **按次消耗，且默认不跑**：keyed 引擎只有 `engines=` 显式点名才执行（命中搜索缓存则连调用都不发）
- **`engines=["brightdata"]` 可单独选它**：只跑付费后端；未配 key 时报错直指 `DHOLE_BRIGHTDATA_API_KEY`，不会把你引去查代理。可选引擎名全部来自 `_DHOLE_TO_BACKEND` 这一张表 —— 8 个免密的加它
- **不拉高共识门槛**：`min_engines = min(3, 免费引擎数)` 只按免费引擎计算，它不计入
- **提前返回时不取消**：免费引擎凑够结果触发早退时，其余任务被 cancel，但 Bright Data 会等它跑完，避免已花出去的配额白花
- **失败大多静默**：非 200 或任何异常都返回空列表、只记 debug 日志，不影响本次搜索结果。**401/403 除外** —— key 错误或过期会抛 `BrightDataAuthError`，在 `status` 里显示为 `error:BrightDataAuthError`，不会伪装成「没有结果」；它也不触发熔断（key 错了冷却 60 秒毫无意义）
- 单次 HTTP 超时取 `max(DHOLE_SEARCH_DEADLINE, 20)`：默认（deadline 16 秒）下仍是 20 秒 —— SERP 渲染要时间，且配额在请求发出那一刻就已花掉；但你调高 deadline 它会跟着涨
- 失败响应体写日志前会先脱敏。注意 `security.redact_api_key()` 的正则只认 `sk-`/`pk-`/`api_key-` 前缀和带凭据的代理 URL，**盖不住 Bright Data 自己的 key 形状**，所以额外拿已知 key 做了定向替换

### 重排模型选择（默认跨语言，含中文）

搜索结果的相关性排序由一个本地 cross-encoder 负责——它回答的是「这条结果跟你的问题真有关吗」，这是各个引擎自己的排名给不了的信息。模型不进 wheel，首次神经搜索时下载到 `~/.dhole/models/<名字>/`。

**默认用 `bge-zh`**（BAAI 双语重排器 `bge-reranker-base` 的 int8 版，`Xenova/bge-reranker-base`，中英双语训练，~279MB）：中文 ranking 比多语蒸馏模型更好，体积也小 38%。想更重/更准可以换 `zh-full`（跨语言 MiniLM fp32，~450MB），英文优先用 `ms-marco`（~91MB）：

```bash
dhole model                    # 列出可用模型 + 当前生效的那个
dhole model use ms-marco       # 切到英文 MS MARCO MiniLM（~90MB）
dhole model use bge-zh         # 切回默认
```

也可以直接编辑配置文件（与 CLI 写的是同一个文件，CLI 只是便捷入口）：

```json
// ~/.dhole/config/reranker.json
{ "model": "ms-marco" }
```

| 名字 | 模型 | 语言 | 体积 |
|------|------|------|------|
| `bge-zh`（默认） | `Xenova/bge-reranker-base`（BAAI，int8） | 中英双语 | ~279MB |
| `zh-full` | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`（fp32） | 跨语言（含中文/多语） | ~450MB |
| `ms-marco` | `cross-encoder/ms-marco-MiniLM-L-6-v2` | 英文优先 | ~91MB |

两边都是 Apache-2.0、revision 固定（换了端点也不会换字节）。写明未注册的名字会被**拒绝并回退默认**并告警——不会静默换个模型给结果打分。模型各自独立目录，切换不会覆盖对方；14.x 已下过的英文模型目录会被自动改名保留，不会重下。下载**可断点续传**（中断不重来）；`dhole -v` 会报告当前生效的模型。int8 量化对重排质量影响很小（BAAI 原厂量化），但如果你要极限精度，`zh-full` 的 fp32 随时可切。

## 边界、状态与前提

这一节说明「$0 无密钥」到底意味着什么，以及它会在这台机器上留下什么——README 的其余部分只谈能力。

**搜索能力的来源。** 免密引擎是**对公开搜索结果的直接抓取**：与那些索引之间没有授权、没有配额、没有 SLA。所以「免费」的确切含义是「用不受许可的读取替代付费授权」，代价由可用性承担——对方改版、封 IP 或收紧反爬时会直接反映为结果变少或变差，而**只表现为静默降级**（熔断/冷却/退回共识排序），不会报错。需要可靠性的场景请用 `DHOLE_SEARCH_PROXY` 或 keyed 后端（brightdata / tavily / exa / bocha）——那才是可持续的那条路。

**抓取的合规边界。** 抓取与爬取**不检查 `robots.txt` 的 Disallow**（只在 sitemap 发现时读它的 `Sitemap:` 指令）；HTTP 层的 UA 与 TLS 指纹是伪装的，被拦截时会升级到隐身浏览器求解 Cloudflare 验证。目标站点的 ToS 与所在司法辖区的法律由使用者自负。

**SSRF 防护覆盖到哪里（以及到不了哪里）。** 前提是本工具跑在**自己的机器上、单用户使用**；下面的取舍都建立在这个前提上，共享机器上请自行收紧。

- **HTTP 层**：入口 URL 与**每一跳重定向**都过 `validate_url` —— scheme 白名单、IPv4/IPv6 字面量、八进制/十六进制/十进制/短写等 curl 变体记法、IPv4-mapped IPv6、云元数据主机名、DNS rebinding 服务名，以及默认开启的「域名解析到内网即拒」。
- **没有 IP 钉死**：校验用一次 DNS、连接时 libcurl 再解析一次，中间存在 TOCTOU 窗口（primp 不提供 force-resolve 接口）。所以这层是**纵深防御**，不是一道边界。`DHOLE_SSRF_DNS_RECHECK=0` 会把它关掉。
- **hosts 文件钉位按钉到的值放行**：`127.0.0.1`/`172.16.x` 这类本机开发覆盖仍然放行（那是用户显式决定），`0.0.0.0`/`::` 这类屏蔽用黑洞**不再**放行——它在 Windows/macOS 上作为连接目标等同回环。
- **隐身浏览器层**：入口 URL 校验过，但**浏览器内部跟随的重定向、子资源请求、页面 JS 自己发起的 fetch 都不经过这道校验**（Chromium 的行为我们无法在不破坏 Cloudflare 求解的前提下拦截）。页面内容不会经这些请求回流给你，因此残余是一个**盲打式**的内网可达面（本机能连上某端口），而非数据外泄。想覆盖它需要把浏览器套一层本地过滤代理——那与「零配置」直接冲突，且回归面正落在 Cloudflare 求解这条唯一的能力上，故**未做**。
- **TLS**：浏览器层为通过真实站点的残缺证书链而设 `ignore_https_errors`，代价是同网络位置的中间人可以给这一层伪造内容。HTTP 层不做此让步。
- 直连可达性探测（TCP preflight）不再对内网目标发起连接，其返回类别也与「真的不可达」同形，不留端口指纹。

**状态文件权限**：POSIX 下 `~/.dhole`（或 `DHOLE_HOME`）建为 0700、状态文件 0600；Windows 上 chmod 基本无效（NTFS ACL 说了算），那边的手段是换位置 + 文件系统本身的权限。缓存内容始终是明文。

**本机会留下什么。** 全部都在一个目录 `~/.dhole/`（14.3 之前缓存与模型在 `~/.dhole_mcp_cache/`，首次使用时自动搬移合并，不会重下 90MB 模型；整个目录可用 `DHOLE_HOME` 换位置，换位置后旧目录仍会自动迁过去）：

| 位置 | 内容 | 何时产生 |
|------|------|----------|
| `~/.dhole/cache.db` | 抓到的正文（明文 SQLite）。**按请求上下文分区**：带 cookies / 自定义头 / UA / 代理 / PDF 口令或改动内容开关的抓取，不会与匿名请求共享缓存条目（口令这一维早前缺席，用 `password=` 解出的正文可能被匿名请求复读；受影响的是 PDF 行，首次打开时清一次，不动其余缓存） | 每次成功抓取 |
| `~/.dhole/models/<model>/` | 神经重排序模型（3 个文件 + 一份 `model.sha256`，来自 HuggingFace 固定 revision）。默认 `bge-zh`（中英双语 int8，~279MB），可换 `zh-full`（~450MB）/ `ms-marco`（英文，~91MB） | 首次神经搜索时下载 |
| `~/.dhole/config/reranker.json` | 重排模型选择（`{"model": "..."}`），`dhole model use` 也写这里 | 切换模型时 |
| `~/.dhole/circuit_breaker.json` | 引擎熔断/冷却状态 | 引擎被限速/被墙时 |
| `~/.dhole/engine_stats.json` | 每个引擎最近一轮的解析产出（容器条数 / 可用条数 / 均值），用来把"引擎答了但解析出 0 条"这种静默降级变可见；`dhole -v` 读它 | 每次真实搜索（至多 60s 写一次） |
| `~/.dhole/search_feedback.json` | 隐式域名偏好 | 仅 `DHOLE_SEARCH_FEEDBACK=1` |
| `~/.dhole/usage.jsonl` | 本地调用日志（工具名/结果/耗时/脱敏错误，无参数值） | 仅 `DHOLE_USAGE_LOG` 开启 |
| `~/.dhole/repair.py` | 自愈脚本（跟随 `DHOLE_UPDATE_PACKAGE` / `DHOLE_UPDATE_INDEX_URL`） | 首次自愈时写入 |

缓存 TTL 默认 1 小时；当 `cache_ttl` 用默认值时，docs 页自动抬到 24h、article 页 6h。`cache_ttl=0` 完全绕过缓存。

**给 agent 的安全提示。** 抓回来的页面正文是**不可信数据**：指令里已明确要求模型不要把页面里的内容当指令执行，但那是提示、不是强制。任何"页面告诉我该做什么"的场景（尤其是页面里出现工具调用、密钥、上传指令时）都应按提示注入处理。同理，`is_official` 只对 gov / edu / github 这类**第三方注册不走的命名空间**为真；`docs.*` / `developer.*` 只是「这个站给自己的文档起了个 docs 子域」，谁都能这么做，不构成权威。

## 已知限制

- 无法绕过 DataDome / Akamai / 交互式 Turnstile；需要登录的网站不在设计范围内
- duckduckgo / brave / yahoo 需要 VPN 可达（bing / yandex 国内直连）
- 搜索引擎限速时熔断器自动冷却 60 秒，重度使用建议配置代理
- YouTube 仅能获取少量文本
- 页面正文里的指令可能试图操纵 agent（提示注入）：dhole 会提示模型把正文当数据，但不做内容净化或拦截
- 引擎存活状况**不做主动巡检**（不发后台真实请求），但每次真实搜索都会记下各引擎的解析产出（容器数/可用数）：`dhole -v` 的 `engine yield` 行会告诉你哪个引擎这轮 0 产出、以及是「被墙」还是「答了但解析不出来」。默认池 5 个引擎中 2 个有真页面契约测试（bing 两种版面 + yandex），其余因本机网络不可达尚未抓取——补齐方式见 CONTRIBUTING

## 贡献

欢迎 issue 与 PR，规范见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 致谢

本项目是 [dondai1234/master-fetch](https://github.com/dondai1234/master-fetch) 的二创（衍生作品），上游以 MIT 协议发布，原始版权文本已完整保留于 [LICENSE](LICENSE)。`src/dhole_mcp/search_metasearch.py` 派生自 [ddgs](https://github.com/deedy5/ddgs)，声明见 [NOTICE.ddgs.txt](NOTICE.ddgs.txt)。

## 许可证

[MIT](LICENSE)
