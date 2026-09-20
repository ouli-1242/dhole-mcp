# 更新日志

本文件在 11.1.8 版重新建立。更早的发布**未**在此回溯补记——历史请查看
`git log` 与 GitHub releases 页面。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

> **版本说明。** 自 13.14 起本仓库为个人衍生作品，不再跟随上游项目的发布，
> 版本号是自己的，与上游版本不可比。`src/dhole_mcp/__init__.py` 中的
> `__version__` 是版本的唯一权威来源。

## [14.2] - 2026-09-20

### 新增
- **KeyedApiEngine 抽象 + 三个新 keyed 引擎：`tavily` / `exa` / `bocha`。**
  均为 POST JSON + 密钥的 API 后端，`engines=` 按名选择，**默认不跑**
  （每次调用都消耗真实配额）。博查国内裸网直连、Bing 同源索引，`timelimit`
  自动映射 `freshness`（oneDay/oneWeek/oneMonth/oneYear），`summary` 默认关；
  Tavily 自带 content 正文，默认 basic 深度（1 credit）；Exa 无 key/欠费
  实测回 402，归入鉴权失败。密钥：`DHOLE_TAVILY_API_KEY` /
  `DHOLE_EXA_API_KEY` / `DHOLE_BOCHA_API_KEY`
- **搜狗微信引擎 `sogou_weixin`**：免费、国内裸网直连（实测 ~0.2s）、独家
  微信公众号内容池，纯 HTTP GET 无需浏览器层；结果 href 为搜狗 /link
  跳转包装，如实返回（会过期）
- **`DHOLE_DEFAULT_ENGINES`**：覆盖免密默认池（逗号分隔），国内用户可收敛
  到直连可达引擎；未设用上游默认 5 个，未知名忽略并告警
- **免密引擎连接失败连续冷却**：连续 3 次 DNS/拒连/超时（通常是被墙）冷却
  10 分钟并持久化，任何一次成功清零——被墙引擎不再每轮搜索陪跑

### 修复
- **Bright Data 的 401/403 不再伪装成「没有结果」**：抛 `BrightDataAuthError`
  进 status（`error:BrightDataAuthError`），且不触发 60 秒熔断——key 错了
  冷却不会变好
- **失败响应体写日志前脱敏**：`redact_api_key()` 的正则认不出各家的 key
  形状，额外用已知 key 定向替换
- **Bright Data HTTP 超时跟随 `DHOLE_SEARCH_DEADLINE`**（原硬编码 20s，
  调高 deadline 对它无效），下限仍 20 秒

### 变更
- **keyed 引擎策略：显式点名才执行**。Bright Data 原先是「设了 key 每次
  搜索必调」；三个付费引擎并存后隐式全开等于每搜三笔配额，故统一为
  `engines=` 点名才调用，免费默认池不变
- `engines=` 合法名单改为从 `_DHOLE_TO_BACKEND` 单一来源派生（原先两份
  手工名单靠人肉同步）

## [14.1] - 2026-09-20

### 移除
- **下线 14.0 的全部改名兼容层。** 确认所有 client 配置均已迁移到
  `dhole` / `dhole_mcp` / `DHOLE_*`：
  - 删除 `hound` CLI 命令别名（`[project.scripts]`）
  - 删除 `hound_mcp` 兼容模块（`python -m hound_mcp` 不再可用）
  - 删除包导入时的 `HOUND_*` → `DHOLE_*` 环境变量自动迁移；client 配置
    里若仍写着 `HOUND_*`，现在会被直接忽略，需改为 `DHOLE_*`

## [14.0] - 2026-09-20

### 重大变更（破坏性）
- **项目更名：`hound-mcp` → `dhole-mcp`。** 原名与 PyPI 上的其他项目撞名，
  自更新也因此长期关闭。改名覆盖：
  - Python 包目录 `src/hound_mcp/` → `src/dhole_mcp/`，CLI 命令 `hound` → `dhole`
  - 环境变量前缀 `HOUND_*` → `DHOLE_*`（全部读取点）
  - 数据目录 `~/.hound` → `~/.dhole`，缓存 `~/.hound_mcp_cache` →
    `~/.dhole_mcp_cache`（旧缓存不迁移，TTL 到期自然重建）
  - 日志名 `hound-mcp.*` → `dhole-mcp.*`，仓库地址同步更名
    （`github.com/ouli-1242/dhole-mcp`；GitHub 仓库本身需在 Settings 里改同名）
- **自更新模块注释按新语义改写。** `_DIST_NAME` 指向本仓库自己的分发名；
  `dhole-mcp` 发布到 PyPI 后用 `DHOLE_UPDATE_PACKAGE` 打开自更新。

### 兼容措施（迁移期零破坏）
- 旧环境变量 `HOUND_*` 在包导入时自动迁移为 `DHOLE_*`（同名 `DHOLE_*`
  优先），既有 client 配置里的 env 无需改动。
- 保留 `hound` CLI 命令别名与 `hound_mcp` 兼容模块（导入时发
  DeprecationWarning），`python -m hound_mcp` 等旧启动方式继续可用；
  确认全部 client 配置已迁移后可删除这两处兼容层。

## [13.16] - 2026-09-20

### 修复
- **`links.py` 的 `_norm_host` 会损毁 w 开头的域名。** 它用 `lstrip("www.")` 剥离
  `www.` 前缀，但 `lstrip` 是按字符集剥离而非剥字符串：`wikipedia.org` 变成
  `ikipedia.org`、`web.example.com` 变成 `eb.example.com`、`www.wikipedia.org`
  变成 `ikipedia.org`。这类域名的所有比较与 `.{domain}` 后缀匹配（外链分类、
  primary source 识别）因此全部失效。现改为 `startswith("www.")` + 切片，
  userinfo/端口交给 `urlparse().hostname` 处理。
- **`fetcher.py` 的 `ElementWrapper.text_content()` 只返回元素自身首段文本**
  （`.text`），嵌套子元素的文字被静默丢弃。现改为拼接 `itertext()`，与 lxml
  原生 `.text_content()` 语义一致。（既有的 3 个测试恰好都用叶子元素，两种
  实现都能通过——现已补嵌套内容回归测试钉住正确行为。）
- **`smart_search(fetch_content=true)` 静默吞掉单页抓取错误。** 某条 top 结果
  抓取失败时直接从 `fetched_pages` 中消失。现在失败也占位，带
  `content_ok=false` 与脱敏后的 `error` 字段，Agent 能看到缺口及其原因。

### 变更
- **清除"刻舟求剑"式测试（-13 +1）。** 删除把当前实现快照进断言的变更
  探测器：4 个签名/属性存在性测试（`inspect.signature` 查参数、
  `hasattr` 查字段——只在改名时报假警，抓不到行为 bug）、2 个搜索引擎池
  数量/成员冻结（`DEFAULT_ENGINES` 是数据不是逻辑，增删引擎属正常演进）、
  6 个隐身层冻结断言（内存调优参数、单个 stealth flag 字符串、指纹档案
  数量、语言/设备内存枚举）。保留并改造了真契约：关系型不变量（自有参数
  永不含有害自动化参数）、档案自洽（MacIntel⇒Apple GPU、GPU 厂商多样性、
  plugins 非空）、跨引擎共识正确性（DDG/Yahoo 必须同属 Bing 索引家族）。
  test_cli 的 `hasattr` 结构检查替换为真行为测试：子进程中用 import hook
  屏蔽全部重依赖后导入 `dhole_mcp.cli` 必须成功——这才是"坏安装下自愈
  入口仍可用"的测法。
- **`get()`/`bulk_get()` 的 `auth` / `proxy_auth` 现在真正生效**（闭合 13.x
  记录的已知缺口）：`auth` 转成 Basic `Authorization` 头（绝不覆盖调用方
  显式设置的头）；`proxy_auth` 经 URL 编码后嵌入代理地址交给 HTTP 层；显式
  `proxy_auth` 优先于代理串里已嵌入的凭据。dict 代理
  （`{server, username, password}`）现在也能到达 HTTP 层——此前所有
  `proxy if isinstance(proxy, str) else None` 写法都把它静默忽略，
  `smart_fetch` 带 dict 代理时只有浏览器层走代理、HTTP 层直连。
- **依赖刷新，约束两端均验证通过：** mcp 2.0 → 2.2、pydantic 2.13.5、
  trafilatura 2.2、httpx 0.27 → 0.28（dhole 早已使用新的 `proxy=` API）、
  anyio/starlette/uvicorn/lxml/beautifulsoup4/cssselect/h2/markdownify 升至
  最新，primp 1.3 → **2.0**（主版本）。primp 2.0 移除了响应的 `.reason`
  属性——`fetcher.py` 已有 `hasattr` 守护；完整测试套件 + 真实冒烟（构造
  参数、`headers_update`、`follow_redirects=False` 逐跳重定向解析、cookies）
  在 1.3.1 与 2.0.1 上均通过。patchright/playwright 保持 1.61 以匹配本地
  已安装的浏览器。
- **工具可发现性重写（为什么模型此前很少主动使用 dhole）。** 模型选工具
  取决于描述的第一句话，而 dhole 的描述原本是功能清单。8 个工具全部改为：
  先说任务触发场景 + 显式声明"用本工具而非内置 WebFetch / web search" +
  理由（反爬绕过、JS 渲染、PDF/OCR、无密钥引擎），功能/信号细节后置。
  connect-time `instructions` 同样重写：祈使句路由表，"prefer dhole over
  built-ins" 放首行。顺带修正两处过时描述：默认引擎池补上 `bing`（代码
  默认早已包含它），删除已失效的 "'bing' maps to yahoo" 映射说明。
- README 环境变量表现在列出代码实际读取的全部变量
  （`DHOLE_SEARCH_DEADLINE`、`DHOLE_BRIGHTDATA_*`、`DHOLE_SSRF_DNS_RECHECK`、
  `DHOLE_UPDATE_*`）。

### 新增
- `include_media` 现在也能抓取懒加载图片：当 `src` 为空或 `data:` 占位图时，
  从同一个 `<img>` 标签读取 `data-src`（`src` 是真实 URL 时仍优先）。

## [13.14] - 2026-09-10

### 修复
- **日志凭据泄漏。** `fetcher.py` 重试时把原始异常文本写进 `logger.warning`，
  含 `user:pass@` 的代理 URL 会落进日志。现统一走 `security.redact_api_key()`
  （代码库其他地方已在用）。
- **`RuntimeWarning: coroutine 'ProxyPool.health_check' was never awaited`。**
  `_kick_health_check()` 在调用 `asyncio.create_task()` 之前就求值了
  `pool.health_check()`，没有运行中的事件循环时协程被创建后即丢弃。现在
  先检查事件循环。
- **过期的类型标注。** `server.py` 两个辅助函数仍标注已不存在的
  `_ScraplingResponse`；`ocr.py` 和 `server.py` 引用 `PdfResult` /
  `CrawlResponseModel` 但未导入。标注是惰性的所以没有崩溃，但类型是错的。
- 清理死赋值（`browser.py`、`pdf_extractor.py`、`search_engines.py`、
  `server.py`），重命名歧义变量 `l`。

### 变更
- **版本单一来源。** `pyproject.toml` 不再硬编码 `99.0.0`，改经
  `[tool.hatch.version]` 读取 `src/dhole_mcp/__init__.py` 的 `__version__`。
  此前 `pip show` 报 99.0.0、CLI 报 11.1.8、package.json 写 11.1.6，三处
  不一致。
- 补声明"被导入但未列出"的依赖：`beautifulsoup4`（`search_engines.py`
  导入——缺失时该代码路径静默降级为空结果），以及仅经
  `httpx[http2,socks]` 传递解析的 `h2` 和 `httpcore`。
- **自更新不再指向上游包。** 本仓库是个人衍生作品，但 `dhole -u` 原先执行
  `pip install dhole-mcp==<latest>`——那是*上游*发行版，会把上游代码装进来
  覆盖本 fork。自更新现默认关闭：`dhole -v` 显示 "self-update off"，
  `dhole -u` 拒绝执行（含显式版本号）并指向
  `git pull && python -m pip install -e .`。发布自己的发行版后可用
  `DHOLE_UPDATE_PACKAGE=<发行版名>` 加可选 `DHOLE_UPDATE_INDEX_URL`
  重新启用。生成的修复脚本与 Windows 助手跟随同一发行版名。
- 自 13.14 起版本号是本 fork 自己的，与上游版本不可比。
- 移除代码注释与测试分组标题里的 `(upstream v12.0.0)` 式溯源标记。
- 删除 `package.json` 里悬空的 `pi.extensions` 条目：它指向的
  `pi-extension/extensions/dhole.ts` 不在本仓库中。

### 新增
- `.github/workflows/test.yml` 与 `.github/workflows/lint.yml`——仓库此前
  没有 CI，测试套件和 lint 无强制。
- `CONTRIBUTING.md` 与本 changelog。
- `[tool.ruff]` 配置及 `dev` extras 中的 `ruff`。

### 已知缺口
- 抓取工具的 `auth` / `proxy_auth` 只校验不生效：`HTTPSession` / `http_get`
  不接受它们。当时保留原行为；见 `src/dhole_mcp/server.py` 中 `bulk_get`
  的注释。（已于 13.15 修复。）
- `LICENSE` 与 `NOTICE.ddgs.txt` 保留原始版权声明。停止追踪上游*版本*并
  不改变这一点：除非重写代码，MIT 要求保留这些声明。
