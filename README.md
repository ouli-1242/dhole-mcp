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
- **网页搜索**：并行抓取多个公开搜索引擎（免密 14 个可选、6 个默认，见[可选的搜索引擎](#可选的搜索引擎)），本地 ONNX 神经重排序 + 跨引擎共识排名，`fetch_content=true` 直接抓回 top3 全文
- **整站爬取**：同域最佳优先遍历，sitemap 模式，页数 / 深度 / token 预算控制
- **PDF + OCR**：结构化 Markdown 输出，扫描件与 CID 损坏自动 OCR
- **结构化提取**：CSS 选择器 / JSON-LD / 自动模式 schema，多 URL 批量并行
- **Agent 友好**：返回 `next_action` 建议与来源分类；超长内容自动分页；SQLite 缓存加速重复抓取
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

| 命令             | 用途                                                             |
| ---------------- | ---------------------------------------------------------------- |
| `dhole -v`       | 版本 + 能力面板（浏览器 / PDF / 重排 / 引擎产出）                |
| `dhole --doctor` | 安装体检：逐项定位问题并给出可直接复制的修复命令（失败退出码 1） |
| `dhole proxy`    | 管理搜索代理池（list / add / remove / clear）                    |
| `dhole engines`  | 查看 / 重置引擎健康状态（list / reset）                          |
| `dhole model`    | 查看 / 切换重排模型                                              |
| `dhole -u`       | 自更新（本 fork 默认关闭）                                       |

## 工具

| 工具           | 功能                                                                                                                                          |
| -------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `smart_fetch`  | 抓取任意 URL：自动反爬升级、PDF/OCR、批量、聚焦提取、页面交互、结构化提取                                                                     |
| `smart_search` | 无密钥网页搜索：多引擎并行、神经重排序、可同时抓回全文                                                                                        |
| `smart_crawl`  | 同域最佳优先爬取，支持 sitemap 模式、关键词过滤与 `path_include`/`path_exclude` 子树限定（`'/docs'` 含 `/docs` 及其下全部，**不含** `/docs-old`）                                                                                               |
| `screenshot`   | 页面截图（多模态代理专用）                                                                                                                    |
| `parse`        | 本地文件解析（.html/.htm/.xhtml/.docx/.xlsx/.csv/.pdf → Markdown）；`.html`/`.csv` 按字节探测字符集（`encoding=` 可覆盖）；相对路径按 `cwd` 参数 → `DHOLE_WORKDIR` → 服务器进程 cwd → 主目录依次尝试 |
| `feed_fetch`   | 批量抓取 RSS/Atom feed 最新条目                                                                                                               |
| `resolve_url`  | 解析 URL 最终地址（跟随重定向，不下载页面体）                                                                                                 |
| `cache_clear`  | 清除抓取缓存；`engine_state=true` 同时重置引擎冷却与产出记录，响应回报 `engine_health`                                                        |

### smart_search 常用参数

| 参数            | 作用                                                                                                                             |
| --------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `max_results`   | 最多返回条数，1–50，默认 6（超出范围**静默钳制**，不报错）                                                                       |
| `site`          | 只保留该域名的结果（按最终 URL 的域名匹配）                                                                                      |
| `exclude_sites` | 排除这些域名，传列表                                                                                                             |
| `freshness`     | 时效过滤，仅接受 `day` / `week` / `month` / `year`，其他值**直接报错**                                                           |
| `page`          | 翻页，0–10，默认 0，超范围**直接报错**                                                                                           |
| `fetch_content` | `true` 时自动抓回正文：取相关性 `high` 的前 3 条（无 `high` 则退化为前 3 条），每条截断 8000 字符，并按 `focus=query` 做聚焦提取 |

## 可选的搜索引擎

免密引擎共 **14 个**：6 个在默认池（每轮都跑），8 个是 opt-in（`engines=["bing_global","mwmbl"]` 点名才跑，单次最多 9 个）。

| 引擎           | 索引 / 内容                                                     | 默认池 | 国内直连 |
| -------------- | --------------------------------------------------------------- | ------ | -------- |
| `baidu`        | 百度搜索（独立索引）                                            | ✔      | ✔        |
| `bing`         | Bing 中国版 `cn.bing.com`（bing 家族）                          | ✔      | ✔        |
| `yandex`       | Yandex（独立索引）                                              | ✔      | ✔        |
| `brave`        | Brave（独立索引）                                               | ✔      | 需代理   |
| `duckduckgo`   | DuckDuckGo（bing 家族，别名 `ddg`）                             | ✔      | 需代理   |
| `yahoo`        | Yahoo（bing 家族）                                              | ✔      | 需代理   |
| `baidu_baike`  | 百度百科条目页（知识库，覆盖窄）                                | opt-in | ✔        |
| `bing_global`  | Bing 国际版 `www.bing.com`（bing 家族；与 cn 版结果几乎不重合） | opt-in | 需代理   |
| `so360`        | 360 搜索（独立索引，别名 `360`）                                | opt-in | ✔        |
| `sogou`        | 搜狗主站（sogou 家族）                                          | opt-in | ✔        |
| `sogou_weixin` | 搜狗微信·公众号文章（垂直索引）                                 | opt-in | ✔        |
| `wikipedia`    | 维基百科（知识库）                                              | opt-in | 需代理   |
| `grokipedia`   | Grokipedia（知识库）                                            | opt-in | 需代理   |
| `mwmbl`        | MWMBL 社区小型独立索引（覆盖窄、相关度参差）                    | opt-in | 需代理   |

共识按**索引家族**算，不按入口算：bing 家族有 4 个入口（`bing` / `bing_global` / `duckduckgo` / `yahoo`），同一 URL 被它们同时返回仍只算一个家族 —— 所以默认池 6 个引擎的共识上限是 **"4 of 4"**（baidu / bing / brave / yandex）。另有 4 个付费 keyed 后端 `brightdata` / `tavily` / `exa` / `bocha`（需 key，见下）；`DHOLE_DEFAULT_ENGINES` 可以改默认池，opt-in 名字也能写进去。

## 配置

所有环境变量均可选，默认零配置可用。

| 变量                                                                 | 用途                                                                                                                                                                                                                                               |
| -------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `DHOLE_SEARCH_PROXY`                                                 | 搜索引擎代理，逗号分隔可轮换；也自动读取 `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` 作为单代理回退（Windows 上环境变量大小写不敏感，沙箱里的小写 `https_proxy` 同样会被采纳）。也可用 `dhole proxy add` 写入配置文件，两种来源合并去重、上限 20 个 |
| `DHOLE_DEFAULT_ENGINES`                                              | 覆盖免密默认池，逗号分隔（默认 `baidu,bing,yandex,brave,duckduckgo,yahoo`）；候选引擎见[可选的搜索引擎](#可选的搜索引擎)                                                                                                                           |
| `DHOLE_SEARCH_DEADLINE`                                              | 单次搜索整体截止秒数（默认 16）                                                                                                                                                                                                                    |
| `DHOLE_SEARCH_FEEDBACK`                                              | 设 `1` 开启隐式域名偏好：抓成功的域名永久 +0.05 排序加权。默认关闭——它按「抓到过」而非「有用」改写共识排序                                                                                                                                         |
| `DHOLE_BROWSER_IDLE_TIMEOUT`                                         | 浏览器空闲关闭秒数（默认 300，`0` 永不关闭）                                                                                                                                                                                                       |
| `DHOLE_NO_BROWSER_PREWARM`                                           | 设 `1` 后启动不预热隐身浏览器（默认预热，为首次 stealthy 抓取省 3-5 秒冷启动）                                                                                                                                                                     |
| `DHOLE_SSRF_DNS_RECHECK`                                             | DNS 解析内网复查，**默认开启**；设 `0` 关闭（详见「边界与前提」）                                                                                                                                                                                  |
| `DHOLE_HOME`                                                         | 状态目录位置（默认 `~/.dhole`）。这里装着**抓到的正文明文**与搜索词，共享机器上可指到别处。POSIX 下建为 0700 / 文件 0600                                                                                                                           |
| `DHOLE_WORKDIR`                                                      | `parse` 解析相对路径时额外尝试的目录（排在 `cwd` 参数之后）。MCP 宿主常把安装目录当 cwd，此时靠它指向项目目录                                                                                                                                      |
| `DHOLE_HF_ENDPOINT`（或 `HF_ENDPOINT`）                              | 重排模型下载源。默认先试 `huggingface.co`、失败回退 `hf-mirror.com`；设了就只用这一个                                                                                                                                                              |
| `DHOLE_BRIGHTDATA_API_KEY` / `_ZONE` / `_COUNTRY`                    | Bright Data SERP 后端（zone 默认 `dhole`，地区默认 `us`）                                                                                                                                                                                          |
| `DHOLE_TAVILY_API_KEY` / `DHOLE_EXA_API_KEY` / `DHOLE_BOCHA_API_KEY` | 对应 keyed 引擎的密钥（均默认不跑）                                                                                                                                                                                                                |
| `DHOLE_USAGE_LOG`                                                    | 设 `1`（或一个路径）写本地调用日志（JSONL）：工具名、成功与否、耗时、脱敏后的错误；**不记参数值**、不联网                                                                                                                                          |
| `DHOLE_NO_AUTO_REPAIR`                                               | 设 `1` 后 `dhole` 入口遇到 ImportError 不再自动 `pip install --force-reinstall`（只打印修复命令）                                                                                                                                                  |
| `DHOLE_UPDATE_PACKAGE` / `DHOLE_UPDATE_INDEX_URL`                    | 自更新的发行名与 `--index-url`（发布自己的发行版后设置）                                                                                                                                                                                           |

**引擎冷却**：免密引擎连续 3 次连接失败（通常是被墙）冷却 10 分钟；被反爬封（403/429/503）冷却 60 秒。冷却**到期即自动放行**，也可 `dhole engines reset` / `cache_clear(engine_state=true)` 立即清空，`dhole engines list` 看每家的判定与剩余秒数。`engine_preempted` 不是冷却：那是"够数的引擎先答完、这一路被取消"，健康快池下是常态。

### Keyed 搜索后端（brightdata / tavily / exa / bocha）

配了 key 即启用，与免费引擎**并行**执行，但**默认不跑**：只有 `engines=[...]` 显式点名才调用（命中搜索缓存则连请求都不发），也不计入共识门槛。失败大多静默返回空（不影响本次搜索），**key 错误/过期除外** —— 会如实报 `error:...AuthError`，不伪装成「没有结果」，也不触发熔断。按次计费：请求发出即扣配额，所以提前返回时不会取消它。

### 重排模型

搜索排序由一个本地 cross-encoder 负责（「这条结果跟你的问题真有关吗」）。模型不进 wheel，首次神经搜索时下载到 `~/.dhole/models/<名字>/`；缺依赖或下载失败时自动退回跨引擎共识排序、不报错。

| 名字             | 语言                  | 体积   |
| ---------------- | --------------------- | ------ |
| `bge-zh`（默认） | 中英双语              | ~279MB |
| `zh-full`        | 跨语言（含中文/多语） | ~450MB |
| `ms-marco`       | 英文优先              | ~91MB  |

```bash
dhole model                  # 列出可用模型 + 当前生效的那个
dhole model use ms-marco     # 切换
```

也可以直接编辑 `~/.dhole/config/reranker.json`（`{ "model": "ms-marco" }`）。未注册的名字会被拒绝并回退默认，不会静默换模型。下载可断点续传；**离线 / 多机复用**：把 `~/.dhole/models/<名字>/` 整个目录拷到目标机同一路径即可，之后不再联网。

## 边界与前提

- **搜索能力的来源**：免密引擎是**对公开搜索结果的直接抓取**——没有授权、没有配额、没有 SLA。所以「免费」的确切含义是「用不受许可的读取替代付费授权」，代价由可用性承担：对方改版、封 IP 或收紧反爬时只表现为**静默降级**（熔断/冷却/退回共识排序），不会报错。需要可靠性请用 `DHOLE_SEARCH_PROXY` 或 keyed 后端。
- **合规边界**：抓取与爬取**不检查 `s.txt` 的 Disallow**（只在 sitemap 发现时读它的 `Sitemap:` 指令）；UA 与 TLS 指纹是伪装的，被拦截时会升级到隐身浏览器求解 Cloudflare 验证。目标站点的 ToS 与当地法律由使用者自负。
- **SSRF 防护**（前提：本工具跑在自己的机器上、单用户使用；共享机器请自行收紧）：
  - **HTTP 层**：入口 URL 与**每一跳重定向**都过 `validate_url` —— scheme 白名单、各种 IP 变体记法、IPv4-mapped IPv6、云元数据主机名、DNS rebinding 服务名，以及默认开启的「域名解析到内网即拒」。hosts 里钉到 `127.0.0.1` 这类本机开发覆盖按**钉到的值**放行，钉到 `0.0.0.0` 这种屏蔽占位则拒绝。
  - **浏览器层**：请求前拦截（页面 JS 发起的 fetch/XHR、iframe、JS 跳转都先判定是否解析到内网）；落地后若是内网则抛 `ssrf_blocked` 且**不返回任何正文**。入口站点豁免（已过校验），异端口不豁免。
  - **残余**：校验用一次 DNS、连接时再解析一次，存在 TOCTOU 窗口（纵深防御，不是边界）；HTTP 3xx 重定向的目标仍会发出一次请求（内容不回流，但"打一下"还在）；浏览器层为通过真实站点的残缺证书链设了 `ignore_https_errors`，代价是同网络位置的中间人可给这一层伪造内容（HTTP 层不做此让步）。
- **archive.org 第三层降级**（`smart_fetch` 专属，文档此前完全没写）：自动升级实际是三层，不是两层 —— `http → stealthy → archive.org`。第三层只在**站点硬阻断**时触发（`404/410/451`、网络失败、`5xx`、`403/503` 且是 bot challenge、`all_tiers_failed`），代价是 **10–30 秒**，且返回的是**某个日期的快照**。响应里 `metadata.source == "archive.org"`、`metadata.archived_at` 给出快照日期，`escalation_path` 会是 `http→stealthy→archive.org`。**没有任何参数能关掉这一层**，所以对时效敏感的问题，引用前必须先看 `source`——`content_ok` 仍然是 `true`，它只回答"拿到了内容"，不回答"内容是今天的"。
- **内容可用性不等于 `content_ok=true`**：`content_ok` 的判定是 `2xx/3xx + 无 error + 正文非空`。已知会漏掉两类：`content_ok=true` 但正文是失败/占位页（已加"软失败"检测，覆盖 `Try reloading` / `Please wait...` 这类，但检测是**词表 + 长度门限**，仍是打地鼠），以及 `content_ok=true` 但内容来自存档（见上一条）。引用前顺手看一眼字符量，几百字符的"成功"大概率是壳。
- **`parse` 的字符集探测**：本地文件没有 HTTP 头可依据，所以 `.html`/`.csv` 按 **BOM → `encoding` 参数 → UTF-8 → GB18030** 依次**严格**试解（GB18030 覆盖 GBK/GB2312，即中文 Windows 上 Excel 的默认导出格式；UTF-16/32 无 BOM 时按头部 NUL 字节识别），`metadata.encoding` 给出实际生效的字符集。解出来仍带损伤（替换字符、`Ã`/`Â` 类错码、PUA 私有区字符）时**不当作成功**：正文照常返回，但 `error` 为 `encoding_undecodable`、`content_ok=false`、`next_action` 让你带 `encoding=` 重试。
  - **已知缺口（实测，不是推测）**：GB18030 能把其它 CJK 老编码"严格解出来"。Big5 会落进 PUA 因此**能**被检出并标记；而 **Shift_JIS / EUC-KR 会解成"看起来合理的中文"且损伤为 0**（`名前` → `柤慜`）——没有廉价的字节特征能把它们和真 GBK 分开，只能显式传 `encoding=shift_jis` / `encoding=euc-kr`。Latin-1/cp1252 文件同理会被解成 GB18030 并标记为有损伤，`encoding=cp1252` 可解。
  - 刻意**不**把 cp1252 放进自动链：它能把**任何**字节解成零损伤的拉丁文本，那会让损伤信号彻底失效——看得见的失败优于看不见的错误。
  - `parse` 单独 import 时不依赖抓取栈（探测逻辑在 `charset.py`，不 import `primp`），缺 `[all]` extra 时降级路径不会 ImportError。
- **提示注入**：抓回来的正文是**不可信数据**，指令里已要求模型不要执行页面里的"指令"，但那是提示、不是强制；页面里出现工具调用、密钥、上传指令时都应按提示注入处理。同理 `is_official` 只对 gov / edu / github 这类第三方注册不走的命名空间为真，`docs.*` 子域不构成权威。
- **上下文开销**：MCP 客户端每次连接要付一次固定 token（`instructions` + 8 个工具 schema），合计约 3.3k（cl100k_base），之后不再重复。15.1 对 wire 做了一轮去重裁剪（13,605 → 12,601 字符，**-7.4%**）：删掉的是**同一事实写两遍**（描述 ↔ `options` 包 ↔ `instructions` 三处重复）、agent 无法据此行动的机制细节（archive.org 的触发状态码清单、相对路径的四步解析顺序），以及一处**错误的**引擎清单 —— `smart_search` 的描述只列了 5 个 opt-in 引擎而注册表里是 8 个（漏 `so360`/`sogou`/`sogou_weixin`），现在改为指向 `options.engines`，两份手写清单只留一份。**参数、类型、枚举、`required`、`annotations` 一个没动**；`tests/test_tool_descriptions.py` 的预算同时**下调**（12,300 / 1,465 / 13,800），让省下的额度不会被下一个改动自动吃掉。
- **真正的大头是响应正文，不是工具表**：连接期固定成本约 12.6k 字符（≈3.3k token，一次性），而单次 `smart_fetch` 默认最多返回 **40,000 字符**（`max_content_chars`，≈10k token）、单次 `smart_crawl` 默认最多 **500,000 字符**（`max_total_chars` 硬顶）—— **一次抓取就能超过整张工具表**。压总消耗的杠杆在 `max_content_chars` / `max_total_chars` / `focus=`，不在描述里。
- **每个响应的固定信封**：`ResponseModel` 有 30 个字段，服务端用 `model_dump_json()` 整体序列化，所以**空值与默认值也照发**（`"error":""`、`"media":[]`、`"content_age_days":null`、`"archived_at":""` …）。实测一次普通抓取的响应 3,869 字符里有 667 字符是信封，其中约 370 字符（55%）在该场景下不携带任何信息。另外同一份 JSON 会同时出现在 `content`（文本，为不读结构化输出的客户端保留）和 `structuredContent`（结构化）两处 —— 服务端确实发了两份（`_dispatch` → `CallToolResult(content=..., structured_content=...)`），**客户端是否把两份都喂给模型未验证** `[unverified]`。

**本机会留下什么**（全在 `~/.dhole/`，可用 `DHOLE_HOME` 换位置；POSIX 下目录 0700、文件 0600，Windows 上靠换位置 + NTFS ACL）：

| 位置                                                 | 内容                                                                                                                |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `cache.db`                                           | 抓到的正文（明文 SQLite）。按请求上下文分区：带 cookies / 自定义头 / UA / 代理 / PDF 口令的抓取不与匿名请求共享缓存 |
| `models/<model>/`                                    | 重排模型（3 个文件 + `model.sha256`）                                                                               |
| `config/reranker.json`                               | 重排模型选择                                                                                                        |
| `circuit_breaker.json`                               | 引擎熔断/冷却状态（到期自动放行）                                                                                   |
| `engine_stats.json`                                  | 每个引擎最近一轮的解析产出（`dhole -v` 的 `engine yield` 读它）                                                     |
| `search_proxies.json`                                | 代理池（凭据**明文**存储，`dhole proxy list` 显示时打码）                                                           |
| `search_feedback.json` / `usage.jsonl` / `repair.py` | 域名偏好 / 调用日志 / 自愈脚本，分别对应上面三个开关                                                                |

缓存 TTL 默认 1 小时；用默认值时 docs 页自动抬到 24h、article 页 6h；`cache_ttl=0` 完全绕过缓存。

## 已知限制

- 无法绕过 DataDome / Akamai / 交互式 Turnstile；需要登录的网站不在设计范围内
- 引擎可达性：`baidu` / `bing` / `yandex` 国内直连；`brave` / `duckduckgo` / `yahoo` 与 opt-in 的 `bing_global` / `mwmbl` 需要 VPN 或代理；`so360` / `sogou` 是国内直连的 opt-in
- 引擎被限速时自动熔断冷却（60 秒），重度使用建议配置代理
- PDF 口令：用 `password=` 选项；没给或给错时会**明确说是口令问题**，不混进「文件打不开」
- 单次 `smart_crawl` 存在 **500000 字符总预算硬顶**（`max_total_chars` 被钳在这个值）：`max_pages` 只在未显式给出 `max_total_chars` 时参与推导，所以撞上钳制后**再调大 `max_pages` 没有效果**（实测传 100 只抓到 31 页）。要更多内容得同时抬 `max_total_chars`，或用 `crawl_urls=[...]` 分阶段取
- `smart_crawl` 的 `path_include`/`path_exclude` 按**路径子树**匹配，不是字符串前缀：`'/docs'`（`'docs'`、`'/docs/'`、`'/docs/*'` 等价）命中 `/docs` 及其下全部，但**不**命中 `/docs-old`、`/docsomething`；`'/'` 或 `'*'` 表示全部。**只接受尾部 `/*` 这一种通配写法**，其余（如 `'/api/*/v1'`）会直接报错 —— 早期版本用 `startswith` 匹配，上面四种写法里两种会**静默**过滤掉整个爬取、一种会多抓兄弟目录、一种完全不生效，都是「看起来成功」的失败
- YouTube 仅能获取少量文本
- 引擎存活不做主动巡检，但每轮真实搜索都会记下解析产出（`dhole -v` 的 `engine yield` 行能区分「被墙」与「答了但解析不出来」）；opt-in 引擎里垂直索引（`sogou_weixin`）在无重排器时排在通用引擎之后，也不能独自填满早退配额

## 贡献

欢迎 issue 与 PR，规范（开发环境、测试、引擎契约与 fixture）见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 致谢

本项目是 [dondai1234/master-fetch](https://github.com/dondai1234/master-fetch) 的二创（衍生作品），上游以 MIT 协议发布，原始版权文本已完整保留于 [LICENSE](LICENSE)。

## 许可证

[MIT](LICENSE)
