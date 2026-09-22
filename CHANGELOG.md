# 更新日志

本文件在 11.1.8 版重新建立。更早的发布**未**在此回溯补记——历史请查看
`git log` 与 GitHub releases 页面。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

> **版本说明。** 自 13.14 起本仓库为个人衍生作品，不再跟随上游项目的发布，
> 版本号是自己的，与上游版本不可比。`src/dhole_mcp/__init__.py` 中的
> `__version__` 是版本的唯一权威来源。

## [14.7] - 2026-09-22

一份外部全量测试报告（两轮：无 VPN / 有 VPN，8 个工具，7 个显著 + 1 个间歇 + 8 个轻微）逐条复验后的修复批次。复验结果：报告的显著/轻微项里 **BUG-1/2/4/5/6/8/9/10/12/13/14/15/16 确认并已修**，**BUG-11（冷启动慢）由超时预算收敛**，**BUG-19 未复现但把产生它的代码路径堵了**，**BUG-17 的根因判断被证据推翻**（改的是它真实剩下的三个缺口）；另外测出 **4 项报告里没有的问题（NEW-1…NEW-4）**并一并修掉。这一批的共同主题是**失败要说自己是失败**：正文里不许夹错误文本，被 dhole 自己丢掉的结果要说出来，超时要在预算内给出结构化答复。

### 修复（响应契约）

- **失败时正文里塞错误/占位文本（BUG-5）。** 实测 DNS 失败返回 `content:["[No more content.]"]`、TLS 失败返回 `content:["[Fetch error: …tls handshake eof]"]` 且 `total_extracted_chars:87` —— 调用方读 `content[0]` 就把异常文本当正文，还把它的长度当成了页面体量。`_apply_chunking` 现在只在**页面确实有过正文**时才给 `[No more content.]`（翻页读完的场景保留原文案），两层 fetch 的异常分支与"全部层都失败"的提示块一律 `content:[]`、文本进 `error`（该字段的定义就是"错误 + 恢复提示"）。
- **入参校验破坏统一响应形状（BUG-16）。** `url=""` 与 `url="example.com"` 此前 `raise`，被兜底成 `is_error` 的 MCP 结果，而 `resolve_url` 同类输入返回的是结构化 `{error, status:0}`。新增 `_invalid_request_result()`：空 url / 非 http scheme / 坏 schema / bulk 模式带 actions 全部返回正常 FetchResult（`status:0`、`content:[]`、`content_ok:false`、`next_action` 给出改法）。`tests/test_schema_param.py` 里"空壳 schema 必须抛"的旧断言随之改为"必须结构化拒绝且不发请求"——可见性没有降级，只是换了信道。
- **`smart_fetch` 的 `timeout` 只是每层建议，不是整体预算（NEW-4，报告里表现为 -32001）。** 根因：`_adaptive_timeout` 学到 [5s,60s] 后**无视调用方的值**，HTTP 层再乘上 `retries=3` 与每个重定向跳转的独立超时，浏览器层拿到的是"已经超支之后"的 `timeout - elapsed` 且**下限 5s**。现在 `_auto_escalate` 按 deadline 逐层收敛（HTTP 超时不超过剩余预算、慢域名加成同样受限、预算不足 1.5s 时明确跳过浏览器层并说明），每层再被 `_with_budget()` 硬包一层；`smart_fetch` 的四个出口统一走 `_within_call_budget()` 兜底。预算耗尽返回结构化 `timeout:` 结果（说清耗尽于哪一层、`Raise timeout / force_fetcher='http' / 换源`），不再是客户端的 -32001。
- **`css_selector` 在默认 markdown 模式下静默失效（BUG-8）。** 实测 `css_selector:"h1"` 返回整页 183 字符、`"blockquote.abstract"` 返回 4051 字符，只有 `extraction_type:"html"` 生效。根因不在选择器逻辑：收窄那段写的是 `page.css(...)` 而 dhole 自己的 Response 没有 `.css()`，`hasattr` 取到 False 后**悄悄退回整页**。改为 `_narrow_html_to_selector()` 用 lxml + CSSSelector 直接吃原始 HTML，markdown/text/article/html 四种模式一致；选择器没命中仍然退回整页（既有行为，日志里保留说明）。
- **`parse` 把相对路径解析到 MCP 宿主进程目录（BUG-10）。** 实测错误指向 `D:\Program Files\Qoder\dhole_fixture.html`——既不是用户工作目录也不是家目录，而是宿主 cwd。现在依次尝试 cwd → `DHOLE_WORKDIR`（新增）→ 家目录，未命中时错误里**列出全部试过的路径**。顺带修掉 `parse` 的信封空洞（BUG-12）：成功时 `total_extracted_chars:0` 而正文有值、`summary`/`fetched_at`/`content_type`/`duration_ms` 全空、url 拼成 `file://D:\…`——现在统一过 `_apply_chunking`，正文按 smart_fetch 同样的规则截断（此前 50MB 文件会整份进上下文）。

### 修复（agent 信号）

- **反爬墙在 HTTP 200 上完全没有检测器（BUG-6 真因）。** 报告的结论是"检测只在 smart_fetch 生效、search 的 fetch_content 路径漏判"——不成立：fetch_content 就是调 `self.smart_fetch` 并原样搬运 `content_ok`（`server.py:3409`），实测**直接 smart_fetch 同一个搜狗验证码页也是 `content_ok:true`**。真因是 `_is_cloudflare_from_response` 只在 403/503 生效、`_AUTH_WALL_CONTENT_SIGNALS` 只有登录词。新增 `_is_bot_wall()`（`/antispider` 之类路径信号 + 中英验证码/`VerifyCode`/`unusual traffic` 等内容信号，并用 1500 字符长度门避开"正文就在讲验证码"的页面）；墙的检测排在 JS 壳启发式**之前**，否则"大 body 小正文"那一条会先把登录页判成需要渲染。
- **`page_type` 与实际检测自相矛盾（BUG-12 后半）。** `error:"auth_wall_detected…"` 时 `page_type` 仍是 `unknown`，因为 `page_type_from_error()` 只映射了 PDF 路径的 `auth_required`。现在 `auth_wall_detected → auth_wall`，并新增 `captcha` 取值（`bot_wall_detected` / `bot_challenge_detected → captcha`），字段描述与工具描述同步。`next_action` 补了登录墙/验证码墙两条——原先它们写在 `content_ok` 为真的分支里，而墙必然让 `content_ok` 为假，**永远不会触发**。
- **引擎有产出、被 dhole 自己的相关性过滤全丢时零解释（NEW-1，这批里最严重）。** 实测 `"how does dns resolution work"`：`engine_stats.json` 记录 bing 该轮 `usable:10 / http:200 / ok`，而调用方拿到 `total_results:0`、`error:""`、`engines_used:["bing"]`、`engine_empty:[]`，`next_action` 还让人"换个说法"——真因是网络层把无关页面当回答送回（bilibili 首页），改查询完全无用。现在全丢时 `error` 明说"引擎给了 N 条、被判为离题全部丢弃 + 通常是门户劫持/代理"，部分丢弃时在 `fetch_hint` 留痕。
- **`site=` 在自动改写那一轮被静默放弃（NEW-2）。** 改写调用写死 `site=None`，实测 `site=theverge.com` 返回的全是 trustpilot/g2。改写轮现在保留站点约束（上游 `site:` 前缀 + 终域过滤都在），NOTE 里标明"仍限制在 site=…"；站点内确实无结果时给专门文案，而不是通用"没结果，换个说法"。
- **article 提取的 author/date 恒为空（BUG-15）。** 同一份响应里 `metadata.author:"Emma Roth"`、`published_time:"2026-09-21T12:41:42+00:00"`，正文 JSON 里却是 `author:""`、`date:""`。新增 `_backfill_article_json()`：只填 `article`/`structured` JSON 中确实为空的 `author`/`date`/`description`，值取自已经解析好的 OpenGraph/JSON-LD，绝不覆盖 trafilatura 自己找到的。
- **`extracted_type` 与实际请求不符（BUG-4/14）。** `_translate_response` 从不设置该字段，于是所有响应都写着默认的 `markdown`（article 模式也是），同 URL 同参数还会来回摆。现在如实回填请求的格式，且 article/structured **只在正文真的是 JSON 时**才标该类型——提取器退回散文时标 `markdown`，不做反方向的撒谎。
- **`related_queries` 给的是摘要碎片（BUG-1）。** 实测 `["benedetto profile","before joining","covering laptops","deals writer","gadget spent"]`、`["stay"]`。两处改动：文档频次改为**按域名去重**（同一站点重复五次的作者签名不再"构成一个话题"，跨源佐证才是真正的信号），并丢掉以虚词/动名词开头的碎片；证据不足就少给或返回空，不再用一元词填空。
- **`install dhole-mcp[all] and retry` 泄露给 agent（BUG-9）。** 实测同一条 `fetch_hint` 前半句是"依赖已装但未启用"，后半句却让人去装依赖。新增 `_rerank_absent_reason()`：没有记录到原因时如实说"本进程尚未加载重排器，`dhole -v` 可查"，不再发明安装指令。
- **`max_results` 越界被静默钳制（BUG-13）。** `max_results=100` 现在在 `fetch_hint` 里说明"超出 1–50 支持范围，按 50 返回"，不会读成"只有 50 条结果"。
- **`content_age_days` 用 -1 表示未知（BUG-2）。** 负数与"未来一天的内容"混淆，而后者是另一种断言。未知/日期在未来（脏数据）现在都返回 `null`，字段描述同步说明"null 不是负龄"。
- **list 页的下一步指向导航栏（NEW-3）。** 实测 theverge.com/news 的 `next_action` 是 `Top targets: /, /auth/login, /subscribe`——按 DOM 顺序取前三条引用，取到的是站点头。新增 `_best_list_targets()`：排除 chrome/登录/订阅/源站首页/静态资源，偏好深层路径与带锚文本的链接，同域加分。
- **`options` 被逐字符解析（BUG-19，未稳定复现）。** 报告的重启后首轮 `['{','"','m',…]` 在 14.7 上没能复现（字符串形态的 options 现在能正确解析），但产生它的代码路径确实存在：`set(options)` 对 str 就是字符集合。`_dispatch` 现在统一走 `_coerce_options()`（字符串按 JSON 解析、非对象如实报错），`_strict_options` 也加了同形的护栏——不再依赖"客户端总是发对象"。

### 新增（引擎健康状态可见 + 可重置）

报告的 BUG-17 把"冷却状态持久化且无自动恢复"当成最严重的问题。**这条被推翻**：`_load_circuit_state()` 读盘时就过滤过期项、`_is_circuit_open()` 实时比时钟、`_record_success()` 立即清除，冷却本来就自动到期；`engine_stats.json` 只被 `engine_health()` 和诊断读取，**没有任何代码用它 gate 引擎**，`engine_preempted` 是当轮早退配额取消而非持久状态。对照组里"清文件才恢复"是把时间先后当成了因果（实测清空状态前的默认池搜索里 brave 就已经在 `engines_used` 中了）。

真正剩下的三个缺口按建议修掉：

- **过期记录不再留在磁盘上误导人。** 加载时若丢弃了过期项就回写文件；每轮搜索再 `sweep_expired_cooldowns()` 清一次，让 `circuit_breaker.json` 与池子的真实行为说的是同一件事。
- **`cache_clear(engine_state=true)`** 立即忘掉冷却与产出记录（内存 + 两个文件），响应新增 `engine_state_reset` 与 `engine_health`（每个引擎的判定 + `cooldown_seconds_left`）——不需要再"手动删文件 + 重启进程"，也不用换会话。
- **`dhole engines list|reset`** 是同一件事的命令行入口（纯 stdlib 读状态文件，半坏安装上也能跑）；`dhole -v` 的能力面板新增 `engine cooldowns` 行，显示还剩多少秒。
- **`circuit_open` 的报告现在带上重试倒计时**（`…skipped; retried in 90s`），把"永久被墙"和"90 秒后重试"分开；`preempted` 的文案明说"不是拦截、不是失败、不涉及网络"——第一轮测试者正是把它读成"dhole 没走 VPN"。

### 测试

`tests/test_bug_report_regressions.py`（68 例）按报告的编号逐条钉住复现证据与修复后的契约。全量：`1128 passed, 2 skipped`，`ruff` 干净。`tests/test_tool_descriptions.py` 的 `cache_clear` 体积预算 550 → 860（该工具新增了 `engine_state` 与使用时机说明），`tools/list` 实测字符数 11809（预算 12500）；README 的 token 表尚未按 14.7 重新测（需要 `tiktoken`，数字会小幅上移）。

## [14.6] - 2026-09-22

三批改动。前两批的共同毛病是**调用看起来成功了，但实际没做它承诺的事**。

**修复**：schema 静默降级、首调 -32001 超时、描述与代码不一致、进程扫描的编码问题。
**新增**：补齐三项用户能感知的能力 —— 代理池管理入口、安装体检、上手文档。
**变更**：收敛工具描述，让 agent 在该用的时候选对工具，同时降低 connect-time 成本。

### 修复（schema 参数静默失效）

- **`smart_fetch` 传 `schema` 仍返回 markdown（结构化提取不可用）。** 门条件写的是
  `if schema and isinstance(schema, dict) and (schema.get("properties") or ...)`，于是
  两个形态都会**静默退回 markdown 并返回 200**：① `schema` 以 JSON **字符串**送达
  （部分 MCP 客户端会序列化嵌套对象，agent 也会）被 `isinstance(..., dict)` 挡掉；
  ② `schema` 是 dict 但没有非空 `properties`（如 `{"type": "object"}`）。调用方完全
  无从知道 schema 被丢掉了 —— 这正是 `_strict_options` 存在的意义要防的那类失败。
  现在新增 `_normalize_schema()`：字符串形式被解析成 dict；**空壳 schema 直接报错**
  并给出可用示例，不再静默降级。校验前移到 `smart_fetch` 入口（single 与 bulk 两条
  路径共用），并给 `options.schema` 补上与其他提升参数一致的兜底（顶层优先）。
  补 `tests/test_schema_param.py`（15 例，含「字符串 schema 不得被丢弃」「空壳 schema
  必须在发起任何抓取前报错」两条回归）。

### 修复（首次调用 -32001 超时）

- **`smart_fetch` 偶发 MCP timeout（-32001），首次报错、重试即恢复。** 根因是**一次性
  的旧状态目录搬移同步跑在事件循环上**：`cache._ensure_db()` 在首次缓存访问时调用
  `paths.migrate_legacy_cache_dir()`，而它会搬 `cache.db` 加整个 `models/`（90–450MB；
  跨卷时 `shutil.move` 退化为复制+删除）。实测搬 120MB 模型就把事件循环**完全停摆
  2.09s**（心跳间隔本该 0.05s），期间 MCP 服务发不出任何响应，首个工具调用被判超时；
  重试时一次性标志已置位，于是瞬间成功。修复：新增
  `paths.migrate_legacy_cache_dir_async()`（走 `asyncio.to_thread`），`cache._ensure_db`
  与 `reranker.ensure_reranker`（`model_present()`）改用它；并把该搬移提到启动预热
  （`_prewarm_state_dir`）里跑，让成本落在启动窗口而不是首个工具调用上。实测同一场景
  事件循环停滞 **2.09s → 0.06s**。
- **同一搬移的并发语义顺带修正。** 原先用「先置位再干活」的普通 bool，第二个调用者会
  立刻返回、然后去读一个**正在被搬移**的 `cache.db`。改为 `threading.Lock` 串行化：
  第二个调用者等待第一个搬完再返回。
- **浏览器会话获取纳入调用预算。** `_auto_escalate` 里 `_ensure_auto_session()` 会排队
  等 `_auto_session_lock`，而启动预热持锁跑完整个浏览器启动（上限 30s）——这是该调用
  路径上**最后一个无界步骤**，足以把调用推过客户端请求超时。现在按本次调用的剩余预算
  限时等待（只限等待，**不取消**已在进行的启动，避免留下半开的浏览器），拿不到就降级
  返回 HTTP 层结果并标注 `escalation_path=http(browser_busy)` 与可操作提示。不传
  `lock_wait` 的调用方（`screenshot` 等）行为不变。
  补 `tests/test_paths.py::TestMigrationNeverBlocksTheEventLoop`（3 例）与
  `tests/test_server.py::TestStealthySessionBudget`（3 例）。

### 修复（描述与代码不一致）

- **`parse` 关于 PDF 的说法是假的。** 它写「PDF 用 smart_fetch（有 OCR）」，内部报错提示
  更具体地教人用 `smart_fetch(url='file:///...')` —— 但 `file://` 在
  `security._BLOCKED_SCHEMES` 里，裸路径与正斜杠路径同样被拒，三条路全不通。实测：

  ```
  parse(.pdf)                    -> content_ok=False, "Use: smart_fetch(url='file:///...')"
  smart_fetch('file:///...')     -> SecurityError: URL scheme 'file' is not allowed
  smart_fetch('C:\\...\\x.pdf')  -> SecurityError: URL contains backslash character
  ```

  照描述走的 agent 会白烧两次调用。**现已让 `parse` 自己解析 PDF**（见下），
  描述与内部提示同步改准。

- **`instructions` 写「searches 5 engines」，实际默认池是 6 个**（`DEFAULT_ENGINES`）。
  数字改由常量计算，并加测试守卫。

### 修复（本地 PDF 无路可走）

- **`parse` 声明支持 `.pdf` 却从不解析它。** `SUPPORTED_EXTENSIONS` 一直含 `.pdf`，
  但实现只返回一句「用 `smart_fetch(url='file:///...')`」—— 而 `file://` 在
  `security._BLOCKED_SCHEMES` 里，裸路径含反斜杠被拒、正斜杠路径不是合法 URL，
  三条路全不通。于是本地 PDF 实际无路可走，照描述走的 agent 白烧两次调用。
  现在 `parse` 读文件字节后调 `pdf_extractor.extract_pdf()` —— 与 `smart_fetch`
  处理 PDF URL 是**同一个提取器**，所以本地文件拿到完全一致的处理：版面感知
  markdown、扫描件 OCR 回退、质量分。
- **没有放宽安全边界**：`parse` 收的是调用方给的文件路径，本来就在读
  `.docx/.xlsx/.csv/.html`，加 `.pdf` 不新增任何能力；`smart_fetch` 的 `file://`
  拦截是 **URL 侧**的 SSRF 守卫，是另一回事。既有的路径穿越校验照旧生效。
- 失败如实出声：扫描件（无文字层）返回 `no extractable text` 并说明原因，而不是
  空内容。实测 `tests/background_checks.pdf` 提出 6,716 字符 markdown，
  `tests/dummy.pdf`（纯图片）如实报错。

### 修复（顺带发现）

- **`_other_dhole_pids()` 在中文 Windows 上静默失效。** 它用 `subprocess.check_output(...,
  text=True)` 读 `tasklist`，而 Windows 控制台按 OEM 代码页（中文系统 GBK）输出；
  Python 的 UTF-8 模式下 `text=True` 的解码器是 utf-8，于是「没有匹配进程」时
  tasklist 的中文提示解码失败。异常发生在读取线程里，`check_output` 只抛出无关的
  `TypeError`，被 `except Exception` 吞掉后**永远返回空列表** —— 有残留进程也看不出来。
  两处调用加 `errors="replace"`（要解析的 `dhole.exe` 与 PID 全是 ASCII）。
  这是 `--doctor` 的「no stale servers」检查，属静默假阴性，由上一轮新接线的
  doctor 路径首次常态化触发。

### 新增（`dhole proxy` 子命令）

- **代理池此前只能靠手写配置文件或环境变量维护，而 `search_proxy.py` 的模块
  docstring 却宣称有 `dhole proxy add/list/remove/clear`** —— 文档承诺了一个不存在的
  命令（实测 `dhole proxy list` 报 `unrecognized arguments`）。轮换核心一直是完整的，
  缺的只是写入侧：`add_proxy` / `remove_proxy` / `clear_proxies` / `list_proxies` /
  `save_proxies` / `reset_pool` 从未进过本仓库（`git log -S 'def add_proxy'` 为空，
  不是后来删掉的），本次补齐并接到 CLI。
- 失败一律出声：重复、不支持的 scheme、索引越界、池满（20）都报错并给非零退出码，
  不再有「以为加上了、三轮搜索后才发现没有」这种状态。
- **凭据永不上终端**：配置文件按文档是明文存储，但 `list` / `add` 的输出一律走
  `_redact()`；`tests/test_proxy_cli.py` 有两条断言专门盯着密码不出现在 stdout。
- 新增 `_env_proxy_source()`：`list` 与 `--doctor` 会说明当前生效的是哪个环境变量。
  这解决本机真实踩到的一个坑 —— **Windows 上 `os.environ` 大小写不敏感**，沙箱/CI 设的
  小写 `https_proxy` 会被 `HTTPS_PROXY` 查到并静默进池（那 13 个 proxy 测试误红的根因）。

### 新增（`dhole --doctor`）

- `-v` 只报告「能做什么」，doctor 补上「装得对不对」：启动器是否在 PATH、**模块实际从
  哪个文件加载**、发行元数据与模块版本是否一致、启动器残留、残留进程、核心依赖、状态
  目录可写性、代理池，最后附能力面板。每项失败都打印可直接复制的修复命令，并以退出码
  `1` 结束，可用于脚本或 CI。
- `module loaded from` 一行是刻意的：装的是构建好的 wheel 而非 editable 时，pytest 会
  静默跑 site-packages 里的旧副本 —— 那行让这件事一眼可见。
- doctor 永不抛异常（它正是用户在「已经坏了」时才跑的命令）：`repair_script_path()`
  同样依赖状态目录，在目录不可用时也会失败，已一并收进 try 保护，并有测试覆盖。
- 复用了 `updater.py` 里既有但未接线的 `_heal_cmd` / `_diagnose` / `_write_repair_script`。

### 变更（工具描述与 instructions）

描述是 agent 判断「该用哪个工具」的唯一依据，因此这次改的是**路由准确性**，
顺带删掉重复与营销话术。实测（tiktoken `cl100k_base`，客户端实际收到的 wire JSON）：

|                  | 改前     | 改后             |
| ---------------- | -------- | ---------------- |
| `smart_fetch`    | 1268     | 981              |
| `smart_search`   | 686      | 519              |
| `smart_crawl`    | 633      | 570              |
| **connect 总计** | **3774** | **3264（−14%）** |

- **补上一条缺失的路由规则**：`smart_fetch(urls=[...])` 与 `smart_crawl(crawl_urls=[...])`
  都能抓一批已知 URL，两边描述都没说该用哪个，agent 只能猜。现在 `smart_crawl`
  明确写出「已有确切 URL 就用 smart_fetch，不必爬」。
- **修掉一处自相矛盾**：`smart_search` 描述让人「再去 smart_fetch 高相关结果」，而它自己的
  `options.fetch_content` 就是干这个的 —— 按描述走会白花 N 次调用。现在两者串成一句。
- **删除重复**：`instructions` 的 GOTCHAS 与 `smart_fetch` 的 RESPONSE SIGNALS 曾逐条
  重复（content_ok / next_action / next_offset / cache_ttl / DataDome）。现在
  `instructions` 只留**跨工具的规则**，`smart_fetch` 只留**该工具的信号清单**。
  `smart_fetch` 描述里与参数说明重复的能力清单（pages/actions/css_selector/
  include_links 等）改为一行索引。
- **删掉无信息量的句子**：营销式复述（"returns the real page content built-in fetch
  often blocks or reduces to a stub"）、参数默认值的两处重复、`options` 里 6 个
  「rarely needed」的防检测调参键（改为「存在且默认值够用」一句带过，仍可从
  `_strict_options` 的报错里发现）。

### 文档

- README 补三块上手内容：**让 agent 自己装**（一段可直接粘贴的 prompt）、
  **和谁比、不跟谁比**（按场景划分的定位表，不对其他项目的能力细节下断言）、
  **上下文开销**（连接时合计 tokens，含逐工具拆分与复现方法），数字随描述收敛
  同步更新为 3,264；并注明其中约三成是 schema 的结构开销，压措辞对它无效。
- 配置表补上 `search_proxies.json` 状态文件，以及 Windows 大小写那一坑的说明。

### 测试

- 新增 `tests/test_tool_descriptions.py`（35 例），把描述当契约来钉：
  - **路由规则**：8 条「哪个工具该把 agent 导向哪个」的守卫，每条对应一个真实的误用场景
  - **事实性**：instructions 的引擎数与 `DEFAULT_ENGINES` 一致、`smart_search` 列出的
    默认池与常量一致、`parse` 必须声明且真的实现本地 PDF、任何描述不得推荐 `file://`
  - **一致性**：描述里 `foo=` 提到的参数必须真的存在（防「描述承诺了不存在的参数」）
  - **预算**：逐工具与合计的 wire 体积上限，留约 10% 余量 —— 让「描述变胖」变成
    有意识的动作，而不是无声漂移
- 新增 `tests/test_proxy_cli.py` 与 `tests/test_doctor.py`，共 49 例；
  `tests/test_doctor.py` 另增 3 例覆盖进程扫描的编码安全。
- `conftest.py` 的 `_no_real_home_state_writes` 增补第四个可写状态文件
  （`search_proxies.json`）的路径接管 —— 否则测试会把真实凭据写进用户的 `~/.dhole`。
- `tests/e2e_mcp_test.py::test_tool_definitions` 里两条断言在 13.16 重写工具描述
  之后就再没成立过（`pytest -m e2e` 默认不跑，所以一直没暴露）：`"Fetch any URL" in
  smart_fetch 描述` 与 `"cache" in smart_search 描述`。前者改为断言现描述里的稳定
  特征，后者改挂在 `options.cache_ttl` 这个**公开选项**上而不是散文上；并补一条
  `schema` 必须同时出现在描述与 `inputSchema` 里的守卫。

测试 949 → 1060，全部离线（默认运行零网络）；`-m e2e` 9 例全绿。

## [14.5] - 2026-09-21

一次以「让静默降级可被观测、把说得太满的地方改准」为主的版本。测试 755 → 949，
仍然全部离线（默认运行零网络）。

### 修复（静默降级）

- **`engines_consensus` 不再在降级池上伪装成「全员一致」。** 分母原先是「本轮实际
  返回了结果的索引家族数」，于是 4 个家族 empty/被墙、只剩 1 个活着时，那 1 个的每条
  结果都渲染成 `1 of 1` —— 一个本该削弱结论的信号反过来给结论加了分。分母改为由配置池
  推出的家族全集（全家被 preempted、根本没轮到的才扣除），并新增 `consensus_basis`
  （`full|single_family|partial_pool|degraded_pool`）。只有 1 个家族时不再伪装成比率：
  `1 of 1 (no corroboration)`。**引擎数 ≠ 索引家族数**（bing/duckduckgo/yahoo 同一索引）：
  修这个 bug 时是 5 引擎 / 3 家族，本版把 sogou_weixin 加入默认池后是 6 / 4 —— 满分是
  `4 of 4`，此前文档里的 `3 of 5` 是不可能出现的字符串。本机实测 10/10 次搜索都是这个形态。
- **降级标注此前在搜索缓存命中时全部丢失。** 「部分引擎未贡献」与「低置信度」两条只加在
  live 路径上，TTL 内的重复查询（agent 的常态）不带任何提示——同一份降级结果，第二次问
  就被包装成干净结果。抽成 `_pool_health_notes` 供两条路径共用，并有回归测试。
- **`empty`（引擎答了、解析出 0 条可用）此前在响应里彻底隐形**：它 `ok` 与 `blocked`
  都是 False，于是既不进 `engines_used` 也不进 `engine_blocked`。新增
  `engine_empty` / `engine_preempted` 两个列表；`_search_next_action` 的分母算上
  empty（4/5 解析器坏了此前被读成 1/1 = 健康，多样性警告永不触发），并把「被墙」与
  「答了但解析不出东西」分开表述。顺带修一处错分：`init_error:*` / `no_key:*` 不以
  `error` 开头，原先落到 else 被当成「无结果」。
- **零结果时的自动改写按原因分别处置。** 原先对「查询真的太窄」和「解析器跟不上页面」
  一视同仁，后者会再打一轮全量 fan-out（在上游正改版那天把请求量翻倍），且最终输出
  `Try rephrasing the query.`——把自家故障诊断成用户的查询问题。现在依据每引擎产出计数
  判定：`item_nodes>0 且 usable==0` 且**全部**引擎皆如此时跳过第二轮并说明是解析器漂移、
  指向 `dhole -v`；部分坏则第二轮只问还活着的引擎。**窄查询的改写救回原样保留**。
- **口令保护的 PDF 此前被误报成"文件打不开"。** pdfplumber 把 pdfminer 的
  `PDFPasswordIncorrect` 包成 `PdfminerException`，而它的 `str()` 是**空串**；原判定按
  消息文字找 "password"/"encrypt"，于是真机上永远命不中，用户拿到的是
  `pdf_open_failed: `（连原因都没有）且 `encrypted=False` —— 加密 PDF 看起来像文件损坏，
  agent 不会想到去用 `password` 选项。改为按**类型**（含异常链里的原类型）判定，并把
  "没给口令"与"口令被拒"分开说明；加密方案不支持时是第三条独立消息。补 `tests/test_pdf_real.py`：
  用真字节（`tests/background_checks.pdf` 现场 AES-256 加密，pypdf 走 dev extra）钉住
  缺/错/对三态 + 损坏文件反例，顺带把仓库里两个从没被引用的真 PDF 接进测试。
- **`auto` 模式下「依赖装了但重排没启用」不再零解释。** 精简安装仍保持安静（那是预期
  形态，每次都解释是噪声）。
- **Bing 版面导致整轮结果为空（实测到的线上故障）。** Bing 同时跑两种标题链接版面，
  其中一种把 `<a>` 放在 `<h2>` 的**祖先**上，而 href xpath 只查后代 → 5 个结果容器全部
  读不出链接、整轮 bing 为空、状态记成 `empty` 且在响应里哪儿都不出现。连测 5 次都是这
  一版，`Bing.search()` 自带 3 次重试也救不回。改为并集 + `ancestor::a` 回退（不绑样式
  类名）。修复前后各实测确认（0 可用 → 5/10 可用）。

### 新增（可观测性）

- **每引擎解析产出统计。** 熔断器只记「引擎拒不拒绝我们」，记不到「200 好好答了但
  xpath 取不到东西」。现在每轮记 item_nodes / usable / post-filter kept 三个整数，判据
  **不依赖历史基线**（新装机第一次搜索就能判漂移），含糊那一格样本不足时如实报
  `unknown` 而不是猜。落 `~/.dhole/engine_stats.json`（≥60s 防抖），`dhole -v` 新增
  `engine yield` 行；读取端只做文件系统读，保证在半坏的精简安装上也能跑。顺带让此前
  是死代码的 `ProxyPool.status()` 在 `-v` 里有了出口。
- **默认池解析器契约测试。** 期望条数由**独立 oracle**（bs4 + html.parser，与被测的
  lxml+xpath 不共享 parser 也不共享查询语言）算出，避免「我的解析器同意我的 fixture」。
  断言分六类（oracle 一致 / 每条都有 href+title / 后处理不归零 / ck-a 必须解掉 / 摘要
  成带 / 负控与 JS 壳读成 0 容器）。fixture 内容寻址 + 禁止无主 fixture + **不写年龄
  断言**。`-m live` 另加 `--engine-fixtures` 双门槛，只有确认漂移才判红（0 结果的真
  SERP 是天气不是故障）。本机网络不可达的 brave/duckduckgo/yahoo 明确 skip 并给出补齐
  命令，**没有捏造假 fixture 冒充覆盖**。
- **`DHOLE_HOME`**：状态目录可换位置（里面是全部正文明文、搜索词与模型）。POSIX 下目录
  0700、状态文件 0600；Windows 上 chmod 基本无效，已在 README 写明那边的实际手段是换位置。
- **三个状态文件的落点改为惰性求值。** `circuit_breaker.json` / `search_feedback.json`
  原先在模块导入时就把路径快照成字符串常量，只加 `DHOLE_HOME` 会让它对一部分文件失效
  —— 半生效的开关比没有开关更糟。顺带解决一个实测到的副作用：**跑一次测试套件就会改掉
  用户真实的引擎冷却状态与域名偏好**（实测抓到 `circuit_breaker.json` 的 md5 变化，以及
  一条 brightdata 记录被写进 `engine_stats.json`）。现在 conftest 把三个落点统一指到临时
  目录，并有一组测试钉住「必须由 paths.py 派生且是惰性的」这条约定。实测复验：全量用例
  跑完，真实 `~/.dhole` 下所有文件的 size/mtime 零变化。

### 安全

- **浏览器层补齐 SSRF 守卫（此前 README 说"盲打而非数据外泄"，实测那句话是错的）。**
  入口 URL 过了 `validate_url`，但浏览器内部的重定向 / 子资源 / 页面 JS 的 fetch 都不过 ——
  一个被 JS 壳或反爬墙逼着升级到浏览器的页面，只要把浏览器引到 `http://169.254.169.254/`
  （云元数据）或 `http://127.0.0.1:9222/`（本机服务），**渲染出的内网正文会被抓回给 agent**。
  现在每个 page 都装 route handler：请求前判定目标是否解析到内网（覆盖 JS fetch/XHR、
  iframe、JS 跳转 —— 真浏览器靶场实测这三类请求一条都没发出去），落地后再判一次
  （主文档被拦或落地即内网 → 抛 `ssrf_blocked`，正文一个字不回流、不重试）。入口站点
  豁免（已过边界校验；含同站点 http↔https 升级），**异端口不豁免** ——
  `localhost:8080` → `localhost:9222` 正是典型内网跳板。
  **实测到的残余**：HTTP 3xx 重定向的目标仍会被请求一次（patchright/Playwright 的 route
  不拦截 `continue_()` 之后的重定向目标 —— 这是拦截模型，不是漏判），内容是拒了，但"盲打"
  这半边还在；连它一起堵要在解析器/代理层做，与 Cloudflare 求解这条唯一能力面冲突且无法
  在此验证，故不做，README 里写明。顺带：`page.route` 此前只在 `disable_resources=True`
  时安装，截图与交互 session 一条防线都没有，现在每条路径都装。守卫的离线单测在
  `tests/test_browser_ssrf.py`，真浏览器的靶场证明同文件标了 `e2e`（`-m e2e` 才跑，
  判红依据是靶场自己的命中计数：内网服务器一次都没被连上）。
- **缓存指纹补上 PDF 口令维度。** 14.3 给缓存键补了 cookies/headers/UA/proxy 四维，
  `password` 这一维漏了——而它是一等参数且真的参与解密，于是带口令解出的正文与同一 URL
  的匿名行撞同一个键，之后的匿名请求可直接复读。缓存层实测复现（完整 HTTP 往返未跑）。
  旧库做一次性定向清理（只删 PDF 行，走 SQLite 自带的 `PRAGMA user_version`）——作废
  整个缓存对这个工具有实代价：被拦页面在某些网络上再也抓不回来。
- **hosts 豁免按「钉到哪个值」判定。** 原先只看名字在不在文件里，而屏蔽类 hosts 把上千个
  域名钉到 `0.0.0.0`（Windows/macOS 上作为连接目标等同回环）；「攻击者写不了你的 hosts」
  这个原论证失效——他只需挑一个已经在里面的名字。黑洞钉位不再自动放行，本机开发/分流
  覆盖（`127.0.0.1`、`172.16.x`）维持豁免。本机无 `0.0.0.0` 钉位，故这条是**预防性**的。
- **`tcp_preflight` 不再是内网端口 oracle。** 它此前对外部可控 URL 做裸 TCP 连接并把
  `connection_refused`/`dns_failure` 的区别原样回报给 agent。现在连前先判一次，命中内网
  时返回与「真的不可达」同形的类别。
- **自愈重装钉在当前已装版本。** 此前 `dhole` 入口任何 ImportError 都会无人值守地
  `pip install --force-reinstall dhole-mcp`（不带版本），信任根等于包名字空间；这个 fork
  的名字改过两次，CHANGELOG 里就记着它曾因解析到上游发行名而覆盖掉本 fork。cli 与
  updater 两份模板一起钉。**残余未改**：捕获范围仍不限模块、且重装前会 kill 所有 dhole
  进程——那属于产品取向而非缺陷，`DHOLE_NO_AUTO_REPAIR=1` 可关。
- **重排模型支持发布方 sha256 真实性校验。** 原实现是「下载完算自己刚下的字节的哈希再
  写盘」，那是完整性不是真实性。机制已就位（不符即删+重试一次，仍不符则拒绝加载并说明
  原因）。API（HF 502 / hf-mirror 403）取不到摘要，改从**仓库元数据**取 LFS oid
  （`<repo>/raw/<rev>/<path>` 是 git 里提交的那份指针，不是下载端点）：bge-zh 与
  ms-marco 的 oid 已填，且与本机实际下载的字节逐位核对一致（两个文件都真在用）；
  zh-full 未在本机下载过，无从核对，**留空**（机制对它是惰性的——宁可空着也不填一个
  只能靠信任的数字）。有测试断言「填了就必须是 64 位十六进制、且不许出现未核对的摘要」。
- 新增 `security.url_targets_internal()`：复用 `validate_url` 本体的单一内网谓词，
  供 preflight 与后续浏览器层共用，避免出现第二份判定实现。

### 变更（默认引擎池）

- **sogou_weixin（搜狗微信）进入默认池**：默认池 5 → 6 引擎、3 → 4 索引家族。理由是它
  国内裸网直连（实测 0.2-0.9s），让国内自然可达的引擎凑到 3 个（bing/yandex/sogou_weixin），
  恰好够 `min_engines=3` 的多样性门槛——不必为了凑引擎数去挂 VPN；内容池也是独家的
  （公众号文章在 Bing/百度里搜不全）。随之处理的五件事：
  - 它是**垂直索引**（只覆盖公众号文章）。有神经重排器时相关性由模型判；**没有重排器**
    （精简安装）时的兜底顺序就是完成顺序，而它答得比 bing(1.3s)/yandex(3.1s) 快，会把
    公众号结果顶到最前。新增 `_general_first()`：纯垂直结果（没有被任何通用引擎也返回过的
    URL）排到通用结果之后；有重排器时这条规则不介入。
  - **它也不能独自填满早退配额**（实测踩到的第二个坑）：sogou 一次回 10 条，配额
    (`max_results+4`) 被它一个人满足，2s 软截止一到就把还在跑的通用引擎全 cancel ——
    本机实测 yandex 就是这样被砍掉的，而它恰是另一个国内可达的通用索引：**为了加宽池子
    而加入的引擎，反而把池子收窄了**。现在早退的"结果够多了"只算通用引擎的产出（通用
    引擎全部不在跑时，垂直结果仍可自己触发早退，不牺牲"别等死引擎"的初衷）。软截止也
    顺手提成模块常量 `_SOFT_DEADLINE`，让这条规则的测试不必真睡 2 秒。修完实测：
    `engines_used=[sogou_weixin, bing, yandex]`、分母回到 4，搜索耗时 2.0s → 2.9s。
  - 它拿**原始** query，不拿意图展开后的变体：`_INTENT_EXPANSIONS` 只有两串英文，对几乎
    全中文的公众号索引只有反作用（进 `_CORE_QUERY_ENGINES`）。
  - 默认池引擎首次凑齐「有真页面契约」的一个（`tests/engine_fixtures/sogou_weixin.html`，
    活页 10 容器裁到 4）。收这个 fixture 时踩到并修掉两个测试基建 bug：裁剪只留结果容器会
    丢掉 `items_xpath` 依赖的**祖先链**（sogou 的 xpath 要求 `ul.news-list` 祖先，剪完
    解析器一条都匹配不到），以及 token 清洗/守卫正则只认裸 `&`、漏掉 HTML 转义的
    `&amp;token=`（真页面里就是后者）。
  - 文档与 schema 都注明了：sogou 的结果是 `weixin.sogou.com/link?url=` 跳转包装，浏览器
    能打开，但不是文章原始 URL。

### 变更 / 修正的说明

- `dhole --cache-ttl` 此前是**死参数**（默认值在函数定义时被焊进签名，实例值永远被遮住），
  现已生效；`cache_ttl=0` 绕开缓存的既有语义不变。
- 意图展开的 core 引擎集合去掉三个从未存在的名字（mojeek/startpage/google/qwant），
  bing 不再是被唯一改写提问的默认引擎；补「集合里不许出现不存在的引擎名」守卫。
- 默认池在两个模块各有一份，**未强行合并**（合并会逼 `search_engines` 顶层 import
  primp/lxml，破掉刻意的惰性导入），改为一致性测试钉住——这是对原计划的有意偏离。
- 修正三处与实际引擎池矛盾的说明、README 两处过度承诺（HF「字节一致」、模型「做哈希
  校验」）与一处模型尺寸/文件数标注。
- README 新增「SSRF 防护覆盖到哪里、到不了哪里」小节，并首次写明全部安全结论所依赖的
  **单机单用户前提**。原计划给浏览器层加 `--host-resolver-rules`，**未实施**：建立不起
  可稳定复现的 Cloudflare 回归样本，在无验证手段的前提下改动唯一不可测的能力面风险高于
  收益，故按计划写明降级为文档。

## [14.4] - 2026-09-21

### 新增

- **重排模型可选，默认换成中英双语的 `bge-zh`。** 相关性排序用一个
  本地 cross-encoder——它回答的是「这条结果跟问题真有关吗」，引擎自己的排名给
  不了这个信息。注册三个模型：
  - **`bge-zh`（默认）**：BAAI `bge-reranker-base` 的 int8 版
    （`Xenova/bge-reranker-base`，中英双语训练，~279MB）——中文 ranking 比多语
    蒸馏好，体积也比 fp32 小 38%。
  - **`zh-full`**：`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`（fp32，~450MB，
    跨语言）——想要极限多语精度、且带宽够时用。
  - **`ms-marco`**：英文 MS MARCO MiniLM（~91MB，14.x 默认，保留兼容）。
  - **选择方式**：`dhole model`（列出 + 标注当前生效）/
    `dhole model use <name>`，或直接编辑
    `~/.dhole/config/reranker.json`（`{"model": "..."}`）。CLI 写的是同一个
    文件，不是第二份事实来源。
  - **未注册的名字一律拒绝**：`dhole model use` 报错并不写文件；文件里写了
    未知名则回退默认 + 告警——绝不静默换个模型给结果打分。
  - **模型各自独立目录**（`~/.dhole/models/<name>/`），切换不覆盖对方；
    14.x 已下载的英文模型目录会自动改名保留（`msmarco-minilm-l6-v2` →
    `ms-marco`），**不会因为改名重下 91MB**。
  - **下载可断点续传**：HF CDN 首字节慢、字节流会卡。现在下载失败保留
    `.part`，下次从断点续传；连续 90s 无字节则主动中止而不是挂着；文件写完前
    有 100MB/300MB/50MB 的尺寸下限，截断的下载不会被当真。
  - `dhole -v` 的 neural rerank 行现在报出**生效的模型名 + 描述**，未下载时
    报出该模型的体积与配置文件位置。
- 新增 `tests/test_reranker_models.py`：注册表完整性（每个条目都可下载可加载）、
  默认值、配置文件选择、未知名拒绝/回退、`model_present()` 跟随生效模型、
  目录改名不覆盖、配置路径落在同一根下。

### 变更

- `reranker.MODEL_ID` / `MODEL_REV` / `MODEL_DIR` 保留为兼容别名（指向默认
  模型）；运行时取模型请用 `active_model()` / `active_model_dir()`。

## [14.3] - 2026-09-21

### 修复（信任信号）

- **`envelope.classify_source` 的 gov 判定可被任意域名伪造。** 原实现测
  `".gov." in host`，于是 `foo.gov.attacker.com`（完全由攻击者注册）被判为
  `gov` + `is_official=True` 并直接交给 agent 当权威信号。改为
  `host == "gov"` / `*.gov`（美国 .gov 为不可注册 TLD）/ `*.gov.<2 字母 ccTLD>`
  （gov.uk / gov.br / gov.cn，label 对由注册局保留）。
- **`docs.*` / `developer.*` 不再返回 `is_official=True`。** 这是形状信号不是
  权威信号：任何站点都能给自己的域名起一个 docs 子域。`source_type` 照旧
  返回 `docs-site`，字段描述与指令措辞同步改为与实现一致（is_official 只对
  gov / edu / github 为真）。

### 修复（作用域）

- **缓存按请求上下文分区。** 缓存键原本只有 URL + 抽取参数，而
  `~/.dhole_mcp_cache/cache.db` 是整机共享的：带 cookies/auth 抓来的正文会被
  之后一次匿名抓取原样回放（`cached=True`、`content_ok=true`），
  `include_media=false` 抓的内容也会顶替 `include_media=true` 的请求。现在
  cookies / 自定义头 / UA / 代理 / 内容开关会进指纹（`server._cache_context`）；
  纯默认请求指纹为空，已有缓存条目与旧键完全一致，不会被一次性作废。
- **隐式域名加权默认关闭。** `fetch_content` 抓到过的域名会永久 +0.05 并落盘
  `search_feedback.json`——按「抓到过」而非「有用」改写跨引擎共识排序，且是
  用户没要求过的持久状态。改为 `DHOLE_SEARCH_FEEDBACK=1` 显式开启，关闭时
  连文件都不写。
- **DNS 内网复查默认开启**（`DHOLE_SSRF_DNS_RECHECK=0` 关闭）。工具的输入是
  agent 从不受信任来源拿到的 URL，"公网域名指向 127.0.0.1 / 169.254.169.254 /
  内网段"是 SSRF 主路径。为不误伤"本机故意阻断"的场景：**hosts 文件里显式
  钉住的域名一律放行**（攻击者改不了你的 hosts 文件），解析结果加短 TTL 缓存
  （拒绝 5 分钟 / 放行 5 秒），一次抓取的重定向链不会重复解析。错误信息里直接
  带上关闭开关的名字。
- **自愈路径不再硬编码公开发行版名。** `dhole` 入口在 ImportError 时会
  `pip install --force-reinstall <dist>`，而 dist 原本写死为 `dhole-mcp`：配置了
  `DHOLE_UPDATE_PACKAGE` 的 fork 会被装回公开包。现在包名与
  `DHOLE_UPDATE_INDEX_URL` 都跟随配置（`cli._dist_name` / `updater` 生成的脚本
  同样支持 index），并新增 `DHOLE_NO_AUTO_REPAIR=1` 让「任意 ImportError 触发
  无人值守重装」这条路径可以被关掉。

### 新增

- **`dhole -v` 报告真实能力。** 附上 browser tier / pdf+ocr / neural rerank（含
  模型是否已下载）/ search pool 四行状态。这些能力**缺失时全部静默降级**，
  所以诊断命令是唯一能看出"装了个更弱的版本"的地方。
- **`DHOLE_USAGE_LOG`（默认关）** 本地调用日志：工具名、成功与否、耗时、脱敏
  后的错误，**不记参数值、不联网**。这个工具最大的失败面是静默的（模型根本
  不调用它），没有本地记录就无法回答。
- **`DHOLE_NO_AUTO_REPAIR`**（见上）。
- **模型下载源可回退**（`DHOLE_HF_ENDPOINT` / `HF_ENDPOINT` 可指定）：默认先试
  `huggingface.co`，失败自动回退 `hf-mirror.com`。revision 固定，任何端点的
  字节一致——回退只改变"从哪下"，不改变"下到什么"。在这台机器上
  `huggingface.co` 被 hosts 钉死，没有回退就意味着模型永远下不回来、搜索
  排序永久静默降级。
- 指令与工具描述里显式声明：抓回来的页面正文是**不可信数据**，其中的指令
  一律不执行（正文与服务器自写的 `next_action`/`summary` 走同一条信道）。

### 变更

- **运行时文件全部收敛到一个目录：`~/.dhole/`。** 之前状态在 `~/.dhole/`、缓存
  与模型在 `~/.dhole_mcp_cache/`——「这工具在我机器上留下了什么」要两个目录才
  答得全，卸载说明也不完整。现在 `src/dhole_mcp/paths.py` 是唯一事实来源
  （cache.db / models/ / circuit_breaker.json / search_feedback.json /
  usage.jsonl / last_version / repair.py / search_proxies.json），cache、
  reranker、search、server、updater 全部走它。旧目录在首次使用时自动搬移
  （**不会重下 90MB 模型**——在 hosts 钉死 HF / 代理不可用的网络上那等于永久
  降级）：只搬目标位置缺失的条目、绝不覆盖、绝不删非空旧目录，失败最多是
  重建，不会丢新位置的数据。浏览器 profile 仍是会话结束即删的系统临时目录
  （transient，不是状态）；rapidocr 的 OCR 模型随包发行，不落这里。
- `smart_fetch` 的描述不再自称"用于所有网页抓取"，改为说明它强在哪
  （反爬墙、JS 渲染、PDF/OCR、多 URL 批量）；连接期指令的开头同样改为条件式
  表述。自我推销式路由不是可验证的承诺。
- `dhole -v` 的帮助文案改为 "version + capability check"。
- 测试套件不再依赖开发机的解析器（`conftest` 里 autouse 固定 `getaddrinfo`）：
  DNS 复查默认开启后，一台把 github.com/huggingface.co 钉到 127.0.0.1 的机器
  （本机就是这样）会让无关测试变红。`conftest` 同时全局跳过迁移
  （`_legacy_migrated`），跑测试不会搬动真实用户目录；test_paths 自己重置开关
  来测迁移本身。
- **删除 `package.json`。** 它是 pi-extension 时代（该扩展 13.14 已移除）的残留，
  且是第三个版本号来源（写着 14.0，实际 14.2.1）——正是 13.14 收敛掉的同一类
  缺陷。需要它的话 `git checkout HEAD -- package.json` 可一行还原。
- `pyproject.toml` 的 wheel packages 去掉 14.1 已删除的 `src/hound_mcp`。

## [14.2.1] - 2026-09-20

### 变更

- PyPI 描述改为中英双语简短版（中文在前）。代码与行为零变化——发版只为刷新
  PyPI 元数据（PyPI 发布后描述不可修改，只能随新版本生效）。

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
