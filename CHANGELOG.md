# 更新日志

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的格式：每个版本按 **新增 / 变更 / 弃用 / 移除 / 修复 / 安全** 的顺序分组，只列有内容的那些；日期用 ISO 8601；最新版本在最前。版本标题在结尾有对应 diff 链接（未打 tag 的版本不链接）。本文件在 11.1.8 重新建立，更早的发布未回溯补记（见 `git log`）。

> 自 13.14 起本仓库为个人衍生作品，版本号是自己的序号、不承诺语义化版本，与上游版本不可比；`src/dhole_mcp/__init__.py` 的 `__version__` 是唯一权威来源。

## [15.0] - 2026-09-22

搜索引擎重编组（默认池 6 + opt-in 8）、第三轮外部复测处置、工具入口修复。

### 新增

- **`baidu`**（百度搜索，国内直连、独立索引）：同时跑两套版面，各出真页面契约（A 版 `div.c-container` + `mu` 属性，B 版 `div.c-result` + `data-log` JSON）；跳转包装（`baidu.com/link`）与百度自家信息流占位链接一律不交。
- **`baidu_baike`**（百科条目页，opt-in 知识库）：取 `/item/{query}`、最多一条结果；必须带 Referer，200 + 几 KB「安全校验」拦截页判为**被拦**（进熔断），不冒充 empty。
- **`so360`（别名 `360`）与 `sogou`**（国内 opt-in）：真链在 `data-mdurl` / `data-url`（会过期的跳转包装不交出）、分页有效；两家都固定 `chrome+windows` 指纹才稳定（`_PrimpClient` 因此多 `impersonate_os`）。
- **`bing_global`（www.bing.com 国际版）与 `mwmbl`（社区小索引，JSON API）** 两个国外 opt-in：前者与 cn 版结果标题仅 1/17 重合、但仍是 bing 家族；后者 `title`/`extract` 都是 `[{value,is_bold}]` 分词数组，漏拼会静默丢摘要。
- **`parse` 新增 `cwd` 参数**（相对路径的解析基准）。宿主进程的 cwd 是它自己的安装目录（实测 `D:\Program Files\Qoder`）、MCP roots 在 SDK 里已弃用（`mcp/server/session.py` 的 `roots capability is deprecated as of 2026-07-28 (SEP-2577)`）、`DHOLE_WORKDIR` 要 host 配置 —— `cwd` 是唯一「agent 从自己的 system prompt 读到工作目录、一次调用带上就生效」的通道，因此排在最前：`cwd` → `DHOLE_WORKDIR` → 服务器进程 cwd → 家目录。旧描述里的 "resolve against cwd" 指的其实是服务器进程 cwd（agent 读成自己的 cwd 正是这轮「解析到宿主安装目录」的成因），措辞已分开。

### 变更

- 默认池 → `baidu,bing,yandex,brave,duckduckgo,yahoo`（6 引擎 / 4 家族）。
- **429 计入「被拦」**（RFC 6585）：实测 brave 换出口 IP 后回 429 + 反爬壳，旧行为把它记成 empty。
- `sogou_weixin` 与 `sogou` 合并为同一索引家族（同源索引不虚报共识）；`_CORE_QUERY_ENGINES` 补中文索引引擎（不吃英文展开词）；`_clean_result_href()` 统一「从 data 属性取真链」路径的卫生（拼接 URL / 引擎自家链接丢弃）。
- `dhole -v` 的 search pool 行改为从 `DEFAULT_ENGINES` 推导（原来写死在 `updater.py`，换池漏改）。
- README 精简（314 → 212 行）并单列「可选的搜索引擎」表，由测试钉住不漏引擎；描述里默认池与 opt-in 名单同步（wire：`smart_search` 419 → 439 tokens、`tools/list` 2,831 → 2,911、连接合计 3,170 → 3,250，均为 cl100k_base、按 `json.dumps` 默认渲染逐工具求和）。
- 放弃并记录证据的引擎（避免重复试错）：头条、Mojeek、Startpage、Qwant、Ecosia、Yep、Stract、RightDao、Presearch、Ask/AOL/Lycos、Dogpile、MetaGer/OneSearch、Naver、Seznam（可解析但赞助卡片与有机卡片同构、类名逐构建哈希）、SearXNG 公共实例（同一实例在几分钟内 0 ↔ 20 条反复横跳）。
- 第三轮复测判定（无代码改动）：`path_exclude` 传字符串那条 14.7 已修（复现方应为更早的构建）；`related_queries` 时有时无属设计内；parse 相对路径是 MCP 宿主 cwd 的架构限制。
- **软 404 检测评估后不做**（记录证据，避免重复试错）：62 条真实 URL 实测 —— 30 条当前 awesome-list 外链（0 硬 404 / 0 软 404）、24 条 2015~2016 年旧清单外链（1 硬 404 / 0 软 404）、8 条伪造 SPA 深路径（vue/react/next/tailwind/medium 全部正确回 404，仅 1 条软 404）。唯一那条软 404（hashnode 的「User not found」）已被现有 `js_shell` 检测判成 `content_ok:false`，**全样本 0 例「200 + content_ok true + 错误页正文」**；且它在本项目中「200 + 正文 <1KB」这一档里只占 1/5，另 4 条是薄落地页/已有检测/正常小文档页 —— 朴素判据 80% 误报，收益换不来「悄悄换成 Wayback 旧版本」的风险。

### 移除

- `sogou_weixin` 移出默认池（垂直索引只覆盖公众号，会稀释通用搜索），**保留注册**：`engines=["sogou_weixin"]` 仍可显式搜公众号。

### 修复

- **顶层参数被静默丢弃**：顶层 `max_pages=3` 爬了 10 页、`path_include` 完全不过滤、`cache_ttl=0` 照样命中缓存 —— 现在 `_dispatch` 按工具维护白名单、**顶层优先**，不认识的键直接报错并列出支持集，`null` 视为未设置。
- **`parse` 描述漏列 `.htm`/`.xhtml`**：`SUPPORTED_EXTENSIONS` 一直含这两个，描述只写到 `.html`（`file_path` 说明连 `.htm` 都没有）—— 手里是 `.xhtml` 的用户会以为读不了，白转一次格式。四处描述（工具描述、schema 属性、方法签名与 docstring）与 README 表格补齐，并由 `test_parse_advertises_every_supported_extension` 按代码里的集合反查描述（又一处「同一份名单多处定义」）。
- **`parse` 拒绝纯文本时不再只报「不支持」**：`.txt`/`.md`/`.log` 这类有意不收（纯文本没有转换可言，agent 自己的读文件工具就够；真要收还得处理 GBK/UTF-16 解码，为零收益格式加一份编码阶梯不划算）。但报错原本只列支持集，agent 读到的是「这个文件读不了」——正确动作（直读）反而不会被做。现在每条拒绝路径都带一句指路，且刻意不维护「哪些算纯文本」的清单（清单必漏，而漏掉的正是最需要这句话的那次）。
- **失败时正文里塞占位文本（图片与「.pdf 却回 HTML」两条分支）**：实测抓任意 SVG 得到 `content: ["[Image page - OCR failed: …]"]` —— 与 14.6 BUG-5 同一条规则（失败时 `content` 为空、错误只留在 `error`），当时只改了抓取异常分支，这两条漏了。清空正文后又补了 `_never_escalate()` 并接进升级判定：空正文 + 200 正是 `_is_js_shell` 升级浏览器的条件，图片页会白烧 30~40s 换来同一张图（图片判定按 `content-type`、`.pdf` 判定按 URL，且**只挡浏览器层、不挡 archive 回退** —— 早期返回会把 404 的图片 URL 连同 Wayback 一起没收）；`image_ocr_*` 的恢复建议搬到 `next_action`（按错误分流而非 `page_type=="image"`：OCR 成功的图片也是 image）。扫描件 PDF 的正文说明**保持原样** —— 那是 `pdf_extractor` 有意的契约，`tests/test_pdf_real.py` 钉着。

## [14.7] - 2026-09-22

第二轮外部测试报告（10 条）的处置：7 条为真、1 条半真、1 条已修、1 条误判（按实测真因另修）。该报告用**自己的** BUG-1..10 编号，与 14.6 那批同名不同物，本节一律记作「报告2-BUG-n」。

### 修复

- `options.cookies` 传 Cookie 字符串被逐字符迭代丢弃 → 列表 / 字典 / `Cookie` 头三种形态都收。
- `options.useragent` 在 HTTP 层完全不生效 → 穿到 `HTTPSession`，显式值优先；顺带修 `extra_headers` 头名大小写重复。
- schema 标量改取「第一个非空」匹配（首个匹配是图片链接时曾返回空串）；`attribute` 从未实现，现已按属性取值。
- crawl 三连：`path_include`/`path_exclude` 传字符串会静默清空整站；`options.search` 被拒（顶层一直可用）；过滤后 summary 仍报过滤前统计。
- 本地 PDF：`parse` 丢掉 ToC 与 metadata → 补 `parse_file_detailed()`（同时带上提取器的 `content_ok`，否则健康 PDF 会报 `content_ok:false`）。
- actions 档默认预算 30s → 60s（actions 必走浏览器层），派发层不再替调用方预填 timeout。
- 实测复核另修两条：`schema` + `max_content_chars` 同用时选择器跑在被截断的 HTML 上（抓取上限固定 200k，截断只作用于返回 JSON）；空 body 的 4xx 被误判成 JS shell（改为如实 `http_error_4xx`）。

## [14.6] - 2026-09-22

四批改动 + 一批审计遗留清理。前三批的共同毛病：**调用看起来成功了，但实际没做它承诺的事**。

### 新增

- `dhole proxy` 子命令（此前模块文档承诺、命令并不存在）：add / list / remove / clear，失败一律出声，凭据永不上终端，并说明当前生效的是哪个代理环境变量。
- `dhole --doctor`：回答「装得对不对」——启动器、模块实际加载路径（editable vs wheel 一眼可见）、元数据一致性、残留进程、状态目录可写性、代理池；每项失败给出可复制的修复命令，退出码 1。
- 引擎健康可重置：`cache_clear(engine_state=true)` 立即忘掉冷却与产出记录并回报 `engine_state_reset` + `engine_health`；`dhole engines list|reset` 是同一件事的 CLI 入口；`circuit_open` 报告带重试倒计时。
- `DHOLE_NO_BROWSER_PREWARM`：设 `1` 后启动不预热隐身浏览器（离线机器 / 计费网络不必接受这笔账）。

### 变更

- 工具描述与 instructions 收敛：补缺失的路由规则（已有 URL 列表用 smart_fetch）、修自相矛盾、删重复与营销话术（connect 3,774 → 3,264 tokens）；描述自此由测试当契约钉住（路由 / 事实一致 / 体积预算）。

### 修复

- 响应契约：失败时不再把错误/占位文本塞进正文（改 `content:[]` + `error`）；入参校验失败改为结构化结果（`status:0`），不再 raise 成 MCP 错误。
- `smart_fetch` 的 `timeout` 变成真预算：逐层按剩余预算收敛，耗尽返回结构化 `timeout:` 结果（不再是客户端 -32001）；浏览器会话等待也纳入预算。
- `css_selector` 在 markdown 模式下静默失效 → 四种模式一致；`parse` 相对路径改为 cwd → `DHOLE_WORKDIR` → 家目录，并补齐信封（`total_extracted_chars` / `summary` / 截断规则）。
- 反爬墙检测：新增 `_is_bot_wall()`（HTTP 200 上的验证码/登录墙，带长度门）；`page_type` 补 `auth_wall` / `captcha`；墙的 `next_action` 此前写在永远不会触发的分支里。
- 引擎有产出但被自家相关性过滤全丢时不再零解释（`error` 说明「引擎给了 N 条、被判离题」）；`site=` 在改写轮不再被放弃；`related_queries` 按域名去重、丢掉虚词碎片。
- 其它 agent 信号：article 的 author/date 从 OG/JSON-LD 回填；`extracted_type` 如实回填；`max_results` 越界给出说明；`content_age_days` 用 `null` 表示未知；list 页 `next_action` 不再指向导航栏；`options` 字符串形态统一解析。
- 审计遗留：代理探活只在有冷却/死代理时才外发；410 真的走 archive 回退；模型离线复用写法写明；新增 `tests/test_import_provenance.py`（断言验的是本 checkout 而不是 site-packages）；fixture 哈希改按仓库字节记录（换新克隆不再红）。
- 首次调用 -32001 的根因：首次缓存访问时的旧目录同步搬移阻塞事件循环（搬 120MB 模型实测停摆 2.09s）→ 改 `asyncio.to_thread` 并提前到启动预热。
- `parse` 本地 PDF 无路可走：`file://` 在 URL scheme 黑名单里，旧提示的三条路全不通 → 改为 parse 自己解析（与 URL 路径同一个提取器，不放宽任何安全边界）。

## [14.5] - 2026-09-21

主题：让静默降级可被观测、把说得太满的地方改准。

### 新增

- 每引擎解析产出统计：item_nodes / usable / post-filter kept 三个整数落 `engine_stats.json`（判据不依赖历史基线），`dhole -v` 新增 `engine yield` 行。
- 默认池解析器契约测试：期望条数由独立 oracle（bs4，与被测的 lxml+xpath 不共享 parser）算出；fixture 内容寻址、禁止无主 fixture、不写年龄断言。
- `DHOLE_HOME`：状态目录可换位置（POSIX 下 0700 / 文件 0600）。

### 变更

- 默认池 5 → 6 引擎、3 → 4 家族（`sogou_weixin` 进池：国内直连、独家公众号内容池），随之处理：垂直索引在无重排器时排在通用结果之后、不能独自填满早退配额（否则会把还在跑的 yandex 提前 cancel）、拿原始 query、结果 href 如实返回跳转包装。
- 修正 `--cache-ttl` 死参数、意图展开集合里的不存在引擎名、README 两处过度承诺；浏览器层 `--host-resolver-rules` 计划**未实施**（无可复现的验证手段，降级为文档写明）。

### 修复

- `engines_consensus` 不再在降级池上伪装「全员一致」：分母改为由配置池推导的家族全集，新增 `consensus_basis`；只剩一个家族时写 `1 of 1 (no corroboration)` 而不是比率。降级标注也不再在缓存命中时丢失。
- `empty` 不再隐形：新增 `engine_empty` / `engine_preempted`；多样性警告的分母算上 empty（此前「5 个里 4 个解析器坏了」被读成 1/1 健康）。
- 零结果时的自动改写按原因处置：解析器漂移（`item_nodes>0 且 usable==0`）不再重打一轮全量 fan-out，也不再把自己的故障说成「查询该换个说法」。
- 口令 PDF 不再被误报成「文件打不开」：改为按异常**类型**判定（此前按消息文字找 password，真机上永远命不中），「没给口令」与「口令被拒」分开说明。
- Bing 双版面事故：`<a>` 挂在 `<h2>` 祖先上的那一版让整轮 bing 为空（且状态记成 empty）→ href xpath 改并集 + `ancestor::a` 回退，不绑样式类名。
- 三个状态文件的落点改为惰性求值（`DHOLE_HOME` 曾只对一部分文件生效），并让测试套件不再改动用户真实的冷却状态与域名偏好。

### 安全

- 浏览器层补齐 SSRF 守卫：请求前拦截页面 JS 的 fetch/XHR、iframe 与 JS 跳转，落地后再判一次，内网正文一字不回流、不重试（残余：HTTP 3xx 重定向的目标仍会「盲打」一次）。
- 缓存指纹补 PDF 口令维度（带口令解出的正文曾与匿名行撞键、可被匿名请求复读），旧库做一次性定向清理。
- hosts 豁免按「钉到哪个值」判定：`0.0.0.0` 这类屏蔽用黑洞不再自动放行（`127.0.0.1` 等本机开发覆盖维持豁免）。
- `tcp_preflight` 不再是内网端口 oracle（命中内网时返回与「真的不可达」同形的类别）。
- 自愈重装钉在当前已装版本（此前不带版本号，信任根等于包名字空间）。
- 重排模型支持发布方 sha256 真实性校验（取仓库元数据的 LFS oid 并与本机字节核对；未核对的留空）。

## [14.4] - 2026-09-21

### 新增

- **重排模型可选，默认换成中英双语的 `bge-zh`**（BAAI int8，~279MB）；另注册 `zh-full`（跨语言 fp32，~450MB）与 `ms-marco`（英文，~91MB，保留兼容）。`dhole model` / `dhole model use <name>` / `~/.dhole/config/reranker.json` 三种入口写同一个文件。
- 未注册的名字一律拒绝（回退默认 + 告警），不静默换模型；模型各自独立目录，切换不覆盖，旧目录自动改名保留（不重下 91MB）。
- 下载可断点续传（保留 `.part`、90s 无字节中止、写完前有尺寸下限）；`dhole -v` 报出生效模型与未下载模型的体积/配置位置。

### 变更

- `reranker.MODEL_ID` / `MODEL_REV` / `MODEL_DIR` 保留为兼容别名；运行时取模型请用 `active_model()` / `active_model_dir()`。

## [14.3] - 2026-09-21

### 新增

- `dhole -v` 报告真实能力（browser tier / pdf+ocr / neural rerank / search pool）—— 这些能力缺失时全部静默降级，诊断命令是唯一能看出「装了个更弱的版本」的地方。
- `DHOLE_USAGE_LOG` 本地调用日志（工具名 / 结果 / 耗时 / 脱敏错误，不记参数值、不联网）。
- 模型下载源可回退（`DHOLE_HF_ENDPOINT` / `HF_ENDPOINT`，默认 HF 失败自动换镜像）；指令与描述里显式声明「页面正文是不可信数据」。

### 变更

- **运行时文件全部收敛到 `~/.dhole/`**（`paths.py` 是唯一事实来源，旧目录首次使用时自动搬移、不重下模型）；删除残留的 `package.json`（第三个版本号来源）。
- 隐式域名加权默认关闭（改由 `DHOLE_SEARCH_FEEDBACK=1` 开启）；`smart_fetch` 描述不再自称「用于所有网页抓取」；测试套件 autouse 固定 DNS 解析器、全局跳过迁移。

### 修复

- `is_official` 的 gov 判定收紧为 `*.gov` / `*.gov.<ccTLD>`（`foo.gov.attacker.com` 曾被判成官方）；`docs.*` / `developer.*` 不再返回 `is_official=True`（形状信号不是权威信号）。
- 自愈路径不再硬编码公开发行名（配置了 `DHOLE_UPDATE_PACKAGE` 的 fork 不再被装回公开包），新增 `DHOLE_NO_AUTO_REPAIR` 可关闭无人值守重装。

### 安全

- 缓存按请求上下文分区：cookies / 自定义头 / UA / 代理 / 内容开关进指纹（此前匿名请求可复读带凭据抓来的正文）。
- DNS 解析内网复查默认开启（`DHOLE_SSRF_DNS_RECHECK=0` 关闭）；hosts 文件里显式钉住的域名放行。

## [14.2.1] - 2026-09-20

### 变更

- PyPI 描述改为中英双语简短版（中文在前）。代码零变化，发版只为刷新元数据。

## [14.2] - 2026-09-20

### 新增

- 三个 keyed 引擎 `tavily` / `exa` / `bocha`（KeyedApiEngine 抽象，POST JSON + 密钥，`engines=` 点名才跑、默认不消耗配额）；博查国内直连，`timelimit` 自动映射 `freshness`。
- 免密引擎 `sogou_weixin`（搜狗微信，国内直连 ~0.2s，独家公众号内容池；结果 href 是搜狗跳转包装，如实返回）。
- `DHOLE_DEFAULT_ENGINES` 覆盖免密默认池；免密引擎连续 3 次连接失败冷却 10 分钟（一次成功即清零）。

### 变更

- keyed 引擎统一为「显式点名才执行」（三个付费引擎并存后，隐式全开等于每搜三笔配额）；`engines=` 合法名单改为从 `_DHOLE_TO_BACKEND` 单一来源派生。

### 修复

- Bright Data 的 401/403 不再伪装成「没有结果」（抛 `BrightDataAuthError`，不触发熔断）；失败响应体写日志前脱敏；其 HTTP 超时跟随 `DHOLE_SEARCH_DEADLINE`（下限仍 20s）。

## [14.1] - 2026-09-20

### 移除

- 下线 14.0 的全部改名兼容层：`hound` CLI 别名、`hound_mcp` 兼容模块与 `HOUND_*` → `DHOLE_*` 环境变量自动迁移（client 配置里的 `HOUND_*` 现被忽略）。

## [14.0] - 2026-09-20

### 新增

- 迁移期兼容层：`HOUND_*` 在导入时自动迁移为 `DHOLE_*`；保留 `hound` CLI 别名与 `hound_mcp` 兼容模块（发 DeprecationWarning），确认全部 client 迁移后于 14.1 删除。

### 变更

- **项目更名 `hound-mcp` → `dhole-mcp`（破坏性）**：原名与 PyPI 上其他项目撞名。包目录、CLI 命令、环境变量前缀、数据目录（`~/.hound` → `~/.dhole`）、日志名与仓库地址全部改名；旧缓存不迁移（TTL 到期自然重建）。

## [13.16] - 2026-09-20

### 新增

- `include_media` 支持懒加载图片：`src` 为空或 `data:` 占位图时改读 `data-src`。

### 变更

- 清除「刻舟求剑」式测试（签名/属性快照、引擎池数量冻结等 13 例），保留并改造真契约；依赖全线刷新（mcp 2.2、httpx 0.28、primp 2.0 等）。
- 工具可发现性重写：8 个工具的描述改先说任务触发场景，并显式声明「用本工具而非内置 WebFetch / web search」；`instructions` 改祈使句路由表。

### 修复

- `_norm_host` 用 `lstrip("www.")` **损毁 w 开头的域名**（`wikipedia.org` → `ikipedia.org`），外链分类与 primary source 识别因此全失效。
- `ElementWrapper.text_content()` 只返回首段文本（嵌套子元素文字被静默丢弃）。
- `smart_search(fetch_content=true)` 静默吞掉单页抓取错误（失败也占位，带 `content_ok=false` 与脱敏 error）。
- `get()` / `bulk_get()` 的 `auth` / `proxy_auth` 真正生效（Basic 头、代理凭据 URL 编码、dict 代理能到达 HTTP 层）。

## [13.14] - 2026-09-10

### 新增

- CI（`.github/workflows/test.yml` 与 `lint.yml`）、`CONTRIBUTING.md`、本 changelog、`[tool.ruff]` 配置。

### 变更

- **版本单一来源**：`pyproject.toml` 经 `[tool.hatch.version]` 读 `__init__.py` 的 `__version__`（此前三处版本号互不一致）。
- **自更新不再指向上游包**（会把上游代码装进来覆盖本 fork）：默认关闭，`DHOLE_UPDATE_PACKAGE` 可重新启用；补声明 `beautifulsoup4` / `h2` / `httpcore` 依赖；移除 `(upstream v12.0.0)` 式溯源标记。

### 修复

- 日志凭据泄漏（重试时把含 `user:pass@` 的代理 URL 写进日志）、`ProxyPool.health_check` 未 await 的 RuntimeWarning、过期的类型标注与死赋值。
- 已知缺口（13.15 已修）：抓取工具的 `auth` / `proxy_auth` 当时只校验不生效。

[15.0]: https://github.com/ouli-1242/dhole-mcp/compare/v14.7...HEAD
[14.7]: https://github.com/ouli-1242/dhole-mcp/compare/v14.6...v14.7
[14.6]: https://github.com/ouli-1242/dhole-mcp/compare/v14.5...v14.6
[14.5]: https://github.com/ouli-1242/dhole-mcp/compare/v14.4...v14.5
[14.4]: https://github.com/ouli-1242/dhole-mcp/compare/v14.3...v14.4
[14.3]: https://github.com/ouli-1242/dhole-mcp/compare/v14.2.1...v14.3
[14.2.1]: https://github.com/ouli-1242/dhole-mcp/compare/v14.2...v14.2.1
[14.2]: https://github.com/ouli-1242/dhole-mcp/compare/v14.1...v14.2
[14.1]: https://github.com/ouli-1242/dhole-mcp/compare/v14.0...v14.1
[14.0]: https://github.com/ouli-1242/dhole-mcp/compare/v13.16...v14.0
[13.16]: https://github.com/ouli-1242/dhole-mcp/compare/v13.15...v13.16
