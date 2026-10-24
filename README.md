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
| `web_act` | 在页面上动手：点击、填写、按键、拖拽、滚动、截图、**上传文件**、**应答弹窗**、**多页签**；提交默认暂停，等你点头 |
| `web_request` | 发一次非 GET（POST / PUT / PATCH / DELETE / HEAD），只发一次：不重试、不缓存、不升浏览器 |
| `web_search` | 10 个免密引擎并行 + 本地重排，可顺带抓回前三条全文；另开五类纵向源（新闻 / 论文 / 问答 / 代码 / 社区） |
| `web_research` | 一个问题，一次调用，一份带 `[n] URL` 引用的证据包；坏的源自己说明为什么，从不拖垮整包 |
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

- **零密钥**：10 个免密搜索引擎、五类纵向源、反爬升级、重排模型，没有一项需要注册或充值。可选的 `tavily` / `bocha` 只在你自己配了 key 并且点名时才调用。
- **零模型**：判断归外层 agent，dhole 只做确定的工具层。同一份页面两次抓取，同一个结果；不会有一个小模型在背后替你决定「这页大概讲什么」。
- **全本地**：正文缓存、cookie、重排模型都在 `~/.dhole`；除了你让它抓的目标站点，进程不往外发任何数据。
- **失败说真话**：每次调用都回同一份 JSON——抽了多少字、走的哪一档、下一步该调什么。抽空了就说空了，不会把一张反爬灰页包装成「成功正文」。

## 配置

两条门：宿主配置里的 `env` 块（改完要重启），或 `dhole config set NAME value` 写进 `~/.dhole/config.json`（下一次调用就生效）。**env 永远优先**。默认行为通常就是你要的，不确定就先别设。

<details>
<summary>全部 15 个旋钮</summary>

| 变量 | 什么时候设 |
|---|---|
| `DHOLE_SEARCH_PROXY` | 搜索引擎被限流 / 被墙时（也自动读 `HTTPS_PROXY`） |
| `DHOLE_IGNORE_ROBOTS` | 关掉进程级的 `robots.txt` 遵从；单次豁免用 `ignore_robots=true` |
| `DHOLE_ALLOW_PRIVATE_HOSTS` | 放行内网 / 本机地址，默认一律拒绝 |
| `DHOLE_HTTP_TOKEN` | `--http` 传输的 bearer 门；不设则端口不设防 |
| `DHOLE_PERSIST_CREDENTIALS` | 把会话 cookie 与凭据落盘（Windows DPAPI 加密）。默认只在内存，进程结束即清 |
| `DHOLE_USAGE_LOG` | 本地调用与写操作日志，不记参数值、不联网 |
| `DHOLE_TOOLS` | 只注册列出的工具，**写了就等于关掉其余** |
| `DHOLE_DEFAULT_ENGINES` | 覆盖默认搜索池 |
| `DHOLE_SEARCH_ROW_FILTER` | `off` 关掉 SEO 农场行过滤（默认开） |
| `DHOLE_DEFAULT_CONTENT_CHARS` | 默认正文预算，40000（500–200000） |
| `DHOLE_TAVILY_API_KEY` / `DHOLE_BOCHA_API_KEY` | 两颗 keyed 引擎的密钥，配了才启用、点名才调用 |
| `DHOLE_HF_ENDPOINT` | 重排模型下载源，默认 huggingface.co，失败回退镜像 |
| `DHOLE_RERANK_MODEL` | `bge-zh`（默认，中文更强）或 `mmarco`（快档，约 1.8×） |
| `DHOLE_HOME` | 状态目录，默认 `~/.dhole` |

</details>

<details>
<summary>搜索引擎与重排</summary>

默认池 10 个免密引擎：`baidu` / `bing` / `sogou` / `so360`（`360`）/ `bing_global` / `yahoo` / `yandex` / `brave` / `duckduckgo`（`ddg`）/ `marginalia`。`engines=` 只能收窄，不能新增。索引家族是 7 个：`yahoo` 与 `bing` / `bing_global` / `duckduckgo` 同属 Bing（多入口、不多家族），`marginalia` 是独立小网索引。另有 `tavily` / `bocha` 两颗 keyed 后端。

重排用本地 cross-encoder，首次用到才下载（`bge-zh` ~279MB / `mmarco` ~119MB），换档要重启宿主。没有模型时按查询词覆盖率分桶排序，不丢行。冻结语料上的公开评测：`python evals/vertical_search_baseline.py`、`python evals/ranking_baseline.py --tiers`，方法与两次采样见 [evals/README.md](evals/README.md)。

**我们不为免密公共 API 承诺 SLA**；能承诺的是失败如实进报告、不冒充「没结果」。

</details>

<details>
<summary>上下文成本</summary>

全量连接期开销 36,291 字符（约 8.8k token，13 个工具，实测）。按用法裁剪时：读 / 搜 / 请求 / 爬 / 文档 / 订阅 / 状态 / 指南这 8 颗是 25,952 字符，加 `web_discover` 共 9 颗 28,274——被裁的工具连自己的路由行一起退出。别按单颗体积挑「最小的 N 颗」，那会把 `web_read` / `web_act` 这类主力排除在外。

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
| `dhole doctor` | 安装体检，逐项给修复命令 |
| `dhole config list` / `set` / `unset` | 改旋钮，看当前值与来源 |
| `dhole engines list` / `probe` / `reset` | 引擎冷却状态、实测健康、清空 |
| `dhole watch add` / `list` / `run` | 页面变更清单；`run` 退出码 0 无变化 / 1 有变化 / 2 检查失败，`--notify` 弹桌面通知 |
| `dhole skill install` | 把随包说明书装成宿主原生 skill |
| `dhole -v` | 版本 + 能力面板 |

监控是轮询不是推送：频率归你的 cron / systemd timer。没有 webhook 出口，是决定不是缺口——dhole 是只读的本地工具层，不变成拿着页面内容往外 POST 的进程。

## 本地数据

全在 `~/.dhole/`（`DHOLE_HOME` 可换；POSIX 下目录 0700、文件 0600）。正文缓存 `cache.db` 是明文 SQLite，TTL 1 小时（article 6h）——`web_recall` 搜的就是它。会话与凭据默认只在内存，`profiles/` 存的是持久浏览器 profile，**含登录态**，删目录即注销。没有任何遥测。

## 文档

- [CONTRIBUTING.md](CONTRIBUTING.md) —— 开发环境、测试、引擎契约
- [evals/README.md](evals/README.md) —— 公开评测的方法与读数
- [CHANGELOG.md](CHANGELOG.md) —— 每个版本改了什么，以及为什么
- 手册两个入口，同一份语料：`sys_guide(topic=...)` 或 `dhole skill install`

## 致谢

本项目是 [dondai1234/master-fetch](https://github.com/dondai1234/master-fetch) 的二创（衍生作品），上游以 MIT 协议发布，原始版权文本完整保留于 [LICENSE](LICENSE)。

## 许可证

[MIT](LICENSE)
