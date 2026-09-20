<div align="center">

# Dhole

**让 AI 代理访问互联网：抓取 · 爬取 · 搜索，内置反爬，$0 无密钥。**

[MIT](LICENSE) · Python 3.11+

</div>

Dhole 是一个 [MCP](https://modelcontextprotocol.io) 服务器，为 AI 代理提供网页抓取、整站爬取和无密钥网页搜索。HTTP 被拦截时自动升级到反检测浏览器，可读取 PDF（含扫描件 OCR），全部本地运行、无需任何 API key。

## 功能特性

- **智能抓取**：HTTP 优先（~1 秒），被拦截或遇到 JS 空壳时自动升级 Patchright 隐身浏览器并求解 Cloudflare 验证；全部失败时回退 Wayback Machine 快照
- **网页搜索**：多引擎并行（bing / duckduckgo / brave / yahoo / yandex 等），本地 ONNX 神经重排序，跨引擎共识排名；`fetch_content=true` 直接抓回 top3 全文
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
rm -rf ~/.dhole                 # 运行时缓存（Windows: rd /s /q %USERPROFILE%\.dhole）
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
| `DHOLE_SSRF_DNS_RECHECK` | 设 `1` 开启 DNS 解析内网复查（默认关闭） |
| `DHOLE_UPDATE_PACKAGE` | 自更新目标发行名（发布自己的发行版后设置以启用） |
| `DHOLE_UPDATE_INDEX_URL` | 自更新/自愈时传给 pip 的 `--index-url`（不设则用 pip 默认源） |

### Bright Data SERP 后端

设了 `DHOLE_BRIGHTDATA_API_KEY` 即启用，没有额外开关。它向 `api.brightdata.com/request`
请求 `google.com/search` 的结果页（`data_format=parsed_light`），与免费引擎**并行**执行。

- **配额按次消耗**：每次真正发起的搜索都会附带一次付费调用；命中搜索缓存则直接返回，不调用
- **`engines=["brightdata"]` 可单独选它**：只跑付费后端；未配 key 时报错直指 `DHOLE_BRIGHTDATA_API_KEY`，不会把你引去查代理。可选引擎名全部来自 `_DHOLE_TO_BACKEND` 这一张表 —— 8 个免密的加它
- **不拉高共识门槛**：`min_engines = min(3, 免费引擎数)` 只按免费引擎计算，它不计入
- **提前返回时不取消**：免费引擎凑够结果触发早退时，其余任务被 cancel，但 Bright Data 会等它跑完，避免已花出去的配额白花
- **失败大多静默**：非 200 或任何异常都返回空列表、只记 debug 日志，不影响本次搜索结果。**401/403 除外** —— key 错误或过期会抛 `BrightDataAuthError`，在 `status` 里显示为 `error:BrightDataAuthError`，不会伪装成「没有结果」；它也不触发熔断（key 错了冷却 60 秒毫无意义）
- 单次 HTTP 超时取 `max(DHOLE_SEARCH_DEADLINE, 20)`：默认（deadline 16 秒）下仍是 20 秒 —— SERP 渲染要时间，且配额在请求发出那一刻就已花掉；但你调高 deadline 它会跟着涨
- 失败响应体写日志前会先脱敏。注意 `security.redact_api_key()` 的正则只认 `sk-`/`pk-`/`api_key-` 前缀和带凭据的代理 URL，**盖不住 Bright Data 自己的 key 形状**，所以额外拿已知 key 做了定向替换

## 已知限制

- 无法绕过 DataDome / Akamai / 交互式 Turnstile；需要登录的网站不在设计范围内
- duckduckgo / brave / yahoo 需要 VPN 可达（bing / yandex 国内直连）
- 搜索引擎限速时熔断器自动冷却 60 秒，重度使用建议配置代理
- YouTube 仅能获取少量文本

## 贡献

欢迎 issue 与 PR，规范见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 致谢

本项目是 [dondai1234/master-fetch](https://github.com/dondai1234/master-fetch) 的二创（衍生作品），上游以 MIT 协议发布，原始版权文本已完整保留于 [LICENSE](LICENSE)。`src/dhole_mcp/search_metasearch.py` 派生自 [ddgs](https://github.com/deedy5/ddgs)，声明见 [NOTICE.ddgs.txt](NOTICE.ddgs.txt)。

## 许可证

[MIT](LICENSE)
