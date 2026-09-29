# 更新日志

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的格式：每个版本按 **新增 / 变更 / 弃用 / 移除 / 修复 / 安全** 的顺序分组，只列有内容的那些；日期用 ISO 8601；最新版本在最前。版本标题在结尾有对应 diff 链接（未打 tag 的版本不链接）。

**本文件只记使用者能感知的变化**：新增能力、行为变更、会踩到的缺陷修复、安全相关。实现细节、测试与重构见 `git log`。

> 自 13.14 起本仓库为个人衍生作品，版本号是自己的序号、不承诺语义化版本，与上游版本不可比；包内的 `__version__` 是唯一权威来源。

## [16.2] - 2026-09-29

agent 技能随包分发；修掉「代理环境下抓不了本机服务」这一个真实缺陷家族。

### 新增

- **`dhole skill` 子命令：skill 随包分发**。skill 正本（`SKILL.md` + `references/` 七个文件，教 agent 怎么选工具、防坑、排查与配置的手册）打进 wheel（`dhole_mcp/skills/dhole-web/`），`dhole skill install` 拷到 `~/.agents/skills/dhole-web/`（agent 宿主的用户级技能目录），`dhole skill status` 报告 bundled / installed 是否一致。目标已存在且内容不同时不覆盖（列出漂移文件），`--force` 重同步——升级 dhole 即升级 skill，不再需要为 skill 单独发版。`--help` 尾单与 README 的 CLI 表同步收录

### 修复

- **回环 / localhost 目标不再走环境代理（G31）**：primp（reqwest 底座）、httpx 与浏览器都会读 `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY`——系统代理或 fake-IP TUN 在场时，抓 `127.0.0.1` / `localhost` 的请求被交给代理，而代理**不可能**到达调用方自己的回环，一律回 502，本机 dev 服务全灭且报因误导（实测：设了死代理后，本机活着的 HTTP 服务从「连接失败」恢复 200）。修复是启动时把 `localhost,127.0.0.1,::1,[::1]` 合并进 `NO_PROXY`（已有条目两种拼写都保留、只补缺、幂等）
- **强制 `force_fetcher='stealthy'` 遇不可达目标快速失败（G31 后续）**：浏览器导航不把 connection-refused 浮出成错误，本机死端口实测烧满整个 timeout（45 秒）。`_force_fetch` 的 stealthy 分支补上与自动路径同一条 TCP 预检：`connection_refused` / `dns_failure` 跳层；回环 / 私网目标的 connect 超时也算定论（回环不存在「2 秒才应答」的合法服务，实测 DROP 型防火墙栈把 refused 变成 timeout）；公网目标的 timeout 仍交给真实尝试。实测死端口 45s → 2s，活的本机服务照常 200。显式代理照旧在函数入口探测（G21），环境代理在场时只探私网 / 回环目标（G31 之后它们不走环境代理，直连探测重新准确）。TCP 预检之外还有第二层：对「先应答再失败」的网络栈（透明代理 / 沙箱 broker，TCP 探不出死活），私网目标在启动浏览器前再做一次 HTTP 级健康探测（直连、3 秒、不重试）——网络层失败或 5xx 直接把探测结果作为答案返回，2xx/3xx/4xx 才进浏览器
- **`dhole -v` 横幅在 editable 安装下说谎**：版本号读的是安装那一刻的 dist-info，源码升版后横幅停在旧号（实测 16.2 的代码显示 16.1）。改为优先读 `dhole_mcp.__version__`（pyproject 与 CHANGELOG 头部声明的唯一权威），元数据只作兜底
- README 里 `date_filter.exact` 的含义写反了：它表示「**发出的档位 == 覆盖窗口的最窄一档**」，不是「没被放宽」——`after=2026-09-01`（28 天窗口）发 month 档时 `exact` 就是 `true`。人话解释以 `date_filter.note` 为准

## [16.1] - 2026-09-29

默认搜索池换掉国内第三席，顺带把连接期的工具描述再压一遍。

### 变更

- **默认池的国内第三席由 `so360` 换成 `sogou`**：默认池现在是 `baidu` / `bing` / `sogou` / `bing_global` / `yandex` / `brave`。两家是同生态位的国内独立索引（都按 IP 频次限流，默认池只点一家），家族账不变，仍是 6 引擎 / 5 家族（`x of 5`）。`so360` 照旧注册可用，`engines=["so360"]`（别名 `360`）或 `DHOLE_DEFAULT_ENGINES` 都能点回来
- **工具描述再精简，连接期 21,726 → 21,282 字符**（`tools/list` 19,585 → 19,141，约再省 100 token）：`close_session` 一条从 1,260 压到 724 字符，删掉的是 `options` 包里已经写过的会话科普
- **`cache_clear` 从此明说自己会连 `robots.txt` 结论与所有会话 cookie jar 一起忘掉**（该条描述 760 → 852 字符，两条合计仍为净省）：这件事它一直在做，原先只写在 `close_session` 的描述里，而拿着「清缓存」意图的人恰恰是需要知道会掉登录的那一个。只想忘掉单个会话仍然用 `close_session`

## [16.0] - 2026-09-28

能力面的一次整体扩张：robots 遵从、非 GET 请求、凭据与跨调用会话、内容再验证、增量 feed、整站与搜索的账；同时把每次调用的响应与连接期的工具表压小。

### 新增

- **`robots.txt` 遵从**（默认开启）：被 Disallow 的 URL 一个请求都不发（`error=robots_disallowed`）；单次豁免 `options.ignore_robots=true`，进程级 `DHOLE_IGNORE_ROBOTS=1`；`smart_crawl` 逐页遵从，已知被禁的链接入队前就丢掉
- **非 GET 请求**：`options.method` / `body` / `content_type`（POST / PUT / DELETE / PATCH / HEAD）。写请求不重试、不进缓存、不升浏览器、不用快照作答
- **凭据**：`options.auth` 支持 basic / bearer / API-key 三种形状，形状读不懂就在发出任何请求之前拒绝，值永不回显
- **跨调用会话**：`options.session_id` 的 cookie jar（按主机、24 小时），登录之后的页面抓得到；响应只报 cookie 名字（`session_cookie_names`），不报值
- **`close_session` 工具**：不带参数=会话名册（id、主机、cookie 名字、还剩多久过期、浏览器是否开着），`session_id=` 点名关一个，`all=true` 全关
- **内容再验证**：`if_none_match` / `if_modified_since`，每次答复回吐 `cache_validators`；304 按成功处理（`not_modified=true`、空正文、无 error）
- **内网取回**：`options.allow_private` 与 `DHOLE_ALLOW_PRIVATE_HOSTS`，按名字放行 dev / staging / Docker 主机；云元数据端点永不放行
- **截图落盘**：`options.save_to` 写文件并回报绝对路径与字节数，纯文本 agent 也用得上这张图
- **页面交互**：`scroll` 能触发懒加载（等 DOM 停止变长，上限 20 秒）；`wait_selector` 支持 `{selector,count,state,timeout_ms}`；每个动作在 `metadata.actions` 留一行收执
- **结构化提取**：`schema` 支持嵌套与重复记录，子选择器在容器内求值，一个容器一条记录
- **feed**：`since=<日期>` 增量轮询；`cache_validators` + 条件请求；可直接传站点首页（顺页面自己声明的 alternate 链接找 feed，`discovered_from` 记下这一步）；零条目时 `note` 给出归因
- **整站**：`options.sitemap=true` 一次拿到全站 URL 图；`options.delay` 控制同一主机的请求间隔（站方 `Crawl-delay` 会抬高它）；`crawl_urls` 被丢掉的条目如实报数（`urls_supplied` / `urls_deduped` / `urls_dropped_off_domain` / `urls_dropped_over_max_pages`）
- **搜索**：`after=<日期>` 与 `freshness` 走同一条路，`date_filter` 说明实际发到哪一档、哪几家根本不接受日期；`min_relevance` / `min_raw_relevance` 相关性下限，`fetch_hint` 报出这一轮的原始分跨度
- **链接**：`max_links`（每类 1–100）；`links.total_found` / `is_truncated` 报告被裁掉多少
- **代理预检**：显式代理不应答时返回 `error=proxy_unreachable`，5 秒内失败、不发请求、不回退快照
- **本地文件**：`parse` 新增 `.md` / `.markdown` / `.txt` / `.json` / `.yaml` / `.yml` / `.pptx` / `.odt`，不引入新依赖；JSON / YAML 会校验；文本工具带不动的部分写在输出首行
- **源类型**：`source_type` 新增 `reference`（百科 / 标准 / 规范）与 `paper`（期刊 / 预印本）
- **引擎池**：三档冷却时长可调（`DHOLE_ENGINE_COOLDOWN` / `DHOLE_ENGINE_CHALLENGE_COOLDOWN` / `DHOLE_ENGINE_CONN_COOLDOWN`）；`DHOLE_ENGINE_HEARTBEAT` 主动巡检仍在冷却中的引擎，答得上来当场解除
- **开销开关**：`DHOLE_STRUCTURED_CONTENT`（是否额外发一份结构化拷贝）、`DHOLE_WIRE_FULL`（把响应退回未压缩形状）、`DHOLE_OUTPUT_SCHEMA`（声明可机器校验的 `outputSchema`）

### 变更

- **响应只发偏离默认值的字段**：一次调用 −45%~−49%，`duration_ms` / `fetched_at` 砍到有效位；`instructions` 写明「缺席即默认」的读法；`DHOLE_WIRE_FULL=1` 整体退回旧形状
- **工具描述与 `instructions` 精简**：连接期 23,084 → 21,726 字符（cl100k 口径 ≈5.2k token）
- **500 / 502 不再升级隐身浏览器**，改走 archive 回退或如实报错；数据文档（XML / 图片 / PDF）一律不送浏览器渲染
- **`is_stale` 按源类型判定**：新闻 30 天、`reference` / `paper` / `docs` / 仓库 / Q&A 730 天、其余 365 天；持续编辑类站点没有修改时间时 `content_age_days=null`
- **非 PDF 的 `quality_score` 改为 `null`**
- **`page_type` 判据换掉**：列表页看非链接正文的占比与绝对长度，正文页第一次能判成 `article`；抓取失败的页不再被说成 JS 壳
- **`smart_crawl` 的 `discover_only=true`** 如实报告（`content_ok=false`、每页一次的代价可见、`next_action` 给出一次拿全图的两条路）；地图模式的每行只留说出区别的字段
- **`is_official`** 覆盖命名空间的运营方（iana / rfc-editor / w3 / unicode / pypi / npm / crates.io …）
- **`metadata.robots`** 按真实判定分档，且每次返回按当前进程重算；缓存里的历史许可并列写出
- **`smart_fetch` 除 `url` 外的每个参数两处都接受**（顶层与 `options` 袋，顶层优先）
- **`cache_clear`** 同时忘掉 robots 结论与 cookie jar；引擎健康表只在 `engine_state=true` 时回
- nature.com / science.org 由 `news` 归入 `paper`
- `schema` 参数的说明补上一句：只有 `selector` / `attribute` / `type` / `properties` / `items` 会被读取，别的 JSON-Schema 键一律不起作用——自己发明 `repeat` 然后把首条当成全部的那种误读从此有明确出处
- 服务器自己回的 JSON / XML 错误体不再被 archive 快照顶替；`css_selector` 对非 HTML 不再静默忽略（`summary` 明说没应用）

### 修复

- `feed_fetch` 的条目摘要被裁到 500 字符却不吭声：现在被裁的那条带 `summary_truncated=true`，工具描述也写明这个上限与拿全文的路
- 参数放错通道时的报错现在会说清它该去哪：`smart_crawl` 的 `discover_only` 放进 `options` 被拒时，会补一句「它是顶层参数，请挪出去」——此前唯一的读法是「这工具没有地图模式」
- `DHOLE_OUTPUT_SCHEMA=1` 打开后 `tools/list` 整张表消失（客户端看到 0 个工具）：`smart_fetch` 声明的 `outputSchema` 顶层只有 `anyOf`，缺线协议要求的 `type:"object"`，SDK 判定整个结果非法
- HTML 同一份字节被解码两次导致的乱码（`Saturn's` → `Saturnâs`）
- `parse` 把「文档引用了乱码样例」判成字符集猜错：按 UTF-8 一字不差的干净文件回 `encoding_undecodable`
- 缓存命中丢掉 `cache_validators` 与抽取器的 `content_ok`
- `feed_fetch` 的 `since` 从未到达过滤器；feed 里不带时区的日期被按本机时区解读；无日期条目被排在最新之前
- 文档 URL 的 304 不回 `not_modified`；`force_fetcher` 钉层时绕过死代理预检
- `smart_fetch` 的 wire schema 拒掉了描述承诺的 `options.urls`
- 带 `schema` 的调用全线失败（取回层参数错位）；`smart_crawl` 的 `ignore_robots` 从未通过；`--cache-ttl` 在 MCP 这条路上是死的
- 非英文的 JS 墙被判成正文；`actions` 的 click 不等它自己造成的导航，动作失败此前只写日志
- `Response.cookies` 恒为空（映射形状没处理）；表单实体不发 `Content-Type` 就收不到；`HEAD` 的 `total_size_bytes` 恒为 0
- SSRF 的拒绝被当成可重试错误（会把源站再敲一次）；跳数预算耗尽时 `url` 报的是从没向任何服务器要过的那一跳
- `<meta http-equiv=refresh>` 的跳转不跟随；无引号的 `REFRESH` 连「这是跳转页」都判不出
- `robots.txt` 的 5xx / 429 曾被按 1 小时缓存（策略写明 60 秒）；注入的抓取器返回文本时规则体被静默丢空（fail-open）
- `links.total_found` / `is_truncated` 之前无从得知截断；`smart_crawl` 的 `is_truncated` 恒为 false
- URL 地图按页收费（1000 条 sitemap 序列化成 353 KB 装 70 KB 的 URL）；错误页附带整份导航的引用图
- 搜索空结果的 `error` 与 `next_action` 互相拆台；引擎有产出但被自家相关性过滤全丢时零解释
- 本地解析把内容问题（坏 JSON、坏 YAML、假 PDF、未知扩展名）说成路径问题
- 预算耗尽时把内层已经查出的真凶丢掉，并把连接重置引导成「调大 timeout」
- PDF 正文混进页边旋转文本；`schema` 的「数组」漏写父 `selector` 时静默产出混合分组
- `wikipedia` / `grokipedia` 的产出计数从未写过，因而被误判成「引擎未接入」
- `401` 的建议分话：「你给的凭据被拒」与「这个 URL 要凭据而你没给」修法相反

### 安全

- **凭据不再跟跨源重定向走**：每跳重建请求头，跨源时摘掉 `Cookie` / `Authorization` / `Proxy-Authorization`，含调用方自己命名的 API-key 头
- **带凭据的正文按凭据分别进缓存**，不会被之后的匿名请求回放出去
- **会话凭据可清、且从不外流**：cookie 值不进响应（只有名字），`close_session` / `cache_clear` 立刻忘掉，不等 24 小时自然过期
- **SSRF 白名单点名生效**：`allow_private=true` 只放开回环，云元数据端点写进白名单也不放行；页面「自动发现」的 feed 地址同样过守卫

## [15.2] - 2026-09-25

默认搜索池重组 + 第七轮外部实测的处置。

### 新增

- `dhole engines probe`：逐个实测引擎**当下**能不能用，打印命中数、耗时、解析产出与被拦原因（`--all` 连 opt-in 一起测，`--query=` 换查询；无引擎答话时退出码非零）。`dhole engines list` 只记得上一轮结论，答不出「现在」。

### 修复

- **参数类型容错**：把 `max_results` 这类参数序列化成字符串的客户端，会让 `smart_search` 抛回一句裸 Python 异常，读起来像工具崩了。现在顶层参数与 `options` 两条通道都按字面转类型；取值范围仍归各工具自己管。
- 传 `crawl_urls="https://..."`（字符串而非列表）会被按字符拆成十条 URL 去抓，现在在任何抓取动作之前报错。
- `pages=3`、`password` 传数字时 PDF 页码范围静默失效，现在照字面接受。
- `smart_search` / `smart_crawl` 遇内部异常时不给 `next_action`，读起来像工具崩了，现在照给。
- `location` 的报错补上约束：只接受 ISO 国家码或 `us-en`，没有 `worldwide`。
- **Bing 被拦时反而重试三次**，熔断永不触发——bing 家族的「被拦」判定一直没生效。现在被拦原样上抛，网络抖动仍重试。
- 3xx（跳转未跟随）不再被说成「没有结果」，`error` 带出真实状态码。
- 整池都在冷却时不再抛裸异常。那会把一个二十秒后自愈的状态说成查询质量问题，还叫用户改写查询；现在照常返回状态。
- `dhole -v` 提示的 `dhole reset-engines` 是不存在的命令名（真名 `dhole engines reset`）。
- **大 PDF 必然超时**：同域页面的历史耗时压住了 `timeout`，40MB 的 PDF 只剩 5 秒，调大 `timeout` 也无效。现在页面与文档分别学习耗时，`timeout` 如实生效。
- **50MB 硬顶原先在下载完成后才检查**：200MB 的 PDF 下到预算耗尽才报「超时」，归因错且白烧带宽。现在文档请求先探体积，装不下的直接拒绝，正文请求根本不发。
- 文档只拿一次尝试并独占剩余预算（重试是从第 0 字节重下，切成四次只会每次都装不下）；页面相反，切开才让 `retries` 真正跑得起来。

### 变更

- **默认搜索池改为 `baidu` / `bing` / `so360` / `bing_global` / `yandex` / `brave`**（原 `baidu,bing,yandex,brave,duckduckgo,yahoo`）。ddg / yahoo 与 `bing` 同属一个索引家族，在池里只多入口不多家族，席位让给了国内第三个独立索引 `so360`；共识分母随之由 4 个家族变 5 个（`x of 5`）。ddg / yahoo 仍可点名。
- **HTTP 200 的校验页按连续次数递增冷却**：前两次 60 秒，第三次起 10 分钟；状态码拒绝（403 / 429 / 503）恒为 60 秒。

## [15.1] - 2026-09-24

第四至第六轮外部实测的处置。

### 新增

- `DHOLE_DEFAULT_CONTENT_CHARS`：未显式传 `max_content_chars` 时的默认正文预算（出厂 40000），可调。显式传值仍然优先，500–200000 的可用区间不变。
- `DHOLE_TOOLS`：只注册指定的工具（如 `smart_fetch,smart_search`），降低每次连接的 schema 成本。未知名在启动期报错并列出合法名。

### 变更

- `smart_crawl` 单次总预算硬顶 500,000 → 1,000,000 字符，仅影响显式抬 `max_total_chars` 的调用。
- 连接期开销再降 7.4%（13,605 → 12,601 字符）。
- `feed_fetch` 的 `content[0].text` 与 `structured_content` 统一为 `{"feeds": [...]}`。
- `schema` 的描述写明：不带 `type` 时返回首个匹配，`"type": "array"` 时返回全部。
- `focus` 的过滤强度随查询词在页面里的分布摆动（本轮未改）：`Focus:` 头的 `showing N of M blocks` 要当**子集**看，取全量用 `focus=''`。
- `smart_fetch(urls=[...])` 只限 URL 个数（100），对正文总量没有上限（本轮未改），止损靠调用方自己传 `max_content_chars`。

### 修复

- `smart_crawl` 的 `path_include` / `path_exclude` 把字符串前缀当路径子树匹配：`"docs"` 与 `"/docs/*"` 会把整站静默过滤成 0 条，`"/docs"` 又会多留 `/docs-old`。现在按路径段边界匹配子树（`'docs'` / `'/docs'` / `'/docs/'` / `'/docs/*'` 等价），其余通配写法直接报错，且校验在任何网络动作之前。
- `parse` 读 GBK 文件返回乱码却报 `content_ok=true`。现在 BOM → `encoding` 参数 → 严格 UTF-8 → 严格 GB18030 依次尝试，`metadata.encoding` 回报实际生效的字符集；仍有损伤时正文照常返回，但报 `encoding_undecodable`、`content_ok=false`。Big5 可检出，Shift_JIS / EUC-KR 需显式传 `encoding=`。
- `max_content_chars` 静默忽略调用方的值：`"2000"` 这类数字字符串会拿到 40000（20 倍超额，以普通 200 交付）。现在能转换的转换、不能转换的报错；`offset` 负数报错。
- `screenshot` 把浏览器原始日志原样返回给 agent 且截断在半句。现在只保留异常类型与首行，并给出可行动提示（`options.timeout` / `options.wait_selector` / `close_session` / `playwright install chromium`）。
- `content_ok=true` 但正文是失败页（x.com 的 `Try reloading`、douyin.com 的 `Please wait...`）。新增软失败检测：命中明确失败措辞且正文短于 600 字符时置 `content_ok=false`、报 `soft_failure_detected`。
- 错误状态（404 / 403 / 429）的 `next_action` 被截断提示抢走，agent 被引导去翻不存在的下一页。截断提示现在只对 `status < 400` 生效。
- Content-Type 声明的 charset 与字节不符时产出乱码。现在两种解码各打一次分取更干净的，`<meta charset>` 参与判断。
- `smart_fetch(urls="https://example.com")` 按字符迭代，返回 19 条垃圾结果且没有任何错误信号。现在报错并指向 `url=`；JSON 数组字面量仍接受。
- `cache_clear(all="false")` 会清空全部缓存（任何非空字符串都是真值）。布尔参数现在按白名单解析（`true/false/yes/no/on/off/1/0`，大小写不敏感），其余报错。
- `force_fetcher` 传非法值会静默落进最重的 stealthy 层（`smart_crawl` 的 `options.force_fetcher` 同）；它也不在缓存键里，跨层命中会拿到错误层写入的正文。前者改为取值校验，后者进缓存指纹。
- 中文 `focus` 完全 no-op（分词只匹配 ASCII）。现在按 Unicode 分词，无空格文字按字符二元组展开。
- `max_results` 的钳制注记在缓存命中时丢失；`max_content_chars` 越界静默钳到边界。两者现在都在 `summary` 里说明。
- `urls=["https://...", 123]` 泄漏裸 Pydantic 错误文本，现在报 `urls[1] must be a string, got int`。
- `cache_clear` 默认路径每次都返回完整的 `engine_health`（~1KB），现在只在 `engine_state=true` 时取。
- `feed_fetch` 抛异常，与其余 7 个工具的错误信封不一致且丢掉 `next_action`，现在统一为 `{"feeds": [], "error": ..., "next_action": ...}`。
- `smart_fetch` 重定向后 url 被静默改写，现在新增 `original_url`，仅在确实与传入值不同时给出。
- `focus=` 对问题式查询几乎不筛，而问题式正是描述推荐的用法（同一页关键词留 9%、问题式留 76%）。现在割线改为 `max(绝对阈值, 最佳内容块分 × 1/3)`，问题式正文 40,099 → 11,562 字符。

## [15.0] - 2026-09-22

搜索引擎重编组（默认池 6 + opt-in 8）+ 第三轮外部复测的处置。

### 新增

- 搜索后端：`baidu`、`baidu_baike`、`so360`（别名 `360`）、`sogou`、`bing_global`、`mwmbl`、`sogou_weixin`。
- `parse` 新增 `cwd` 参数：相对路径的解析基准，优先于 `DHOLE_WORKDIR`、服务器进程 cwd 与家目录。

### 变更

- 默认池改为 `baidu,bing,yandex,brave,duckduckgo,yahoo`（6 引擎 / 4 家族）。
- 429 计入「被拦」（RFC 6585），此前记成 empty。
- `sogou_weixin` 与 `sogou` 合并为同一索引家族，不虚报共识。

### 移除

- `sogou_weixin` 移出默认池（垂直索引会稀释通用搜索），保留注册，`engines=["sogou_weixin"]` 仍可显式搜公众号。

### 修复

- 顶层参数被静默丢弃（顶层 `max_pages` 被忽略、`path_include` 不过滤、`cache_ttl=0` 照样命中缓存）。现在按工具维护白名单、顶层优先，不认识的键报错并列出支持集，`null` 视为未设置。
- `parse` 的描述漏列 `.htm` / `.xhtml`（实际一直支持），现在补齐。
- `parse` 拒绝纯文本时只报「不支持」，现在每条拒绝路径都带一句指路。
- 失败时把占位文本塞进正文（图片页与「`.pdf` 却回 HTML」两条分支）。现在清空正文、错误只留在 `error`，并阻止这类页面白烧 30~40 秒的浏览器升级。

## [14.7] - 2026-09-22

第二轮外部测试报告的处置。

### 修复

- `options.cookies` 传 Cookie 字符串被逐字符迭代丢弃，现在列表 / 字典 / `Cookie` 头三种形态都收。
- `options.useragent` 在 HTTP 层完全不生效，现在生效且显式值优先；`extra_headers` 头名大小写重复的问题一并修掉。
- schema 标量改取「第一个非空」匹配（首个匹配是图片链接时曾返回空串）；`attribute` 从未实现，现已按属性取值。
- `path_include` / `path_exclude` 传字符串会静默清空整站；`options.search` 被拒（顶层一直可用）；过滤后 `summary` 仍报过滤前的统计。三者都已修。
- 本地 PDF：`parse` 丢掉目录与 metadata，现在带上。
- actions 档默认预算 30s → 60s（actions 必走浏览器层）。
- `schema` 与 `max_content_chars` 同用时，选择器跑在被截断的 HTML 上；空 body 的 4xx 被误判成 JS 空壳（改为如实报 `http_error_4xx`）。

## [14.6] - 2026-09-22

主题：**调用看起来成功了，但实际没做它承诺的事**。

### 新增

- `dhole proxy` 子命令（此前模块文档承诺、命令并不存在）：add / list / remove / clear，失败一律出声，凭据永不上终端。
- `dhole --doctor`：回答「装得对不对」——启动器、模块实际加载路径、元数据一致性、残留进程、状态目录可写性、代理池；每项失败给出可复制的修复命令，退出码 1。
- 引擎健康可重置：`cache_clear(engine_state=true)` 立即忘掉冷却与产出记录，`dhole engines list|reset` 是同一件事的 CLI 入口；`circuit_open` 报告带重试倒计时。
- `DHOLE_NO_BROWSER_PREWARM`：设 `1` 后启动不预热隐身浏览器（离线机器 / 计费网络不必接受这笔账）。

### 变更

- 工具描述与 instructions 收敛：补缺失的路由规则、修自相矛盾、删重复与营销话术，连接期开销随之下降。

### 修复

- 响应契约：失败时不再把错误 / 占位文本塞进正文（改 `content:[]` + `error`）；入参校验失败改为结构化结果（`status:0`），不再 raise 成 MCP 错误。
- `smart_fetch` 的 `timeout` 变成真预算：逐层按剩余预算收敛，耗尽返回结构化 `timeout:` 结果（不再是客户端 -32001）；浏览器会话等待也纳入预算。
- `css_selector` 在 markdown 模式下静默失效，现在四种模式一致；`parse` 相对路径改为 cwd → `DHOLE_WORKDIR` → 家目录，并补齐信封。
- 反爬墙检测：新增 HTTP 200 上的验证码 / 登录墙识别，`page_type` 补 `auth_wall` / `captcha`；墙的 `next_action` 此前写在永远不会触发的分支里。
- 引擎有产出但被自家相关性过滤全丢时不再零解释；`site=` 在改写轮不再被放弃；`related_queries` 按域名去重、丢掉虚词碎片。
- 其它 agent 信号：article 的 author / date 从 OG / JSON-LD 回填；`extracted_type` 如实回填；`max_results` 越界给出说明；`content_age_days` 用 `null` 表示未知；列表页 `next_action` 不再指向导航栏；`options` 字符串形态统一解析。
- 代理探活只在确有冷却或死代理时才外发；410 现在真的走 archive 回退。
- **首次调用超时（-32001）**：首次缓存访问会同步搬移旧目录、阻塞事件循环（搬 120MB 模型实测停摆 2.09 秒）。现在搬到后台，并提前到启动时做。
- `parse` 读本地 PDF 无路可走：`file://` 在 URL scheme 黑名单里，旧提示的三条路全不通。现在 `parse` 自己解析（与 URL 路径同一个提取器，不放宽任何安全边界）。

## [14.5] - 2026-09-21

主题：让静默降级可被观测、把说得太满的地方改准。

### 新增

- 每引擎解析产出统计（`dhole -v` 新增 `engine yield` 行），能区分「被墙」与「答了但解析不出来」。
- `DHOLE_HOME`：状态目录可换位置（POSIX 下目录 0700 / 文件 0600）。

### 变更

- 默认池 5 → 6 引擎、3 → 4 家族（`sogou_weixin` 进池），随之处理：垂直索引在无重排器时排在通用结果之后、不能独自填满早退配额、拿原始 query、结果 href 如实返回跳转包装。
- 修正 `--cache-ttl` 死参数、意图展开集合里的不存在引擎名，以及 README 两处过度承诺。

### 修复

- `engines_consensus` 不再在降级池上伪装「全员一致」：分母改为由配置池推导的家族全集，新增 `consensus_basis`；降级标注也不再在缓存命中时丢失。
- `empty` 不再隐形：新增 `engine_empty` / `engine_preempted`；多样性警告的分母算上 empty。
- 零结果时的自动改写按原因处置：解析器漂移不再重打一轮全量 fan-out，也不再把自己的故障说成「查询该换个说法」。
- 口令 PDF 不再被误报成「文件打不开」：现在按异常类型判定，「没给口令」与「口令被拒」分开说明。
- Bing 某一版版面让整轮 bing 返回空（链接挂在标题元素上），解析不再绑定样式类名。
- `DHOLE_HOME` 此前只对一部分状态文件生效，现在全部生效。

### 安全

- 浏览器层补齐 SSRF 守卫：请求前拦截页面 JS 的 fetch / XHR、iframe 与 JS 跳转，落地后再判一次，内网正文一字不回流、不重试（残余：HTTP 3xx 重定向的目标仍会「盲打」一次）。
- 缓存指纹补 PDF 口令维度（带口令解出的正文曾与匿名行撞键、可被匿名请求复读），旧库做一次性定向清理。
- hosts 豁免按「钉到哪个值」判定：`0.0.0.0` 这类屏蔽用黑洞不再自动放行。
- 自愈重装钉在当前已装版本；重排模型支持发布方 sha256 真实性校验。

## [14.4] - 2026-09-21

### 新增

- **重排模型可选，默认换成中英双语的 `bge-zh`**（~279MB）；另注册 `zh-full`（跨语言，~450MB）与 `ms-marco`（英文，~91MB）。`dhole model` / `dhole model use <name>` / `~/.dhole/config/reranker.json` 三种入口写同一个文件。
- 未注册的名字一律拒绝（回退默认 + 告警），不静默换模型；模型各自独立目录，切换不覆盖。
- 下载可断点续传；`dhole -v` 报出生效模型与未下载模型的体积 / 配置位置。

## [14.3] - 2026-09-21

### 新增

- `dhole -v` 报告真实能力（浏览器层 / PDF+OCR / 神经重排 / 搜索池）——这些能力缺失时会全部静默降级，这是唯一能看出「装了个更弱的版本」的地方。
- `DHOLE_USAGE_LOG` 本地调用日志（工具名 / 结果 / 耗时 / 脱敏错误，不记参数值、不联网）。
- 模型下载源可回退（`DHOLE_HF_ENDPOINT` / `HF_ENDPOINT`）；指令与描述里显式声明「页面正文是不可信数据」。

### 变更

- **运行时文件全部收敛到 `~/.dhole/`**（旧目录首次使用时自动搬移、不重下模型）。
- 隐式域名加权默认关闭（改由 `DHOLE_SEARCH_FEEDBACK=1` 开启）；`smart_fetch` 描述不再自称「用于所有网页抓取」。

### 修复

- `is_official` 的 gov 判定收紧为 `*.gov` / `*.gov.<ccTLD>`（`foo.gov.attacker.com` 曾被判成官方）；`docs.*` / `developer.*` 不再返回 `is_official=True`。
- 自愈路径不再硬编码公开发行名（配置了 `DHOLE_UPDATE_PACKAGE` 的 fork 不再被装回公开包）；新增 `DHOLE_NO_AUTO_REPAIR` 可关闭无人值守重装。

### 安全

- 缓存按请求上下文分区：cookies / 自定义头 / UA / 代理 / 内容开关进指纹（此前匿名请求可复读带凭据抓来的正文）。
- DNS 解析内网复查默认开启（`DHOLE_SSRF_DNS_RECHECK=0` 关闭）；hosts 文件里显式钉住的域名放行。

## [14.2.1] - 2026-09-20

### 变更

- PyPI 描述改为中英双语简短版（中文在前）。代码零变化，发版只为刷新元数据。

## [14.2] - 2026-09-20

### 新增

- 三个 keyed 引擎 `tavily` / `exa` / `bocha`（POST JSON + 密钥，`engines=` 点名才跑、默认不消耗配额）；博查国内直连，`timelimit` 自动映射 `freshness`。
- 免密引擎 `sogou_weixin`（搜狗微信，国内直连 ~0.2s，独家公众号内容池）。
- `DHOLE_DEFAULT_ENGINES` 覆盖免密默认池；免密引擎连续 3 次连接失败冷却 10 分钟（一次成功即清零）。

### 变更

- keyed 引擎统一为「显式点名才执行」（三个付费引擎并存后，隐式全开等于每搜三笔配额）。

### 修复

- Bright Data 的 401 / 403 不再伪装成「没有结果」，会如实报 AuthError 且不触发熔断；失败响应体写日志前脱敏；其超时跟随 `DHOLE_SEARCH_DEADLINE`。

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

- 依赖全线刷新（mcp 2.2、httpx 0.28、primp 2.0 等）。
- 工具可发现性重写：8 个工具的描述改先说任务触发场景，并显式声明「用本工具而非内置 WebFetch / web search」；`instructions` 改祈使句路由表。

### 修复

- **外链分类对 `w` 开头的域名全失效**（`wikipedia.org` 被当成 `ikipedia.org`），primary source 识别因此一起失效。
- 提取正文时嵌套子元素的文字被静默丢弃，只返回首段。
- `smart_search(fetch_content=true)` 静默吞掉单页抓取错误，现在失败也占位并带上 `content_ok=false` 与脱敏后的错误。
- 抓取工具的 `auth` / `proxy_auth` 真正生效（Basic 头、代理凭据 URL 编码、dict 代理能到达 HTTP 层）。

## [13.14] - 2026-09-10

### 变更

- **`parse` 不再拒绝纯文本**（G30 带来的立场变化，写下来是因为有人会依赖它）。过去的报错是「.txt/.md 不需要转换，用你自己的文件工具读」——那句话预设调用方**有**文件工具，而只会说 MCP 的客户端只有一个入口。现在这些扩展名直接给内容，未列出的扩展名（.log/.rst/.ini）在报错里被告知改名成 .txt 再解析。
- **`smart_crawl` 与 `smart_fetch` 对同一页给同一份正文**（G20）。报告记录的分叉（同一 URL，一边是干净 markdown、一边是带大量空白的原始 HTML）在当前实现里已经不复现：crawl 的正文本来就调同一个抽取入口。这一项没有改代码，改的是**把等式钉住**——两条入口（HTML 字符串 / Response 对象）的输出逐字节相同由测试守着，联网再证一次工具层等价；唯一的有意分叉（列表页给结构化链接清单）也留了名字，免得下次「统一」把它一起统一掉。
- **版本号单一来源**：此前三处版本号互不一致，现在统一读包内的 `__version__`。
- **自更新不再指向上游包**（会把上游代码装进来覆盖本 fork）：默认关闭，`DHOLE_UPDATE_PACKAGE` 可重新启用；补声明 `beautifulsoup4` / `h2` / `httpcore` 依赖；移除 `(upstream v12.0.0)` 式溯源标记。

### 修复

- **日志凭据泄漏**：重试时会把含 `user:pass@` 的代理 URL 写进日志。

[16.2]: https://github.com/ouli-1242/dhole-mcp/compare/v16.1...v16.2

[16.1]: https://github.com/ouli-1242/dhole-mcp/compare/v16.0...v16.1

[16.0]: https://github.com/ouli-1242/dhole-mcp/compare/v15.2...v16.0

[15.2]: https://github.com/ouli-1242/dhole-mcp/compare/v15.1...v15.2

[15.1]: https://github.com/ouli-1242/dhole-mcp/compare/v15.0...v15.1

[15.0]: https://github.com/ouli-1242/dhole-mcp/compare/v14.7...v15.0

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
