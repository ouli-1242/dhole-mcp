<div align="center">

# Dhole

**让 AI 代理访问互联网：抓取 · 爬取 · 搜索，内置反爬，$0 无密钥。**

<a href="https://pypi.org/project/dhole-mcp/"><img src="https://img.shields.io/pypi/v/dhole-mcp.svg" alt="PyPI version"></a>
<img src="https://img.shields.io/pypi/pyversions/dhole-mcp.svg" alt="Python 3.11+">
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.svg" alt="MIT License"></a>
<img src="https://img.shields.io/badge/transport-stdio%20%C2%B7%20HTTP-blueviolet.svg" alt="MCP stdio and streamable HTTP transports">

</div>

Dhole 是一个 [MCP](https://modelcontextprotocol.io) 服务器：抓网页、爬整站、搜资料、读 PDF、截图、操作页面，全部本地运行，不需要任何 API key。

## 30 秒理解

- **是什么**：给 AI 用的上网工具包。
- **怎么用**：正常提要求，AI 自己选工具，你不用记命令。
- **11 个工具默认全部注册**：搜网页 · 抓网址 · 在页面上动手（截图在内）· 发一次非 GET 请求 · 爬整站 · 出 URL 地图 · 读本地文件 · RSS 订阅 · 设一次的默认身份 · 看与清本地状态 · 读 dhole 自己的说明书。
- **没有"要额外打开"的能力**：每一颗都默认注册，`DHOLE_TOOLS` 是唯一的子集开关，写了它才会少注册。
- **代理环境自己适配**：Clash / sing-box 的 TUN + fake-ip 不需要先设任何变量。
- **要调设置**：见[配置](#配置)；出问题先跑 `dhole doctor`。

## 安装

需要 Python 3.11+。

```bash
pip install dhole-mcp              # 默认即完整版：浏览器 / PDF / OCR / 解析器 / 重排
python -m patchright install chromium   # 浏览器二进制（~193MB），pip 装不到这一步。
                                  # 跳过也行：第一次用到浏览器档时 dhole 会装它自己要起的那一份；
                                  # 上游 playwright 那一颗不行——patchright 的 driver 只认自己清单里的版本
dhole doctor                       # 安装体检：起一次 server、真起一次浏览器，再跑一次真实搜索 + 真实抓取
```

- `pip install "dhole-mcp[all]"` 等价于默认安装（老文档里的写法，仍然有效）。
- 没有 Playwright wheel 的平台（Termux / aarch64）会卡在浏览器依赖；真要 HTTP-only 得 `pip install --no-deps dhole-mcp` 再手挑运行核心。
- 缺浏览器依赖时自动降级为纯 HTTP，并在失败点给可复制的命令；那是降级不是故障。
- 可选：`dhole skill install` 把随包说明书装成宿主原生 skill；不装也有 `sys_guide` 兜底。
- 开发本仓库：`pip install -e ".[dev]"`，改源码立即生效，测试 `python -m pytest`（细节见 [CONTRIBUTING.md](CONTRIBUTING.md)）。
- 卸载：`pip uninstall dhole-mcp`；运行数据全在 `~/.dhole`，删目录即清干净。

### 让 agent 自己装

把下面整块粘给编码 agent：

```
在这台机器上安装 Dhole MCP 服务器，逐步执行，失败就停下报告。

1. 认清你所在的 agent 宿主，查出它的 MCP 配置文件位置与添加本地服务器的格式（读宿主文档，不要猜）。
2. pip install dhole-mcp，然后 python -m patchright install chromium（已装则跳过；不装会在第一次抓取时自动装）。
3. 备份配置文件，添加名为 "dhole" 的服务器：command 为 "dhole"，无参数、无密钥、无环境变量。
4. 让用户重启 agent，再运行 dhole doctor 确认体检通过。
5. 可选：dhole skill install。
```

## 配置

### 两条门

| 门 | 怎么写 | 什么时候生效 |
|---|---|---|
| **文件**（推荐） | `dhole config set DHOLE_IGNORE_ROBOTS 1`，落在 `~/.dhole/config.json` | 按次读取的旋钮下一次调用就生效，不用重启；`dhole config list` 看当前值与来源 |
| **env 块** | 宿主 MCP 配置里的 `env`，值一律写成字符串 | 重启宿主之后 |

**env 永远优先**——一个文件不会悄悄覆盖你已经做出来的决定。`dhole doctor` 两个门一起审计。

### 配置形状

```json
{
  "mcpServers": {
    "dhole": {
      "command": "dhole",
      "args": [],
      "env": { 
        "DHOLE_SEARCH_PROXY": "http://127.0.0.1:7890" 
      }
    }
  }
}
```

变量名与取值见下面这张表。**默认行为通常就是你要的**，不确定就先别设。

### 常用变量

| 变量 | 什么时候设 |
|---|---|
| `DHOLE_SEARCH_PROXY` | 搜索引擎被限流 / 被墙时（也自动读 `HTTPS_PROXY` 等）；只想让某一次调用走代理，用 `cred_defaults` 存 |
| `DHOLE_IGNORE_ROBOTS=1` | 关掉进程级的 `robots.txt` 遵从（单次豁免是 `ignore_robots=true`，那是你的合规决定） |
| `DHOLE_ALLOW_PRIVATE_HOSTS` | 放行内网 / 本机地址，默认一律拒绝 |
| `DHOLE_HTTP_TOKEN` | `--http` 传输的 bearer 门：设了之后每个 `/mcp` 调用必须带 `Authorization: Bearer …` 或 `X-Dhole-Token`；未设保持开放行为（非回环绑定启动时警告）。启动时读取，改了要重启 |
| `DHOLE_PERSIST_CREDENTIALS=1` | 把会话 cookie 与 `cred_defaults` 存的凭据落盘（值经 Windows DPAPI 加密，绑定本用户）。**默认只在内存**：库文件根本不创建，进程结束即清；非 Windows 无 DPAPI，设了也不落盘（`dhole doctor` 报 fail）。启动时读取，改了要重启 |
| `DHOLE_USAGE_LOG=1` | 本地调用与写操作日志（不记参数值、不联网） |
| `DHOLE_TOOLS=web_read,web_search` | 只注册列出的工具，连接期省约一半；**写这个就等于关掉其余工具** |
| `DHOLE_DEFAULT_ENGINES` | 覆盖默认搜索池（默认 `baidu,bing,sogou,bing_global,yandex,brave`） |
| `DHOLE_DEFAULT_CONTENT_CHARS` | 默认正文预算（默认 40000，区间 500–200000） |
| `DHOLE_TAVILY_API_KEY` / `DHOLE_BOCHA_API_KEY` | 两颗 keyed 引擎的密钥：配了才启用，只有点名才调用，按次计费 |
| `DHOLE_HF_ENDPOINT`（或 `HF_ENDPOINT`） | 重排模型下载源（默认 `huggingface.co`，失败回退 `hf-mirror.com`） |
| `DHOLE_HOME` | 状态目录（默认 `~/.dhole`） |

**其余旋钮已收成固定值**，写进 env 也不会被读，所以这张表不再列它们：引擎冷却与心跳、cookie 存活时长、搜索整体截止、浏览器空闲关闭与预热、SSRF 的 DNS 复验、`structuredContent` / `outputSchema` / 完整 wire 形状三个开关、多模态许可（截图与元素裁剪总是可用）、神经 focus 的许可门（改成了每次调用的 `focus_engine`）、`doc_parse` 的工作目录、自愈与搜索代理池、起哪一台浏览器（检测顺序 chrome → msedge → 内置 chromium，这台机器上有什么就用什么）。理由同一句话：**能算清的数不需要旋钮**，需要按次决定的改成了参数——"要不要看得见那扇窗"就是后者，见下面那一节。

### 借你已登录的 Chrome（CDP 接管）

- `chrome.exe --user-data-dir=D:\dhole-chrome --remote-debugging-port=9222`（Chrome 136+ 不许对默认 profile 开调试），在这个窗口登录一次目标站点。Edge 同样是 CDP 端点，把这行换成 `msedge.exe --user-data-dir=… --remote-debugging-port=9222` 即可，dhole 这边只改端口。
- 第一次 `web_read(url=..., cdp_url="http://127.0.0.1:9222")` **不执行任何东西**，回 `cdp_authorization_required`；用户同意之后同一调用加 `confirm=true` 才放行，并把那一台主机写进授权名册（精确主机名：`example.com` 不含 `www.example.com`）。
- 名册之后 agent 抓这些站点会带你的登录态；每次 attach 都回 `cdp_attached: true` 并进审计。要撤销就删状态目录里的 `cdp_hosts.json`。
- 每一台新主机都要重新过一次这道闸：attach 的重定向与第三方请求会带着你的已认证 cookie 出门，所以授权只能逐台点名，不能一把开。

### 要看着它干活（可见浏览器）

- 没有 env 旋钮，这是一次调用的事：`web_act(url=..., actions=[...], headed=true)` 起一台**看得见**的窗口，方便你手工登录、盯着它填表、替它过一道验证码。搜索与抓取默认照旧隐形。
- 配 `cred_defaults(set={'profile': '点名'})` 再用，否则你登的东西随那台浏览器的临时目录一起没了。一台 profile 只有一台浏览器，窗口开着的时候后续调用会在你眼前开标签页。
- 那个 profile 上已经有一台**隐形**浏览器在跑时，这一次会被具名拒绝（`browser_busy`）——不接管别人的窗口，也不关掉可能正被你使用的标签页。先把窗口交回去：`sys_state()` 列会话，`sys_state(clear=['<session id>'])` 关一个。
- 看得见的窗口不被空闲回收（`AUTO_SESSION_IDLE_TIMEOUT=300` 只关那台隐形的自动会话），所以用完要自己交回。它反过来能干后台的活：窗口热着时，普通调用直接骑它。

## 用法

装好重启后直接说人话：「抓 `https://example.com/pricing` 列成表」→ `web_read`；「查官方文档挑两条最相关的读给我」→ `web_search` + `web_read`；「`/guide` 下面都收一遍」→ `web_crawl`；「这份 `report.pdf` 讲了什么」→ `doc_parse`。

| 命令 | 用途 |
|---|---|
| `dhole -v` | 版本 + 能力面板 |
| `dhole doctor` | 安装体检，逐项给修复命令 |
| `dhole config` | `list` / `set <NAME> <value>` / `unset <NAME>` |
| `dhole engines list` / `probe` / `reset` | 引擎冷却状态 / 实测健康 / 立即清空 |
| `dhole skill` | 安装 / 查看随包 agent 技能 |
| `dhole --http --host --port` | 改用 streamable HTTP 传输（默认 stdio） |

**传输**：`dhole --http` 的端点是 `http://<host>:<port>/mcp`，**这层默认不带鉴权**——设 `DHOLE_HTTP_TOKEN` 后每个调用必须带 bearer 令牌；未设时 `--host 0.0.0.0` 等于把「替你抓任意 URL（含写请求与存储凭据）」开放给整个网络，共享网络里自行套反代 / 认证 / 防火墙。传输层同时校验 Host / Origin 头（DNS rebinding 防护，按 `--host` 构建名单）。

## 工具

**11 颗默认全部注册**：

| 工具 | 做什么 |
|---|---|
| `web_read` | 抓任意 URL：反爬升级、PDF / OCR、批量、聚焦提取、结构化提取、页面动作、CDP 接管 |
| `web_act` | 在页面上动手：click / fill / press / scroll / wait / snapshot / submit，然后把动作后的那一页读回来。`submit` 不带 `confirm=true` 只暂停，什么都不提交。截图也在这条动作链里：`{"screenshot": "css"}` 裁元素内联回图，dict 形态加 `full_page` / `save_to`（看不见图的宿主拿路径）/ `image_type` / `quality`；`anti_detect` 在这一扇门上只管"要不要先答 Cloudflare 挑战"。`headed=true` 把这一串动作放到**看得见**的窗口里跑（要你登录、盯场、替它过一次验证码时用它），细节见上面「要看着它干活」那一节 |
| `web_request` | 发一次非 GET：POST / PUT / PATCH / DELETE / HEAD。**只发一次**——不重试、不缓存、不升浏览器、不回退快照；凭据从 `cred_defaults` 来，正文上限 256KB |
| `web_search` | 无密钥搜索：多引擎并行 + 本地重排，可顺带抓回前三条全文 |
| `web_crawl` | 同域爬取：sitemap、关键词过滤、路径子树限定 |
| `web_discover` | 出站点的 URL 地图：一次调用看清「这个站有什么」再挑要读的，不做正文抓取（`pages_crawled=0` 是设计不是失败）；挑中的交给 `web_read(urls=…)` |
| `doc_parse` | 本地文件 → Markdown（docx / xlsx / pdf / pptx / odt / csv / html / json / yaml / md / txt） |
| `feed_read` | 批量抓 RSS / Atom，`since=` 只取增量 |
| `cred_defaults` | 设一次的默认身份：代理 · 命名浏览器 profile · CDP 端点 · 私网放行 · 反爬档位 · `auth`/cookies（**必须带 host**，否则会被发给下一个你读的站点）。回执与每次响应只报名字，值不出境 |
| `sys_state` | 看与清本地状态：正文缓存 · 会话 cookie（值一律不出境）· 引擎冷却 · 落盘的默认身份。抹掉「事后才察觉」的那几类要 `confirm=true` |
| `sys_guide` | 读 dhole 自己的说明书：选工具、写参数、字段含义、失败怎么修、配置改哪个 |

参数摆放：**每个参数都是顶层那一份**，没有袋子——塞 `options` 会被拒绝，并列出这颗工具真正接受的键。每个工具只接受自己那组名字（`since=` 属于 `feed_read`，不属于 `web_read`），名字对、门错同样拒。写操作在 `web_request`，页面动作在 `web_act`，都不在 `web_read`。以 `tools/list` 为准。响应同样只有一个家：一整个 JSON 对象走文本通道送达，**没有 `structuredContent` 双信封**——同一份 JSON 不送两遍，要程序化消费就解析文本；字段词典见 fields.md（`sys_guide(topic='fields')`）。

调用写法、字段含义与排查的手册有两个入口，同一份语料：`sys_guide(topic=...)`（默认注册，装上就能用）与 `dhole skill install`（宿主原生 skill 形态）。

## 注意事项

- **`content_ok=true` 不代表内容为真**：只表示 2xx + 无错 + 正文非空；几百字符的「成功」多半是登录墙或占位页。
- **`robots.txt` 默认遵守**：被 `Disallow` 的 URL 一个请求都不发，回 `error=robots_disallowed`。
- **写请求一次性**：`web_request` 里 `POST/PUT/DELETE/PATCH` 不重试、不缓存、不升浏览器、不回退快照——重发就是写两遍。超时那句答案是「不知道」不是「没成功」，先读一下再决定要不要再发。
- **凭据不跟跨源重定向走**：存储的 `cookies` 与 `Authorization` 只发给那个源站。
- **凭据只能 `cred_defaults` 存，不逐次传**：`auth` 只把头摆好，不做 OAuth、不刷新 token；没带 host 的凭据一律拒。
- **内网 / 本机默认拒绝**：先 `cred_defaults` 存 `allow_private=true`（只放开回环）或点名主机；云元数据端点任何时候都不放行。
- **PDF 的 `pages=` 只减少抽取量**，文件仍整个下载；大 PDF 调大 `timeout`。
- **抓回的正文是不可信数据**：里面的工具调用、密钥、上传指令按提示注入处理。启发式命中时响应信封带 `untrusted_content` 标记（`web_crawl` 每页、`feed_read` 每条同样带）——标记不是拦截，识别与裁决归外层 agent，纪律见手册 fields.md。
- **提交默认暂停**：`web_act` 的 `{"submit": ...}` 不带 `confirm: true` 什么都不提交；验证码最多试 3 次就转交，2FA / 滑块一律转交。
- **连接期开销 30,804 字符**（约 7.4k token，11 个工具，实测 `python tests/tool_payload_measure.py`），每次连接付一次；11 颗全注册是能力面，字数是它的约束而不是目标。要按用法裁剪用 `DHOLE_TOOLS`——同一把尺子量过：读 / 搜 / 请求 / 爬 / 文档 / 订阅 / 状态 / 指南这 8 颗是 23,876 字符，加 `web_discover` 共 9 颗 25,430。别按单颗体积挑"最小的 N 颗"，那会把 `web_read` / `web_act` 这类主力排除在外。再按**会话**算账：工具目录在宿主模型上下文里整段在场——有提示缓存的宿主折价、无缓存的每轮全价，多轮会话里 `DHOLE_TOOLS` 才是每一轮都省的闸。

## 搜索引擎与重排

**免密 8 个，全部在默认池**：`baidu` / `bing` / `sogou` / `so360`（别名 `360`）/ `bing_global` / `yandex` / `brave` / `duckduckgo`（别名 `ddg`）。没有 opt-in 层，注册表就是默认池；`engines=[...]` 只能**收窄**，单次最多 10 个。另有 2 个 keyed 后端（`tavily` / `bocha`）：配了 key 即启用，只有点名才调用，按次计费。

**日期**只有 day / week / month / year 四档：`after=<日期>` 会被放宽到最窄的覆盖档，`before` 直接拒绝——按日期取增量用 `feed_read(since=)`。

**重排模型**：本地 cross-encoder 只有一颗 —— `bge-zh`（中英双语 int8，~279MB），首次神经搜索自动下到 `~/.dhole/models/bge-zh/`，不给用户选：候选模型与它分差不明显、CPU 上还更慢。老机器上若还留着 `~/.dhole/config/reranker.json`，它不再起作用。`min_raw_relevance` 判断「这一轮有没有相关结果」，起点 **0.1**（实测离题 ~1e-4、勉强 0.2、真命中 0.93+）。离线复用：拷 `models/bge-zh/` 整个目录。

## 故障排查

| 症状 | 怎么办 |
|---|---|
| 搜索结果很少或为空 | 重试；设 `DHOLE_SEARCH_PROXY`；`dhole engines probe` 看哪家能用 |
| 所有公网站点都报内网地址 | fake-ip TUN 已自动适配（探测在启动时做一次，写死为开启）。仍报错就跑 `dhole doctor`：它会把那个地址与其网段点名出来。没有手动关掉复验的旋钮——那个形状正是当初造成误判的地方 |
| 抓取超时 | `timeout` 是整次调用的墙钟预算，文档下载全额计入（默认 30s，上限 120s） |
| 正文被截断 | 用 `offset` / `next_offset` 续取，或调大 `max_content_chars`（上限 200000） |
| 返回了内容但明显是壳 | 见[注意事项](#注意事项)第一条 |
| 本地文件解析成乱码 | 显式传 `encoding=` |
| 装不上 / 工具没注册 | `dhole doctor` |

## 已知限制

- 绕不过 DataDome / Akamai / 交互式 Turnstile；2FA 与 CAPTCHA 不自动解。需要登录的页面可借自己的登录态（CDP attach，或 `cred_defaults` 存一次 cookie / auth）。
- **搜索没有 SLA**：免密引擎是对公开结果的直接抓取，对方改版或封 IP 时静默降级。要可靠性用搜索代理或 keyed 后端。
- 默认池里 `baidu` / `bing` / `yandex` 国内直连稳定；`sogou` 按 IP 限流，`bing_global` / `brave` 无代理时不稳。
- 正文 **50MB 硬顶**，在下载之前守门；抬 `timeout` 没用。
- `archive.org` 返回的是**某个日期的快照**（看 `source` / `archived_at`）。
- YouTube 仅能取到少量文本。
- **合规自负**：目标站点 ToS 与当地法律由使用者承担；抓取 UA 是 `dhole-mcp`。

## 本地数据

全在 `~/.dhole/`（`DHOLE_HOME` 可换位置；POSIX 下目录 0700、文件 0600）。**没有任何遥测**——除了你让它抓的目标站点，进程不往外发任何数据。

| 位置 | 内容 |
|---|---|
| `cache.db` | 抓到的正文（明文 SQLite），TTL 默认 1 小时（article 6h） |
| `sessions.db` / `identity.db` | **默认不存在**：会话 cookie jar（24 小时过期）与 `cred_defaults` 存的凭据只在内存里，进程结束即清；`DHOLE_PERSIST_CREDENTIALS=1` 才落盘，值经 Windows DPAPI 加密（绑定本用户，防的是文件被拷走/同步外带，不防同用户进程）。旧版明文库会在首次使用时吸收进内存并删除（落盘模式则原地重加密） |
| `profiles/<name>/` | 持久浏览器 profile，**含登录态**，删目录即注销 |
| `models/bge-zh/` | 重排模型（唯一一颗） |
| `config.json` | `dhole config set` 写的旋钮；**secret 同样明文落盘**，不想进文件就继续用 env 块 |
| `usage.jsonl`、引擎状态 | 调用与写审计、引擎冷却 |

## 贡献

欢迎 issue 与 PR；开发环境、测试与引擎契约见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 致谢

本项目是 [dondai1234/master-fetch](https://github.com/dondai1234/master-fetch) 的二创（衍生作品），上游以 MIT 协议发布，原始版权文本完整保留于 [LICENSE](LICENSE)。

## 许可证

[MIT](LICENSE)
