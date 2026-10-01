<div align="center">

# Dhole

**让 AI 代理访问互联网：抓取 · 爬取 · 搜索，内置反爬，$0 无密钥。**

<a href="https://pypi.org/project/dhole-mcp/"><img src="https://img.shields.io/pypi/v/dhole-mcp.svg" alt="PyPI version"></a>
<img src="https://img.shields.io/pypi/pyversions/dhole-mcp.svg" alt="Python 3.11+">
<a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green.svg" alt="MIT License"></a>
<img src="https://img.shields.io/badge/transport-stdio%20%C2%B7%20HTTP-blueviolet.svg" alt="MCP stdio and streamable HTTP transports">

</div>

Dhole 是一个 [MCP](https://modelcontextprotocol.io) 服务器：给 AI 代理装上「上网」的能力——抓网页、爬整站、搜资料、读 PDF、截图、操作页面。全部本地运行，不需要任何 API key。

## 30 秒理解

- **是什么**：一个给 AI 用的「上网工具包」——AI 自己不会上网、不会读本地文件、不会点网页，装上它就会了。
- **怎么用**：正常提要求就行，AI 自己决定调哪个工具，你不需要记任何命令。
- **默认能做什么**：搜网页 · 抓一个网址 · 批量抓一个站 · 截图 · 读本地文件（Word / Excel / PDF / PPT）· 盯 RSS 更新 · 解析短链 · 清缓存 · 查看登录状态 · 读 dhole 自己的说明书——共 **10 个**工具。
- **另外 4 个默认关**：录制回放 `recipe` 与网页变化监控 `watch`；密码管理器查询 `vault` 与辅助登录 `login`。默认关是因为工具说明**每次聊天都占额度**，全开白花约 1,700 token。打开它们不必编辑 JSON：`dhole init --tasks` 或 `--vault`（写 `~/.dhole/config.json`，重启宿主后注册），装了什么、还差什么 `dhole init --probe` 会真跑一次告诉你。但它们**存在**这件事不用读这份 README 才知道：`dhole_doc(topic='behavior')` 会告诉你这四个是什么、怎么打开。
- **代理环境现在自己适配**：Clash / sing-box 这类 TUN + fake-ip 代理过去要求你**先**设 `DHOLE_SSRF_DNS_RECHECK=0` 才抓得到东西；现在 dhole 第一次需要判定时探测一次，命中就自动跳过那一层——IP 字面量 / 主机名 / 云元数据守卫照旧，且每个受影响的回包都会说明。要强制恢复：`dhole config set DHOLE_SSRF_DNS_RECHECK on`。
- **要调设置**：见[配置](#配置)。

## 安装

需要 Python 3.11+。

```bash
pip install dhole-mcp             # 默认即完整版：反爬浏览器 / PDF / OCR / 解析器 / 神经重排
playwright install chromium      # 反检测浏览器引擎（~150MB），pip 装不到这一步
```

**两步都跑完浏览器层才可用**——`pip install dhole-mcp` 只落 Python 包，Chromium 二进制是 Playwright 独立下载的。W7 把 extras 反转了，但没改变 Playwright 的引擎分发方式。（`dhole init`（W8）会一次跑完这两步 + 写客户端配置，装完就能用。）

`pip install "dhole-mcp[all]"` 依然合法——它现在的效果等价于默认安装（`[all]` extra 保留，是为了 updater 的自愈路径与既有文档 / 用户习惯仍然指向同一个名字，不会失效）。

平台装不上浏览器依赖时的备选（Termux / aarch64 上 Playwright 没有 wheel）：`pip install dhole-mcp[slim]`——这是给这一档**名字**，不是"退回 HTTP-only"的实际开关（pip extras 不能从默认 deps 里减）；真要 HTTP-only 得 `pip install --no-deps dhole-mcp` 再手挑运行时核心，见 troubleshooting。

卸载：`pip uninstall dhole-mcp`；运行时数据全在 `~/.dhole`，删掉该目录即清理干净。

浏览器依赖缺失时会自动降级为纯 HTTP 模式（Termux / 精简容器可用），代价是反爬站点开始失败——那是降级，不是故障。降级现在**在使用点提示**（`install.browser_install_advice()` 会给可复制的命令），不再只在 `-v` 的能力面板里静默列一行。

### 让 agent 自己装

把下面整块粘给你的编码 agent，它会自己完成安装与配置：

```
在这台机器上安装 Dhole MCP 服务器。逐步执行，不要跳步。

1. 判断你运行在哪个 agent 宿主（Claude Code / Cursor / OpenCode 等），找出 (a) MCP
   配置文件的位置，(b) 添加本地 MCP 服务器所需的格式。必要时读宿主文档，不要猜。
2. 运行 pip install dhole-mcp，再运行 playwright install chromium（已装则跳过）。
   任一步失败就停下并告诉用户，不要继续。
3. 备份配置文件，按宿主要求的格式添加名为 "dhole" 的服务器，命令为 "dhole"，
   不带参数、不需要 API key、不需要环境变量。
4. 让用户重启 agent，然后运行 dhole doctor 确认检查项通过（`dhole --doctor` 是同
   一件事的另一种写法）。
5. 可选：运行 dhole skill install，把随包分发的 agent 技能装到 ~/.agents/skills/dhole-web。
```

## 配置

**默认零配置就能跑通最常见的路径**（搜 / 抓 / 截图 / PDF / OCR / 解析本地文件）。要改行为有两条路：**`dhole config set DHOLE_X value`** 写进 `~/.dhole/config.json`，按调用时刻重读，逐次生效的旋钮不用重启宿主；或在 MCP 客户端配置里加 `env` 块（变量名原样，值一律写成字符串，改完重启宿主）。env 永远优先——一个文件不会悄悄覆盖你已经做出来的决定。`dhole --doctor` 两个门一起审计。

**例外已经不需要例外**：Clash / sing-box 这类 TUN + fake-ip 代理过去必须设 `DHOLE_SSRF_DNS_RECHECK=0` 才不致于所有 URL 工具全挂，现在 dhole 探测到这种环境就自己跳过那一层（`dhole --doctor` 会说明这台机器命中了什么，回包里也带 `metadata.ssrf_recheck`）。

```json
{
  "mcpServers": {
    "dhole": {
      "command": "dhole",
      "env": {
        "DHOLE_SSRF_DNS_RECHECK": "0",
        "DHOLE_SEARCH_PROXY": "http://127.0.0.1:7890",
        "DHOLE_MULTIMODAL": "1",
        "DHOLE_NEURAL_FOCUS": "1",
        "DHOLE_TASKS": "1",
        "DHOLE_VAULT": "1",
        "DHOLE_CDP_ATTACH": "1",
        "DHOLE_CDP_HOSTS": "portal.example.com,mail.example.com",
        "DHOLE_ALLOW_PRIVATE_HOSTS": "localhost,192.168.1.50",
        "DHOLE_IGNORE_ROBOTS": "1",
        "DHOLE_USAGE_LOG": "1"
      }
    }
  }
}
```

> 上面这份是**菜单，不是要照抄的配置**——多数人只会用到其中一两条，不确定就先别设，默认行为通常正是你要的。

常用的几个（顺序与上面示例一致）：

| 变量 | 什么时候设 |
|---|---|
| `DHOLE_SSRF_DNS_RECHECK=0` | 手动关掉 DNS 复检这一层。**一般不用设**：fake-IP TUN 代理（Clash / sing-box）下 dhole 会自己探测并跳过；设 `on` 可以强制恢复。普通 HTTP 代理别设 |
| `DHOLE_SEARCH_PROXY` | 搜索引擎被限流 / 被墙时走这个代理（也自动读 `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY`） |
| `DHOLE_MULTIMODAL=1` | **宿主 agent 能看图才设**：解锁元素裁剪截图与本地二维码解码；纯文本宿主不要设 |
| `DHOLE_NEURAL_FOCUS=1` | 让 `focus` 改用语义打分——更准（答案召回 90% → 100%）但**单页约 12.5 秒 CPU**，默认的 BM25 只要 23ms。它是**许可**：flag 开着之后仍可用 `options.focus_engine='bm25'` 让某一次调用走便宜路，反之 `'neural'` 没有许可时被拒绝而不是悄悄降级。一条命令打开（含那 ~280MB 模型）：`dhole init --neural`，按次读取、不用重启 |
| `DHOLE_CDP_ATTACH=1` + `DHOLE_CDP_HOSTS` | 交出的是**用户已登录浏览器的读权限**，默认拒绝。别手改这两个：`dhole init --cdp <host>` 一条命令同时写「开」与「围栏圈在哪些主机」（围栏是**精确主机名**，`example.com` 不含 `www.example.com`；已有列表合并而非覆盖）。两个闸都按次读取，不用重启；attach 开着而没有围栏时 `dhole --doctor` 单行说明 |
| `DHOLE_TASKS=1` | 打开 `recipe` / `watch`（重复任务），默认关 |
| `DHOLE_VAULT=1` | 打开 `vault` / `login`（凭据与登录流），默认关；需本机装了 Bitwarden CLI（`bw`）或 1Password CLI（`op`）并已解锁 |
| `DHOLE_CDP_ATTACH=1` + `DHOLE_CDP_HOSTS` | 借你已登录的 Chrome 读登录后的页面，白名单限定哪些域名能用你的登录态，见下节 |
| `DHOLE_ALLOW_PRIVATE_HOSTS` | 逗号分隔的主机白名单，放行内网 / 本机地址（抓自己的 dev 服务），默认一律拒绝 |
| `DHOLE_IGNORE_ROBOTS=1` | 关掉默认开启的 `robots.txt` 遵从（进程级；单次豁免用 `ignore_robots=true`） |
| `DHOLE_USAGE_LOG=1` | 写本地调用日志（工具名 / 耗时 / 脱敏错误，不记参数值、不联网） |
| `DHOLE_TOOLS=smart_fetch,smart_search` | 只注册列出的工具，连接期开销省约一半。**没写进上面的示例**——照抄它会静默关掉其余 11 个工具 |

其余变量（都不是必须设的）：

| 变量 | 用途 |
|---|---|
| `DHOLE_BROWSER_PROFILE` | 设名字后浏览器改用 `~/.dhole/profiles/<name>/` 持久 profile，cookie / 登录态跨重启存活（关闭不删）；不设仍是临时目录。**这是凭据级数据**，只在确实要「登录一次长期复用」时开 |
| `DHOLE_COOKIE_TTL_S` | 会话 cookie jar 的过期秒数（默认 86400 = 24h，区间 60–2592000；写入时定死、使用不续期） |
| `DHOLE_DEFAULT_ENGINES` | 覆盖免密默认搜索池（默认 `baidu,bing,sogou,bing_global,yandex,brave`） |
| `DHOLE_SEARCH_DEADLINE` | 单次搜索整体截止秒数（默认 16） |
| `DHOLE_SEARCH_FEEDBACK` | 设 `1` 开启域名偏好：抓成功的域名获得排序加权（默认关） |
| `DHOLE_DEFAULT_CONTENT_CHARS` | 默认正文预算（默认 40000，区间 500–200000） |
| `DHOLE_BROWSER_IDLE_TIMEOUT` | 浏览器空闲关闭秒数（默认 300，`0` = 永不关闭） |
| `DHOLE_NO_BROWSER_PREWARM` | 设 `1` 启动不预热隐身浏览器（默认预热，为首次抓取省 3–5 秒） |
| `DHOLE_ENGINE_COOLDOWN` | 普通被拒的冷却秒数（默认 60，区间 5–1800；越界会被夹紧并说明） |
| `DHOLE_ENGINE_CHALLENGE_COOLDOWN` / `DHOLE_ENGINE_CONN_COOLDOWN` | 连续 3 次「校验页」/ 连续 3 次「连不上」的冷却秒数（各默认 600，区间 60–7200） |
| `DHOLE_ENGINE_HEARTBEAT` | 主动巡检冷却中引擎的间隔秒数（默认 300，`0` = 关）；探测失败不会延长惩罚 |
| `DHOLE_STRUCTURED_CONTENT` | 设 `1` 时每次调用额外再发一份 `structuredContent`（体积翻倍）；只有下游确实按结构化字段解析时才开 |
| `DHOLE_OUTPUT_SCHEMA` | 设 `1` 时在 `tools/list` 声明 `outputSchema`（连接期 +13.6%）；会**隐含打开**上面那个。面向人的界面或要做校验的客户端才值 |
| `DHOLE_WIRE_FULL` | 设 `1` 恢复瘦身前的响应形状（每个字段恒定存在，含默认值）；给按 `result["字段"]` 直接取值、不理解「缺席即默认」的调用方用 |
| `DHOLE_HOME` | 状态目录（默认 `~/.dhole`，装着抓到的正文明文与搜索词）；共享机器上可指到别处 |
| `DHOLE_WORKDIR` | `parse` 解析相对路径时额外尝试的目录 |
| `DHOLE_HF_ENDPOINT`（或 `HF_ENDPOINT`） | 重排模型下载源；默认先试 `huggingface.co`、失败回退 `hf-mirror.com`，设了就只用这一个 |
| `DHOLE_BRIGHTDATA_API_KEY` / `DHOLE_BRIGHTDATA_ZONE` / `DHOLE_BRIGHTDATA_COUNTRY` | Bright Data SERP 后端（zone 默认 `dhole`，地区默认 `us`） |
| `DHOLE_TAVILY_API_KEY` / `DHOLE_EXA_API_KEY` / `DHOLE_BOCHA_API_KEY` | 对应 keyed 引擎的密钥（均默认不跑） |
| `DHOLE_NO_AUTO_REPAIR` | 设 `1` 后入口遇到 ImportError 只打印修复命令，不自动重装 |
| `DHOLE_UPDATE_PACKAGE` / `DHOLE_UPDATE_INDEX_URL` | 自动修复与 `dhole -u` 重装时用哪个发行版名 / 哪个索引源（默认 `dhole-mcp` + 官方 PyPI）。**从私有源或改名分发装的话要设**——否则修复会去官方源装同名的另一个包，把这份 fork 覆盖掉 |

配置出问题先跑 `dhole --doctor`——它逐项体检并给出可直接复制的修复命令。

### 借你已登录的 Chrome（CDP 接管）

有些页面必须登录才看得见。让 dhole 连上你**已经登录**的 Chrome，就不必把密码交给它。三步：

1. 用**副本 profile** 启动 Chrome 并开着调试端口（Chrome 136+ 不允许对默认 profile 开调试），在这个窗口里登录你要用的站点（一次性）：

   ```bash
   chrome.exe --user-data-dir=D:\dhole-chrome --remote-debugging-port=9222
   ```

2. 在 MCP 配置的 `env` 里加 `DHOLE_CDP_ATTACH=1`（总开关）与 `DHOLE_CDP_HOSTS=github.com,example.com`（导航白名单，**强烈建议设置**），重启客户端。

3. 之后 agent 抓这些站点的页面时，会带上你的登录态。

不用这功能就整行删掉——它默认关闭，不设开关时相关调用直接拒绝。**白名单不设的风险**：attach 后页面的一次重定向、一个第三方脚本请求都可能带着你的已认证 cookie 出门，所以白名单是强制的路线级拦截，不是摆设。

## 用法

装好重启后直接对 agent 说人话：

- 「抓 https://example.com/pricing，把价格列成表」→ `smart_fetch`
- 「查这个库的官方文档，挑两条最相关的读给我」→ `smart_search` 再 `smart_fetch`
- 「把 docs.example.com 的 `/guide` 下面都收一遍」→ `smart_crawl`
- 「这份 report.pdf 讲了什么」→ `parse`（本地文件）或 `smart_fetch`（在线 PDF）

CLI 诊断与配置：

| 命令 | 用途 |
|---|---|
| `dhole -v` | 版本 + 能力面板 |
| `dhole --doctor` | 安装体检，逐项给修复命令 |
| `dhole engines list` / `probe` / `reset` | 引擎冷却状态 / 逐个实测健康 / 立即清空 |
| `dhole proxy list` / `add` / `remove` / `clear` | 搜索代理池 |
| `dhole model` / `dhole model use <name>` | 查看 / 切换重排模型 |
| `dhole skill` | 安装 / 查看随包分发的 agent 技能（`install` / `status`） |
| `dhole init` | 一条命令跑完安装与配置（`--yes` 才落盘；`--tasks` / `--vault` 打开默认关的工具组；`--neural` 打开语义 focus 并把重排模型下载好；`--cdp <host>` 打开 CDP attach **并按主机加围栏**；`--probe` 跑一次真实搜索+抓取确认"从这里能用"） |
| `dhole config` | 持久化旋钮：`list` / `set <NAME> <value>` / `unset <NAME>`（写 `~/.dhole/config.json`，不用改宿主 JSON、逐次生效的旋钮不用重启） |
| `dhole --http --host --port` | 改用 streamable HTTP 传输（默认 stdio） |

**传输方式**：默认 stdio，agent 把 `dhole` 当子进程拉起。要让多台机器共用同一个 dhole，用 `dhole --http`，端点是 `http://<host>:<port>/mcp`——**这一层不带任何鉴权**，`--host 0.0.0.0` 等于把「替你抓任意 URL」的能力开放给整个网络，放在共享网络里请自行套上反向代理 / 认证 / 防火墙。

## 工具

**默认 10 个**：

| 工具 | 做什么 |
|---|---|
| `smart_fetch` | 抓任意 URL：自动反爬升级、PDF / OCR、批量、聚焦提取、结构化提取、页面交互、`POST/PUT/DELETE/PATCH/HEAD`、`auth`（basic / bearer / API-key 头）、条件请求、CDP 接管 |
| `smart_search` | 无密钥网页搜索：多引擎并行 + 本地重排，可顺带抓回前三条全文 |
| `smart_crawl` | 同域爬取：sitemap 模式、关键词过滤、`path_include` / `path_exclude` 子树限定 |
| `screenshot` | 页面截图；看不见图片的宿主传 `options.save_to` 拿文件路径 |
| `parse` | 本地文件 → Markdown（docx / xlsx / pdf / pptx / odt / csv / html / json / yaml / md / txt）。docx / xlsx / pptx / odt 是 zip、pdf 是容器，**文本工具读到的是字节**；非 UTF-8 的 csv / html 会解成「看着对、其实错」的字而不是报错 |
| `feed_fetch` | 批量抓 RSS / Atom 最新条目，`since=` 只取增量 |
| `resolve_url` | 解析 URL 的最终地址（跟随重定向，不抽正文） |
| `cache_clear` | 清抓取缓存（连 robots 结论与全部会话 cookie jar 一起清） |
| `close_session` | 查看 / 清掉会话 cookie（无参数=名册；cookie 值一律不出境） |
| `dhole_doc` | 读 dhole 自己的说明书：怎么选工具、参数怎么写、字段什么含义、失败了怎么修、配置改哪个。一次一卷，猜错 topic 返回的是目录不是报错 |

**默认关的 4 个**：

| 工具 | 做什么 | 开关 |
|---|---|---|
| `recipe` | 把一条抓取路径录下来，以后一次调用重放 | `DHOLE_TASKS=1` |
| `watch` | 盯一个 URL 有没有变，与上次快照做 diff | `DHOLE_TASKS=1` |
| `vault` | 读**你自己**密码管理器里的条目元数据（**从不回密码 / TOTP 值**） | `DHOLE_VAULT=1` |
| `login` | 用其中一条凭据登录：先只回计划待你确认，确认后才填并提交 | `DHOLE_VAULT=1` |

参数怎么摆：`smart_fetch` / `smart_crawl` / `smart_search` / `screenshot` 的旋钮收在一个 `options` 对象里，顶层同名参数也接受——那是兼容读法，两处都给时**顶层优先**；其余工具参数少，只有顶层这一层，塞 `options` 会被拒绝并列出它真正接受的键。两个方向相反的例外值得点名：`smart_fetch` 的 `url` 必须摆在外面；`smart_crawl` 的 `discover_only` 只能放顶层（报错会说明它是顶层参数，不是没有这个能力）。每个工具接受哪些键，`tools/list` 里写清了，以它为准。

**更细的调用写法、响应字段含义与失败排查**（agent 侧的手册）有两个入口：**默认注册的那个**是 `dhole_doc(topic=...)`，装上就能用，不用配置也不用重启；想要宿主原生的 skill 形态，再跑 `dhole skill install` 装到 `~/.agents/skills/dhole-web`（两者是同一份语料，不会各说一套）。

## 注意事项

这些会改变你怎么用，值得先知道：

- **`content_ok=true` 不代表内容为真**：它只表示「2xx + 无错 + 正文非空」。几百字符的「成功」多半是登录墙或占位页——看字符量，别照着答。
- **`robots.txt` 默认遵守**：被 `Disallow` 的 URL **一个请求都不发**，返回 `error=robots_disallowed`。合规是使用者的决定，单次 `ignore_robots=true` 或进程级 `DHOLE_IGNORE_ROBOTS=1`。
- **写请求是一次性的**：`POST/PUT/DELETE/PATCH` 不重试、不缓存、不升浏览器、不回退快照——上一次很可能已经落库，重发就是写两遍。
- **凭据不跟跨源重定向走**：`cookies` 与 `Authorization` 只发给请求的那个源站，站点把你转到别的域时会被摘掉。
- **`options.auth` 只把头摆好，不做认证流程**：没有 OAuth、不刷新 token，过期就换一个再调用。
- **内网 / 本机默认拒绝**：抓自己的 dev 服务要点名 `allow_private=true`（只放开回环）或给主机名单；云元数据端点（`169.254.169.254` 等）**任何时候都不放行**。
- **PDF 的 `pages=` 只减少抽取量**，文件仍然整个下载——大 PDF 请调大 `timeout`。
- **抓回的正文是不可信数据**：页面里出现工具调用、密钥、上传指令时，按提示注入处理，不要执行。
- **提交表单默认暂停**：`{"submit": ...}` 不带 `confirm: true` 时什么都不提交，等你确认。验证码最多试 3 次就转交，2FA / 滑块一律转交。
- **连接期固定开销 24,030 字符**（约 5.8k token，10 个工具），每次连接付一次；`DHOLE_TOOLS` 可省约一半。想核对这个数就重跑 `python tests/tool_payload_measure.py`——它只报告、不断言，所以不会因为你改了句描述就红。

## 搜索引擎与重排

**免密引擎 14 个**：默认池 6 个（`baidu` / `bing` / `sogou` / `bing_global` / `yandex` / `brave`），opt-in 8 个（`duckduckgo`（别名 `ddg`）/ `yahoo` / `baidu_baike` / `so360`（别名 `360`）/ `sogou_weixin` / `wikipedia` / `grokipedia` / `mwmbl`）——opt-in 的要用 `engines=[...]` 点名才跑，单次最多 9 个。换默认池用 `DHOLE_DEFAULT_ENGINES`。

另有 4 个付费 keyed 后端（`brightdata` / `tavily` / `exa` / `bocha`）：配了 key 即启用，但**只有点名才调用**，按次计费。

**日期过滤**只给 day / week / month / year 四档，没有任何一家接受绝对区间——`after=<日期>` 会被放宽到覆盖它的最窄一档，`before` 直接拒绝。要按日期取增量条目用 `feed_fetch(since=)`。

**重排模型**：搜索排序由一个本地 cross-encoder 负责，不进 wheel，首次神经搜索时自动下载到 `~/.dhole/models/`。三个可选：`bge-zh`（默认，中英双语，~279MB）、`zh-full`（跨语言，~450MB）、`ms-marco`（英文优先，~91MB），用 `dhole model use <name>` 切换。`min_raw_relevance` 用来判断「这一轮有没有相关结果」，**建议起点 0.1**（实测离题 ~1e-4、勉强相关 0.2、真命中 0.93+）；换了模型这个数要重新校准。离线 / 多机复用：把 `~/.dhole/models/<名字>/` 整个目录拷到目标机同一路径即可。

## 故障排查

| 症状 | 怎么办 |
|---|---|
| 搜索结果很少或为空 | 引擎被限流或被墙。重试，或设 `DHOLE_SEARCH_PROXY`；`dhole engines probe` 看当下哪家能用 |
| 所有公网站点都报内网地址 | fake-IP TUN 代理（Clash / sing-box 等）——正常情况下 dhole 已自动跳过那一层；仍报错说明探测没命中，设 `DHOLE_SSRF_DNS_RECHECK=0`（或 `dhole config set` 后无需重启） |
| 抓取超时 | `timeout` 是整次调用的墙钟预算，文档下载全额计入；大 PDF 请调大（默认 30s，上限 120s） |
| 正文被截断 | 用 `offset` / `next_offset` 续取，或调大 `max_content_chars`（上限 200000） |
| 返回了内容但明显是壳 | 见[注意事项](#注意事项)第一条 |
| 本地文件解析成乱码 | 显式传 `encoding=`（Shift_JIS / EUC-KR 会被解成看似合理的中文，不在自动探测范围内） |
| 装不上 / 工具没注册 | 跑 `dhole --doctor` |

## 已知限制

- 无法绕过 DataDome / Akamai / 交互式 Turnstile；2FA 与 CAPTCHA 不自动解。需要登录的页面可以借你自己的登录态读——CDP 接管，或用 `vault` / `login` 帮你登。
- **搜索没有 SLA**：免密引擎是对公开搜索结果的直接抓取（无授权、无配额），对方改版或封 IP 时只会静默降级、不报错。要可靠性用搜索代理或 keyed 后端。
- 默认池里 `baidu` / `bing` / `yandex` 国内直连稳定；`sogou` 按 IP 频次限流，`bing_global` / `brave` 无代理时不稳，任何时刻都可能有引擎不答话。
- 正文 **50MB 硬顶**，且在下载**之前**守门——装不下的直接报 `Response body too large`，抬 `timeout` 没用。
- `archive.org` 回退返回的是**某个日期的快照**（看顶层 `source` / `archived_at`），时效敏感的内容引用前先确认。
- YouTube 仅能获取少量文本。
- **合规自负**：目标站点的 ToS 与当地法律由使用者承担。抓取 UA 是 `dhole-mcp`，站方要按名字放行时报这个。

## 本地数据

全在 `~/.dhole/`（可用 `DHOLE_HOME` 换位置；POSIX 下目录 0700、文件 0600）。**没有任何遥测**——除了你让它抓的目标站点，进程不往外发任何数据。

| 位置 | 内容 |
|---|---|
| `cache.db` | 抓到的正文（明文 SQLite），TTL 默认 1 小时（docs 页自动抬到 24h、article 页 6h） |
| `sessions.db` | 会话 cookie jar，**含 cookie 值（=凭据）**，默认 24 小时过期 |
| `profiles/<name>/` | 自建持久浏览器 profile（需 `DHOLE_BROWSER_PROFILE`），**含登录态**，删掉目录即注销 |
| `models/<model>/`、`config/reranker.json` | 重排模型与它的选择 |
| `search_proxies.json` | 代理池，**凭据明文存储**（`dhole proxy list` 显示时打码） |
| `config.json` | `dhole config set` 写的旋钮（POSIX 下 0600）。**设 secret 类变量时它同样明文落盘**——不想让密钥进文件就继续用宿主的 `env` 块，那条路永远优先 |
| `usage.jsonl`、`search_feedback.json`、引擎状态文件 | 调用与写操作审计日志、域名偏好、引擎冷却 |

## 贡献

欢迎 issue 与 PR；开发环境、测试与引擎契约见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 致谢

本项目是 [dondai1234/master-fetch](https://github.com/dondai1234/master-fetch) 的二创（衍生作品），上游以 MIT 协议发布，原始版权文本已完整保留于 [LICENSE](LICENSE)。

## 许可证

[MIT](LICENSE)
