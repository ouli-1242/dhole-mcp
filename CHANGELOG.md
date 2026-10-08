# 更新日志

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的格式：每个版本按 **新增 / 变更 / 弃用 / 移除 / 修复 / 安全** 的顺序分组，只列有内容的那些；日期用 ISO 8601；最新版本在最前。版本标题在结尾有对应 diff 链接（未打 tag 的版本不链接）。

**本文件只记使用者能感知的变化**：新增能力、行为变更、会踩到的缺陷修复、安全相关。实现细节、测试与重构见 `git log`。

> 自 13.14 起本仓库为个人衍生作品，版本号是自己的序号、不承诺语义化版本，与上游版本不可比；包内的 `__version__` 是唯一权威来源。

## [17.0] - 2026-10-01

从「网页研究工具」迈向「agent 的个人 Web 操作助手」的第一块：**带登录态抓取**与**页面操作**——CDP 接管你已登录的 Chrome，让已登录页面第一次可读；元素快照 + 索引化动作让 agent 能点、能填、能提交（提交默认暂停，等人确认）。边界不变：识别与决策归外层 agent，dhole 是零模型、零密钥的确定性工具层。

同版收尾还有一个「大众化批次」：零配置就跑通最常见的路径，配置只在需要时可见、且能确认生效。

### 新增

- **系统浏览器认 Edge（`channel='msedge'`）**：浏览器档位的检测链从 chrome → 打包版 chromium 扩为 chrome → msedge → 打包版 chromium。Edge 随 Windows 出厂，没装过 Chrome 的机器首次使用即可走满隐身档，不再触发 193MB 的 chromium 下载；Edge 与 Chrome 同属一套 Chromium 网络栈（BoringSSL），系统通道那根真实 TLS 指纹（JA4）杠杆原样成立。headless UA 按本机 Edge 的真实版本构造并自带 `Edg/<版本>` 令牌——UA 说 Chrome、浏览器自报 Sec-CH-UA 说 Edge 的自相矛盾指纹不会出现。`chromium_artifacts()` 新增 `launch_channel` 字段指名这次用的是谁；`system_chrome` 字段名不变、语义扩为"系统浏览器在场"。真机验证：强制 msedge 通道，真实 Edge（154.x）经隐身档抓取 UA 回显页成功，线上 UA 带 `Edg/154` 且无 Headless 痕迹
- **`hover` 与 `drag` 动作**：`web_act` 的动作链补齐悬停与拖拽（与 playwright-mcp 对标时唯一明确的动作缺口）。`{"hover": "css"}`（或 `{"selector"|"index", "expect"?}` dict）展开悬停菜单/提示；`{"drag": {selector|index, "to": ...}}` 把一个元素拖到另一个上，**两端**各自接受 CSS 串或快照索引、`expect` 同样可用；没有 `to` 的 drag 在校验期就拒——没有落点的拖拽不是没写完，是没意义。收尾等的是"页内 DOM 安静"（2 秒预算）：悬停菜单与排序重排是同步渲染的本地变换，没有导航可等。真机验证：靶站新增 `/hoverdrag` 端点（mouseenter 展开菜单、mousedown/mouseup 拖拽、事件账本写 localStorage 跨重载存活），选择器链与快照索引链各自跑通，悬停展开、拖拽落点的事件序列完整落账
- **带登录态抓取（CDP attach）**：`web_read` 新增 顶层 `cdp_url`（配 `confirm=true`）（指向开了 remote debugging 的本机 Chrome，如 `http://127.0.0.1:9222`），抓取在你的真实浏览器里执行，已登录站点的页面直接可读。此前浏览器层每次都 `new_context()` 新建隔离上下文——attach 能连上 Chrome，但登录态永远是空的；现在复用浏览器已存在的默认上下文（`contexts[0]`）。attach 是最高路由优先级：强制浏览器层、绕过公开缓存与 HTTP 层，与 `schema=` 互斥（显式报错，不静默丢一个）；响应带 `cdp_attached: true` 标记。前置是用户的一次性设置：Chrome 136+ 不允许默认 profile 开调试，需用 `--user-data-dir` 指向副本目录启动并在那里登录一次（README「带登录态抓取」一节有完整三步）
- **登录态判定 `check_login`**：响应新增 `login_check`，三态 `{logged_in: true/false/null, signals, cookie_names, method: "dom+cookie-names"}`——DOM 形状 + cookie 名字聚合，无模型、无密钥；`null` 表示页面形状不足以定论，信号交回调用方判断。cookie 只报名字，值不出境（与 sessions 同纪律）
- **持久 profile**：`cred_defaults` 的 `profile` 键把 dhole 自家隐身浏览器的上下文落到 `~/.dhole/profiles/<name>/`（名字限字母数字点横线下划线），持久模式关闭时不再删除目录——自己登录过的浏览器会话跨重启存活（attach 路径用不到它，它服务 HTTP/隐身层）。17.0 一度给这件事配过 `DHOLE_BROWSER_PROFILE` 环境旋钮，本版注销 —— 同一个能力不该有两扇默认开着、又会各自漂走的门
- **会话 cookie 的寿命**：24 小时（17.0 写死，`DHOLE_COOKIE_TTL_S` 随大众化批次注销）。HTTP 层「登录一次、长期复用」不再一天就断。照旧：活跃使用不续期，到期即失效
- **元素索引快照 + 索引化动作**：`web_read` 的 `actions` 新增 `{"snapshot": true}`——把页面上可交互元素（链接 / 按钮 / 输入框 / 带 role 的控件）按文档序列成编号清单（index / tag / role / name，最多 80 条，超出如实报 `elements_truncated` 与总数）。之后 `click` / `fill` / `press` / `screenshot` / `submit` 都可按 `{"index": N, "expect": "名字片段"}` 寻址，不必写选择器；快照与动作用**同一个**交互元素选择器的文档序，索引跨两次页面加载可复现；`expect` 让过期的索引大声失败（报出该位置现在读的是什么），而不是点到别的东西上。dhole 不做 `act(prompt)`——「这个按钮是干嘛的」由外层 agent 的语言能力回答，dhole 保持零模型
- **submit 审批闸**：`actions` 新增 `{"submit": {...}}`（显式动作；`click` 到提交按钮不触发它）。没有 `confirm: true` 时**暂停**：动作行标记 `pending_approval`、什么都没有提交，summary 与 `next_action` 被它接管——提醒 agent 把提交内容转述给用户，得到肯定后带 `{"confirm": true}` 重跑同一条 actions 链（页面停在提交前的状态）。暂停与放行都进写操作审计。`confirm: true` 是 agent 对「用户已确认」的断言，dhole 无从验证——闸门的意义正是把不可逆的一步变成要过明面的决定
- **看图那套纪律默认在场**：`web_read` 的 `check_login` 之外，声明**调用方** agent 能看图这件事不再需要旗（不是 dhole 长了眼睛）。今天的形状：`web_screenshot` 与 `{"screenshot": ...}` 动作接受 `selector` / `index` 做元素裁剪截图（上限 2MB），图片作为图片块随响应走、不塞 JSON（base64 会给 transcript 与缓存的每一份拷贝加税），`metadata.actions` 只留收执（`image: true`）；工具描述同时追加看图纪律——图形验证码 ≤3 次就转交用户，DataDome / Akamai / 交互式 Turnstile / 2FA / 滑块一律转交。整页截图不受影响。17.0 曾有 `DHOLE_MULTIMODAL` 许可旗，本版注销：一条烙在工具描述里的纪律不需要每次连接都问一遍开没开
- **`decode_qr`（确定性本地解码）**：`web_screenshot` 与截图动作传 `decode_qr: true`，用 OpenCV（`opencv-python-headless`，随 `pip install "dhole-mcp[all]"`）在本地解二维码，解出的文本列进收执；元素裁剪天然缺静区，解码前自动补白边 / 放大重试；页面里没有码也照常回图，只在收执里说明。它从来不需要任何旗——这是确定性的本地计算，不是视觉任务；缺依赖时报安装命令而不是静默失败
- **神经 focus（`focus_engine='neural'`）**：`web_read` 带 `focus` 时不再只按 BM25 关键词重合挑块，改用本地重排模型按**语义**给块打分选段（分块自动适配编码器窗口）。这是**每次调用**的意愿，不是机器级许可：17.0 注销了 `DHOLE_NEURAL_FOCUS`，模型还没下载时**显式报错**（`neural_focus_model_missing`，一个请求都不发，绝不静默下载也绝不静默降级），要 BM25 就明写 `focus_engine='bm25'`。代价写在手册里：每页约 12.5 秒 CPU。运行中途模型出错才回退 BM25，并在 focus 头部注明。
- **`focus_mode="best"`**：`web_read` 传顶层 `focus_mode="best"` 时不再返回一堆片段，而是返回**单个**得分最高的块加它前面的标题——「答案在哪一段」的直接回答。BM25 与神经两档都可用
- **整站爬取的相关性过滤**：`web_crawl` 带 `focus` 且点名 `focus_engine='neural'` 时，每页正文先按焦点查询打分，低于 0.03（实测离题带）的页面不再入队扩展——预算花在相关的子树上。模型缺席时放行（fail-open：爬取不该被一个可选增强卡死），调用方点名的起始 URL 永不丢，丢了多少页在 summary 报数
- **意图命中提示**：神经 focus 下最高分块低于 0.05 时，`next_action` 提示「抓回的内容对焦点查询命中很弱」——页面是真的，但答案大概率不在这页上。`content_ok` 不因此变脸：内容为真与答了所问是两个问题
- **会话 cookie 双向桥**：点名的 `session_id` 走浏览器层时（`force_fetcher='stealthy'` 或自动升级到隐身层），该会话的专用隐身浏览器在导航前从 HTTP 层 jar **播种** cookie、响应后**回写**新增的——登录态第一次跨两层共用。边界收得很窄：只有调用方点名的会话桥（共享的自动会话永不播种，那等于把 A 会话的登录态倒进 B）；CDP attach 永不桥（那是你的真实 profile，只读不写）；桥只发生在「这个会话自己的浏览器」上。配合 `check_login`、元素快照与提交闸，不写密码的登录流兜底路径成立：HTTP 层攒 cookie → 桥进浏览器 → 快照认字段 → 提交走审批闸（独立登录工具当时要等凭据源；`cred_vault` / `cred_login` 落地后未发布即移除，见下）
- **写操作审计**：`DHOLE_USAGE_LOG` 的审计通道现在也记**写**：HTTP 层所有非 GET（`channel=http`，不重试是写请求的既有纪律）与浏览器 submit（`channel=browser_submit`），记 method / url / session_id / 是否 pending。读和写从此都看得见
- **`allow_private` 覆盖到 `web_screenshot` 与 `feed_read`**：`web_read` 一直能点名私网目标（`allow_private`），另两个工具不能——于是「抓我自己的 dev 服务」能跑、「截我自己的 dev 页面」和「轮询我自己的 feed」直接拒，同一个产品决定在两个工具上读出相反的答案。现在三个工具同一套机制、同一套语义，且**只有一扇门**：`cred_defaults` 的 `allow_private` 键（`true` 只放开回环，或给主机名单；`DHOLE_ALLOW_PRIVATE_HOSTS` 是操作员那侧的同义词），云元数据端点照旧永不放开。`web_screenshot` 的作用域盖住整通调用（含导航），因为回环页面自己的子资源由浏览器路由处理器判定，只放开入口校验等于「页面加载了、图被拦了」
- **重复任务 `web_recipe` / `web_watch`（默认注册）**：计划第九章编排轴的最后两级——复现与监控。`web_recipe` 记下一条 `{url, actions}` 抓取路径，之后一次调用重放；**动作在录制时就过 `actions.validate_actions`**（与执行器同一套），所以库里不可能存在一条执行器会拒绝的链——存一个永远跑不通的配方比不存更糟，调用方会以为「存下来了就是对的」。重放返回完整结果 + 每条动作的收执 + `actions_failed`，非零即页面变了、`next_action` 直接让人重录。`web_watch` 盯一个 URL：`check` 强制新鲜抓取（`cache_ttl=0`）并与上次快照做行级 diff，返回 `changed` / 增删行数 / 限长 unified diff。**首跑只建基线、`changed` 是 `null` 而不是 `false`**——没有基线不等于没有变化，把首跑报成「无变化」是凭空造一个事实；**抓取失败不动基线**——否则一次网络故障会被写成「页面清空了」。`focus` 让 diff 只落在相关内容上（长页面的正确用法：页眉页脚的时间戳与广告位每加载一次就变）。两者共用一个本地库 `~/.dhole/tasks.db`（0600，与 `sessions.db` 同信任级别），各 200 条 + 快照 200k 字符上限，超限如实报 `baseline_cut`；**不存凭据**，`session_id` 每次 run/check 现传。本版不再用旗守着这两颗：默认注册，代价改由连接期文本本身收 —— 见「变更」里的 connect 面账目。**这两颗未发布即移除**，理由与省下的字符账见「移除」
- **验证码靶站与视觉辅助基线**：靶站新增 `/captcha`、`/captcha.png`（扭曲文字：逐字旋转 + 正弦位移 + 干扰线/噪点/笔画擦除，两档难度）、`/captcha/verify`；答案由 token 确定性派生（无状态、重启后不变），出题与判卷分离（`--emit` 不打印答案）。`evals/captcha_baseline.py` 量出：二维码解码 9/10（唯一未过是 σ=60 高斯噪声，信息层面已不可解）、验证码一次读对率 `normal` 36% / `hard` 12%、当时那面 `DHOLE_MULTIMODAL` 旗（本版注销，设了也不再被读）确实只在开旗时才把试读纪律送进 wire —— 今天这条纪律烙在工具描述里，不再需要旗。不变式由 `tests/test_captcha_fixture.py` 钉住（重点：答案只由 token 决定、与难度档无关）
- **凭据来源 `cred_vault` 与独立登录流 `cred_login`（默认注册）**：计划第七章被"凭据源未就位"卡住的两项，随凭据源一起落地。`cred_vault` **不自造保险库**——读用户已有的密码管理器 CLI（Bitwarden `bw` / 1Password `op`），`op=status` 报哪家装了、解锁了，`op=list` 回条目元数据，`op=match` 按 URL 主机找候选；**密码与 TOTP 的值任何 op 都不回**，`VaultModel` 里没有能装它们的字段（"凭据不出境"由接口形状保证，而不是由每个调用点记得脱敏）。解锁令牌从进程环境继承、永不进 argv。`cred_login` 用其中一条凭据登录：**不带 `confirm` 时只做一次只读快照**，按 `type=password`（事实，不是"请登录"这类文案）认出用户名 / 密码 / 提交控件并回计划，**一个字节都不填**——填本身也不可逆（页面上的输入事件能把值发出去），所以它和提交一起等在闸后；带 `confirm=true` 才填并提交，之后复用 `check_login` 的无模型探针判定 `logged_in` 并把 cookie 留在 `session_id` 下。密码由服务器侧读一次、直接喂进输入框，**不进响应 / `metadata.actions` 收执 / `DHOLE_USAGE_LOG` 审计行 / cache.db**——回执点名的是 vault 条目名。`confirm=true` 必须给 `session_id`，否则**在开浏览器之前**拒绝（共享的 auto 会话不能承接登录态，那是既有红线）。没有提交控件时在密码框回车，回车同样在闸后。它今天也是一颗默认注册的工具 —— 但"能不能读你的密码管理器"这件事的保证已经从开关搬到了接口上：`cred_vault` 没有能装密码的字段，任何 op 都不回值，所以注册面开不开都读不到东西。**这两颗未发布即移除**，理由与省下的字符账见「移除」

- **`sys_guide` 工具（读 dhole 自己手册的那颗，默认注册）**：agent 一次调用就能读到 dhole 自己的手册（七个 topic，`section=` 取其中一节），随包语料成为唯一一份说明书，`dhole skill install` 降级为可选加分。它落地时叫 `dhole_doc`、是第 14 颗，本版 B5 改名成 `sys_guide` 并定稿到 13 颗 —— 记在这里是为了让「第 14 个」那句话不再冒充今天的编号
- **`dhole config`（第二条配置门）**：`list | set | unset` 读写 `~/.dhole/config.json`，按次读取的旋钮下一次调用就生效，不用改宿主 JSON、不用重启；env 仍然优先，README 的配置一节同步改成两条门并列
- **`focus_engine`**：每次调用自己说要哪种打分器。`'bm25'` 是默认；`'neural'` 而模型还没下载时**显式拒绝**（`neural_focus_model_missing`）而不是静默降级、也不是静默下载。17.0 早期那一层`DHOLE_NEURAL_FOCUS` 机器级许可旗随大众化批次注销，设了也不再被读 —— 许可与意愿两层门说的其实是同一件事，留两层就会有两份会各自漂走的默认值
- **fake-IP 代理环境自己适配**：Clash / sing-box 的 TUN + fake-ip 下不再需要事先知道要关掉 DNS 复检（当年那颗叫 `DHOLE_SSRF_DNS_RECHECK` 的旋钮本版注销，现在由 dhole 自己认出这种环境），IP 字面量 / 主机名黑名单 / 云元数据 / 私网这些守卫一条不动
- **`dhole --doctor` 多三段体检**：配置审计（env 与文件两个门一起读，非法值计入 failure，孤儿键单独报）、resolver 现实检查、`cdp fence`（attach 开着而没有围栏时说明白，算 info 不算故障）
- **工具表定稿为 13 颗，名字统一成 `web_*` / `cred_*` / `sys_*`**：这一版把工具拆到职责边界上并改名 —— `smart_fetch` 改名成 `web_read`、`smart_crawl` → `web_crawl`、`smart_search` → `web_search`、`feed_fetch` → `feed_read`、`parse` → `doc_parse`、`screenshot` → `web_screenshot`，`dhole_doc` → `sys_guide`。**旧名不会静默成功**：调 `smart_fetch` 得到的是 `Unknown tool: smart_fetch` 加当前 17 颗的名字清单，不是"这名字还有用但行为变了"。拆分的判据是「一扇门只做一件事」——`web_read` 不再既读又写、既读又动页面，于是 `content_ok` 那一类信号只需要在一个地方解释
- **`web_act`（动作链从读的那扇门上搬出去）**：`click` / `fill` / `press` / `scroll` / `wait` / `snapshot` / `screenshot` / `submit` 现在是 `web_act` 的参数，`web_read` 带 `actions` 会被拒（拒绝里说明门在哪）。规矩一条没搬丢：链按顺序跑在隐身层且**永不从缓存出**、每条动作留收执、`{"snapshot": true}` 先数再按 index 打、`submit` 没有 `confirm=true` 就暂停、验证码 ≤3 次就转交用户。为什么值得拆：一条会改变页面的调用与一次只读抓取，在缓存、重试、审计三件事上的答案都相反，放在一颗工具里就得为其中一个形状写例外
- **`web_discover` 与 `web_resolve`**：地图与落点各自一扇门。`web_discover` 回答「这站上有什么」（`pages_crawled=0` 是设计而不是失败，`sitemap=true` 读站点自己的索引、不读就逐跳走），`web_resolve` 回答「这条短链最终到哪」（拿 `final_url` + status + content_type，不下正文）。拆出来之前这两件事是 `web_read` 的两个内部旗，于是"我只要地图"也要付一次正文抽取的钱
- **两层摊平：`nested_selector` / `nested_fields` / `nested_max`**：一次调用读完「列表页 + 它指向的那些页面」——第一层上用 CSS 选择器点名要跟进的元素，每个元素的 href（它自己的、肚子里那个 `<a>` 的、或外面包着它的）被抓取，`nested_fields` 在第二层上抽字段。没有它的时候 agent 的默认做法是先读列表页、再为每条链接发一次调用（实测 8 条 = 9 次调用）。三个数分开报：`matched` 是 DOM 命中、`candidates` 是去重后真能抓的条数、`expanded` 是上限允许抓的那部分，**去重掉的不算被上限藏起来**（报错了调用方会为拿不回来的那两条再抬一次 `nested_max`）。选择器 0 命中时不再只说「没抽到」：响应点名这一页上真实存在的重复链接组（`a.tag` 与装着链接的容器两种形状都报），因为这页没内容 vs 选择器写窄了是两条不同的路。第二层每个字段还带一句列质量：整列同值可能是页面装饰也可能这页只有一条，全空多半是选择器没命中——它说「去看一眼」，不替你断定

- **两台浏览器之间的那颗旋钮：`DHOLE_BROWSER_HEADED` 与 `DHOLE_BROWSER_CHANNEL`（批次 10，操作员侧；这两颗随即被批次 11 注销，能力搬回 `web_act(headed=true)` —— 见下一条。下面这一条保留是因为它记的是当时的实测与判断，不是今天的形状）**。「帮我搜索抓取应该是不显示的，帮我操作、登录此类应该显示，方便我随时辅助」这句需求把隐身层的形状问了出来：dhole **确实**自己起浏览器（`_launch_channel` → 检测 chrome → msedge → 内置 chromium），可 `headless` 在 `BrowserSession` 的签名和六个调用点上都写死成 True，也从没上 wire（实测 `'headless' not in json.dumps(_TOOL_DEFS)`）—— 起的是看不见的那台，而看不见就没法手工登录。缺的只有"窗口可见"这一颗（`profile` 早就有：`~/.dhole/profiles/<name>/`，跨调用持久）。**这颗故意不给调用方**：一个带着用户登录态的可见窗口，与 `submit` 要 confirm、CDP attach 要 confirm 是同一类事 —— 问人；所以它走 env/config 文件（`read_at="call"`，`dhole config set` 下一调用生效，不必重启宿主），并有一条反向守卫钉住"不许把可见性做成参数"。四件连带的事一起写下来：①点名 `DHOLE_BROWSER_CHANNEL=msedge` 走的是 `_launch_channel` **那一个**判据，`launch_artifact()` 与安装侧读同一个函数（批次 1 立的规矩：两处各写一遍就是"装了 119MB 却起不来"的来路），不认识的值不加，宁可用检测也别起不来 ②一台 profile 只有一台浏览器（换 profile 的代码就先关另一台），所以窗口开着时抓取会在你眼前开标签 —— 这句写在 README 与手册里，不藏 ③可见的窗口**不被空闲回收**（`AUTO_SESSION_IDLE_TIMEOUT=300` 原来无差别关 auto 会话；人在里面打字时关掉，扔掉的是半张登录表单，而那看起来像站点的问题）④说了起浏览器就要说清起的形态：开着窗口时抓回来的行带 `metadata.browser`，有命名 profile 就说登录留得住，没有就明写 `THROWAWAY`「你登进来的东西随临时目录一起没了」并指回 `cred_defaults`；旋钮关掉后这句从缓存命中的行上也会被摘掉（讲的是**本进程**，不是写下它的那个进程 —— 与 `_stamp_robots_mode` 同一个理由）。顺带修掉两处手抄的旋钮总数：`tests/test_doctor.py` 里那句 `assert "2 of 13 variables set"`（13 是 G25/17.1 两次加旋钮时抄进来的，每加一颗就要改一次）改成从 `env_registry.ALL` 读，`configuration.md` 开头那句「只剩 13 个 DHOLE_*」改成把总数交给 `dhole --doctor`。注册表自己那颗计数守卫则**按规矩改数**（13 → 15）：它不是抄错，是故意要人在动旋钮时改一次并留下理由。钉住 `tests/test_the_operator_can_ask_for_a_visible_browser.py`（15 条） （**批次 11 已把这颗旋钮整对注销**，理由与替代形态见下一条 —— 这一条留着是为了记下判据来自哪句话，不是现行做法）
- **看得见与否回到它该待的那一层：`web_act(headed=true)`（批次 11，操作员裁决）**。批次 10 那两颗旋钮注销（`DHOLE_BROWSER_HEADED` / `DHOLE_BROWSER_CHANNEL`；env 注册表 15 → 13，设了也不读，与 `DHOLE_BROWSER_PROFILE` 同一处置）。操作员给的原话就是判据：「帮我搜索抓取应该是不显示的，帮我操作、登录此类就应该显示，方便我随时辅助」「多两个配置我觉得没必要，我升级到 17.0 就是避免过多配置的原因」。两件事各自归位：①"什么时候需要人在场"只有正在做事的那一方知道，写死进机器配置意味着每次切换都要人跑去命令行，所以它是**一次调用的参数**；②"用哪一台浏览器"根本不是一个需要谁来回答的问题 —— 检测链 chrome → msedge → 内置 chromium 已经保证这台机器上有什么就用什么，真要用已登录的那台读它里面的页面，那是 CDP attach（浏览器本来就是用户自己起的）。
  四条必须一起成立这颗参数才是真的：**门只有一扇** —— `web_read` 收一个内部同名 kwarg（`web_act` 把动作链委托给它执行），但那颗名字不在它的 wire schema 上，也不在它的 dispatch 白名单里，调用方直接给 `web_read` 传 `headed` 被当表外名拒掉；`web_act` 的 schema 属性集与白名单由一条测试钉成同一份，两边各多一颗都算说谎。**它做决定，所以读字符串写法** —— `headed: "false"` 按裸值传是非空字符串 = 真，于是一次没人要窗口的调用会把浏览器开到用户屏幕上（`confirm` 一族已经栽过一次），这颗进 `_ARG_TYPES` 归一化。**那句话问实例，不问请求** —— 回执里 `metadata.browser` 由那台浏览器自己说（`session.launch_note`），因为形态是"第一次把它起来的那一次"决定的，之后的调用复用同一台；批次 10 那种读旋钮的写法会骗人：旋钮后来关掉了，窗口还在屏幕上。headless 是常态，不说；缓存命中的行把这句摘掉，因为那讲的是别的时刻的另一台机器。**不许用接管或关窗来兑现这句话** —— 一台正在跑的 headless 浏览器既不会"变成可见"（用户的 profile 突然出现在屏幕上，那像魔法而不像行为），也不被关掉另起一台可见的（扔掉的可能正是他在用的标签页），于是具名拒答 `browser_busy`、走信封（与闸门拒答、没有登录上下文同一只 `_refusal_result`，`error` 一样脱敏），`summary` 有自己的词 `browser in use`，`next_action` 把两条出路都写清：先交回窗口（`sys_state(clear=['<session id>'])`）再重问，或去掉 `headed=true` 骑现在这台。反向不冲突：看得见的那台能干后台的活，所以复用判据是**不对称**的，空闲回收也因此问实例而不是问时间 —— 屏幕上开着的窗口不会因为五分钟没流量就被关掉。
  连带：手册 `references/configuration.md` 那一节整段改写（原来教旋钮的四条换成教这颗参数与它的代价），`references/fields.md` 补上 `metadata.browser` 那一行，`references/troubleshooting.md` 与包内 SKILL 的错误表各加 `browser_busy` 一行，SKILL 的硬约束新增第 14 条（"窗口是用户的"），README 增「要看着它干活（可见浏览器）」一节并删掉那两行旋钮表。连接期 30,467 → **30,804**（+337：一颗参数名加一句 292 字的判据；后果清单全部搬进 `sys_guide(topic='config')` 按需付，wire 上只留指针 —— 第一次上抬是 `max_total_chars`，这是第二次，抬它的理由写在该文件的账目里）。钉住 `tests/test_the_operator_can_ask_for_a_visible_browser.py`（24 条，模块文档记录这次反转本身）

- **信封的机器可读孪生：`diagnosis` 与 `approx_tokens`**：`error` 是给人读的一句话，agent 要分支就得拿它做字符串匹配 —— 而措辞会变、会在版本之间漂。现在"不是干净成功"的响应带 `diagnosis: {code, retryable, recover_with}`：`code` 是稳定的 snake_case 枚举（`js_shell` / `bot_wall` / `auth_wall` / `robots_disallowed` / `extraction_collapsed` / `schema_no_match` / `cdp_authorization_required` / `browser_busy` / `proxy_unreachable` / `timeout` / `network` / `body_too_large` / `not_a_pdf` / `encoding_undecodable` / `truncated` …），`retryable` 说"原样重发这一通会不会换一个答案"（墙、错路径、尺寸帽都是 `false`），`recover_with` 点名具体该动的那颗参数或工具（没有就给空串）。识别顺序是前缀 → 子串 → "把 `error` 首词当 code"——新错误也能拿到一个稳定枚举名，不至于静默缺席。无 `error` 但 `content_ok` 为假时按 `page_type` 归类（`auth_wall` / `paywall` / `captcha`→`bot_wall` / `js_shell`，其余落 `no_content`）；被截断单列 `truncated` 并把 `next_offset` 写进 `recover_with`；`js_shell` / `bot_wall` 的 `retryable` 还要看**这台机器**的浏览器档起不起得来（`_never_escalate` / `_browser_cannot_help`）——对一台起不来的浏览器说"再抓一次会升级"是假话。`approx_tokens` 是对 `content` 的**脚本感知**量级估计（CJK/假名/谚文一字≈一 token，其余约 4 字符一 token）——英文用 `chars/4` 尚可，中文按同一把尺会低报约 4 倍，那恰是要预算上下文的人最不该被误导的方向。两条都是**纯增量**：prose（`error` / `next_action`）一个字不删，`diagnosis` 是它的孪生不是替代；干净成功两条都缺席（`None` / `0` 由省略表下线），不额外占字节。钉住 `tests/test_the_envelope_carries_a_machine_readable_diagnosis.py`（14 条）
- **`schema="auto"` 可用，并回一份可复用的提议 schema**：`structured.extract_structured` 的文档一直承诺 `schema="auto"` 触发自动抽取，但 `_normalize_schema` 先把裸串丢给 `json.loads("auto")` —— 直接 `not valid JSON`，只有 `{"type": "auto"}` 能过（实测确认，且这条路径此前**零测试覆盖**）。现在裸串 `"auto"`（大小写与首尾空白不敏感）与 `{"type"/"mode": "auto"}` 等价。auto 模式除了检测 JSON-LD / 表格 / 列表 / OpenGraph，还在页面有重复结构时**提议一份 schema**：找重复 ≥3 次的 `标签.class` 组当容器，取其第一个实例里带文本的后代（按 class 末 token 或标签名命名，泛标签 `div`/`span`/`p` 不作字段）当字段，产出 `_schema` —— 抄回 `schema=` 即可对同站后续页复用，不必再猜选择器（探针：4 条同构容器 → propose → 复用 → 4 条分组记录）。无重复结构时不出 `_schema`（诚实的"这里没有结构"，不是失败）。提议是启发式不是语义理解：容器是重复最多的组、字段名来自 class 末 token，读作"可抄的起点"而非"判词"；边界写进 `sys_guide(topic='behavior', section='Structured extraction')`。钉住 `tests/test_a_page_can_propose_its_own_schema.py`（20 条，含一条发现链守卫：wire 不写 `auto`、只指向手册那一节，那一节就必须真的讲了它）
- **页内样板折叠：同一页里重复的行只留一次（`duplicate_lines_collapsed`）**：一页正文里重复出现的导航 / 页脚 / 订阅条此前原样留在交付的 markdown 里 —— 同一句话在一页里出现 N 次，就是给调用方的上下文预算付了 N 倍的钱。现在抽取链在 trafilatura 之后做一次**有界**的页内重复行折叠：完全相同的行只留第一次出现，其余折掉，折掉多少在信封的 `duplicate_lines_collapsed` 里报数（省略 = 没折）。边界收得很窄，因为误删正文比多留样板更糟：只折**纯段落行**且长度 ≥24 字符 —— 标题、列表项、表格行、代码围栏一律不动（结构性重复是内容不是样板），围栏内的行整段跳过。幂等（折过的文本再折不缩），无重复时逐字节不变，`extracted_type='html'` 的原始标记不受影响。钉住 `tests/test_the_page_boilerplate_is_folded.py`（10 条）
- **共识感知扇出：`web_search` 的自动取正文按跨引擎共识设地板（`fetch_min_consensus`）**：`fetch_content=true` 是这条链上唯一一处 dhole 自己决定"抓哪几页"的扇出，此前按 `fetch_relevance` 取前三 —— 而相关性是**单条结果自己**的分数，一家索引说了算。共识（`engines_consensus`，数的是独立索引家族）是搜索里唯一免费的权威信号：已经在每一行上、不花额外请求、不引模型、确定性。新参数 `fetch_min_consensus=N`（默认 0 = 现状，N≥2）把扇出预算只花在被至少 N 个独立家族共同确认的命中上，被跳过的 URL 进 `consensus_skipped`；每页回执（含失败占位）都带自己的 `engines_consensus`，无论地板开没开。四条边界写死并有独立测试：①地板 1 是**无操作**（每行至少一个家族）→ 拒绝而非静默；②没开 `fetch_content` 时地板没有作用对象 → 拒绝；③池子只回一个家族时地板**不适用**（`1 of 1 (no corroboration)` 是"没人**能**同意"，不是"没人同意"，把降级池的沉默读成反对会在坏天气里丢掉每一行）；④地板清空全部候选时**照实抓 0 页并报数**，不回退到"反正都抓"——调用方显式点了地板，静默绕过它就是替他做了相反的决定。**选可选参数而非改默认行为**：改默认会悄悄换掉每个既有调用抓回的页面，正是本项目最防的那类失败（看起来成功、结果被换了）。实测：merge 模式下闸门结构性不可达（共识加成 0.2/家族远大于标题+URL 上限 0.18，单家族行永远排在多家族之后），它真正咬人的场合是**神经模型判断与共识不一致**时（高相关但单一家族 vs 低相关但多家佐证）——那正是调用方会想覆盖的那一刻，集成测试即复现此形。连接期 30,629 → **30,824**（+195，棘轮第三次上抬；参数名与判据必须在连接期到场，判据细节搬进 `sys_guide(topic='behavior', section='web_search')` 按需付，顶层描述里与它重复的那句按「同一事实不说两遍」删掉）。钉住 `tests/test_consensus_gated_fanout.py`（22 条）

- **搜索结果的行级注入标记（2026-10-07）**：`web_search` 每行的 title＋snippet 过与页级正文同一把注入扫描，命中行携带 `untrusted_content: {hits, total}`，干净行缺席（省略即默认）。此前页级正文有标记而搜索行没有——被注入的标题 / 摘要以干净面貌进转录。钉住 `tests/test_search_rows_flag_injection_shapes_in_title_or_snippet.py`
- **缓存命中读数（2026-10-07）**：`sys_state` 的 `cache` 读数新增本进程的 `hits` / `misses`（找到 / 落空各计一次，重启归零）。缓存该不该加 / 砍什么的判决依据第一次可测，不用再猜。钉住 `tests/test_cache_stats_counts_hits_and_misses.py`
- **doctor 报出连接面孔的指纹（2026-10-07）**：wire cost 行之外新增 `wire face digest`——sha256 覆盖 tools/list 压缩面＋instructions（与计价同一份输入）；`tests/tool_defs_lock.json` 新增 `sha256_wire_face` 存默认面同款指纹。字数相同内容不同的措辞改写从此不再隐形——升级后与版本源码 tag 的锁文件比对即可。钉住 `tests/test_doctor_names_the_digest_of_the_face_it_serves.py`
- **写操作的人身同意走出参数通道（MCP elicitation，2026-10-07）**：`web_request` 的写请求、`web_act` 的 `submit`、CDP attach 的首次授权——宿主声明 elicitation 能力时，服务器经协议直接向**人**要一次明确同意（accept 才执行）；问不到人（宿主不支持 / 用户拒绝 / 询问链路任何失败）一律拒绝执行——门是关的，不是开的，`confirm=true` 不再能替代那次同意（它表达的是"我去问过并得到许可"）。宿主不支持 elicitation 时行为照旧：先问操作员、得到许可再带 `confirm=true` 重发。同一连接内逐连接协商，不跨调用记忆。真实 SDK 回路（内存传输，accept 与 decline 两态）与三个降级分支钉在 `tests/test_write_gate_moves_out_of_band_when_client_elicts.py`（11 条）；宿主是否真支持 elicitation 以 `dhole --doctor` 与一次真实写调用为准——问不到人的宿主上行为与旧版一致
- **大站翻页的断点续传（`plan_id` / `resume_token`，2026-10-07）**：上千个 URL 的站点此前要 agent 自己当传送带——每批把全部 URL 重新发一遍，十批就是七万字符的参数生成。现在 `web_discover` 收尾把 URL 清单存成 `plan_id`（1 小时 TTL，32 份上限），`web_crawl` 新增 `resume_token=` 断点续传＋`selector=` / `schema=` 投影：同一计划同一 offset 永远同一批页同一断点；预算（页数 / 总字符 / `deadline_ms`）在批间检查，到点在信封带回 `next_resume_token`，逐字传回即可从断点接着跑，计划跑尽回 "Plan exhausted"。投影参数没有 `resume_token` 时具名拒绝（一个请求都不发）；失败的页照常随行返回，不烂在服务器里。连接期净 −22（三参成本由 web_crawl 描述重写对冲，棘轮只降）。钉住 `tests/test_crawl_picks_up_a_selector_and_schema.py`（7 条，含 dispatch 级抵达性）

### 变更

- **`cdp_url` 默认拒绝（breaking-adjacent）**：此前 `cdp_url` 是 agent 可自由传的参数——这正是要收口的洞：attach 你的真实 Chrome 等于交出其中**所有**已登录站点的读权限，而外层 agent 的规划循环不可预测。收口的形状经过两次：先是一颗机器级旗 `DHOLE_CDP_ATTACH`（本版随大众化批次注销，设了也不再被读），定稿是**逐主机的一次性授权** —— 第一次 attach 某个主机返回 `cdp_authorization_required`、一个请求都不发，带 `confirm=true` 重跑才放行并把该主机记进 `cdp_hosts.json`（删掉它即撤销）。受影响的是「给 cdp_url 传值但没批准过」的既有调用：以前是静默跑通但登录态为空（`new_context()` 的旧 bug），现在是明确失败
- **连接期的账按颗算**：17.0 收尾时 17 颗工具 + instructions = **40,010 字符**（B5-17 那几刀从 44,016 压下来：删掉的是措辞与重复，搬走的是用法细节（工具描述、参数描述、instructions 三处都动过）—— 落点在 `sys_guide` 的七个 topic 里）。中途那次「23,572 → 22,817」是 13 颗工具时代的数，工具拆细之后总数必然回升，这一条写在这里是为了不让它冒充目标。本版定稿 **33,452 字符 / 13 颗** —— 四颗状态工具未发布即移除，见「移除」；随后 G25 评审的写操作确认门把它抬到 33,460（+361），名册补全又以自身对冲落回 33,452，见「安全」。
**这一版贵在哪里，是按提交量出来的**：v16.2 是 9 颗工具 21,282 字；`B4-1` 把 71 颗参数第一次在 schema 上报名字，
一个提交 +24,375（袋还在，两份并存），`B4-2` 删袋回收 5,714 —— 净持久成本 ≈ +18.6k，全部来自"参数不再靠猜放哪"
这一步；四颗新工具加起来才 +5.6k，`nested_*` 摊平 +1.2k。所以 17.0 比 16.2 贵一倍字符的地方不是拆工具，而是
把参数提到顶层：每颗参数都要带名字、类型和自己的描述，这是乘上去的固定开销，买到的是"打错参数名会被拒绝而不是
静默丢掉"。默认注册面**定稿 13 颗**（这一版不以字数为目标：能力面是产品，预算是给它的约束、不是反过来）；要按
用法裁剪是现成的一行 `DHOLE_TOOLS`，价目表在 README「注意事项」那一条与 `sys_guide(topic='config')`。删的仍是**写给读者而非写给 agent** 的那层——`session_id` 的桥接实现细节（agent 要的是「登录态跨两层可用」这个结论，不是它怎么桥的）、`focus_mode` 里的进程级开关（agent 改不了它，且响应头已声明实际打分器）、`web_crawl` / `web_search` 里与 `options` 包重复的段落。**条件→动作契约、阈值校准数字、路由规则一条没动**：那些是 agent 做决策的依据，删了省的是字符、赔的是选错工具多花的调用。（`options` 袋在 B4 拆平，今天没有袋可写。）本版其余改动（17 颗拆细、`nested_*` 三颗、逐档 anti_detect）把总数推到 44,016，B5-17 几刀压到 40,010（其中 instructions 从 3,532 到 3,070）—— 与这一条开头的数是同一笔账，中间那些 22,968 / 23,572 都是 13 颗时代的截面，写在这里只是为了不让截面冒充目标
- **单颗工具的形状上限改成棘轮（27 参数 / 296 字符）**：`web_read` 收口后仍是 **27** 颗参数、`sys_guide.topic` 的描述 **296** 字符 —— 与作废 ≤26,000 连接预算是同一句裁决（"字符多可以，最重要的是能力"）。守卫**不删**，改成钉在实测最大值上、只准往下：`tests/test_wire_shape.py` 的两条棘轮管单颗工具的形状（一颗自己长胖、总账却被别处收缩抵消时，只有它看得见），`tests/test_wire_moves_land_in_the_manual.py` 的 `CONNECT_CEILING` 管整条连接期的总账（移除四颗时 33,099，后随 web_request 写门上抬到 33,460，见「安全」——文件归属也在这里更正：这一版早期的条目把它写在 `test_wire_compaction.py` 名下，而那个文件从来没有过这个常量）
- **随包 skill 手册同步到 17 颗工具**：`SKILL.md` 与 `references/` 此前只写九个默认工具，且「登录」一行断言密码「只能走 CDP attach 或用户自己的浏览器」——`vault` / `login` 落地后这句已经是错的。现在手册补齐那四颗工具：它们在本版定稿时**默认注册**（曾经守着她们的 `DHOLE_TASKS` / `DHOLE_VAULT` 两颗旗随大众化批次注销，设了也不再被读）、`cred_vault` 只回元数据（`VaultModel` 没有能装密码的字段）、`cred_login` 的审批闸与三态 `logged_in`、`web_watch` 首跑 `changed=null` 不等于「无变化」、`web_recipe` 重放 `actions_failed` 非零即页面已变，都进了「选工具 / 深行为 / 配置 / 样例 / 字段 / 排查」六卷；新增硬约束 13 明确「凭据值不进 transcript」。升级后重跑 `dhole skill install --force` 生效

- **`pip install dhole-mcp` 默认即完整版**：`[all]` 的 12 项重依赖并入默认依赖（`[all]` 保留、`slim` 只是个名字），"看起来装好了其实只有 HTTP 层"到此结束
- **`DHOLE_IGNORE_ROBOTS` 的未识别值改为视为 OFF 并记录**：唯一一个"typo 朝危险方向失败"的开关被收口，其余 flag 语义一字未动
- **常驻工具说明瘦成「路由 + 陷阱 + 键名清单」**：逐键解释移进 `sys_guide` 的语料，20 个原本没被点名的 `options` 键（那颗袋已在 B4 拆平成顶层参数）全部点名；那一步当时是 10 颗工具、连接期 22,948 → **24,030 字符**。工具从 10 颗拆到 17 颗之后这个数必然回升，今天的账见上面「连接期的账按颗算」那一条（40,010）
- **17 处 `os.environ` 直读改走 `env_registry.value_of()`**：文件门对全部旋钮成立，而不是只对一半
- **重排模型收敛成一颗，不再给用户选（B8）**：此前注册三颗（`bge-zh` 默认 / `zh-full` / `ms-marco`），靠 `~/.dhole/config/reranker.json` 切换。换型评测（集 B，C-MTEB/T2Reranking 300 题，配对 bootstrap 95% CI）量出候选 `gte-multilingual-base` 与 `bge-zh` 在 nDCG 上**分差不明显**（Δ+0.0030，CI 跨 0），而 CPU 上慢 2.3× —— 换不到质量还倒贴时间，于是收口成「只留一个语义模型，不给用户选」。另两颗与 `~/.dhole/config/reranker.json` 一并下线，`paths.rename_legacy_model_dirs`（14.4 的模型目录改名）随之删除 —— 它搬去的目的地名已不再是模型键，留着就是一条搬向空处的迁移。**不静默换打分器**：16.x 上选过模型的机器升上来，首次取模型时日志会说明「你选的那个名字不再起作用、现在只用 `bge-zh`」。原始数据见 `evals/README.md`。
- **单次可点名的引擎上限从写死的 9 改成从注册表推导（=10）**：`_validate_engines` 的 `len(engines) > 9` 是注册表还有 16 个名字时留下的数；池子缩到 8 免密 + 2 keyed 之后，9 就成了一个没有理由的排他（可选集合本身是 10，却永远点不满）。现在上限 = `len(DEFAULT_ENGINES) + len(KEYED_ENGINES)`，注册表增减时跟着走；`web_search.engines` 描述、`SKILL.md`、`tools.md` 里的「max 9」同步到 10。描述文案是静态字符串、抄不了这个常量，由 `tests/test_tool_descriptions.py` 钉住
- **工具默认超时对齐宿主安全线（2026-10-06 实测）**：`web_act` 与带 `actions` 的 `web_read` 默认 60000→**25000ms**，`web_crawl` 的 `deadline_ms` 默认 120000→**25000ms**。判据：宿主普遍在 30s 处把整次调用砍成裸的 "Tool execution timed out"，什么都不回 —— 内部预算比宿主长的调用注定什么都回不来（2026-10-05 那次全真模拟的首调 web_act 正是这么死的）。现在内部超时先到，返回的是结构化超时并点名可抬的旋钮；宽裕宿主的大爬取与慢页面从此要显式抬高 `deadline_ms` / `timeout` —— 这是行为的改变，不只是收紧：默认值现在保证「有回声」。描述与手册（SKILL.md 边界清单、tools.md 爬取经济、configuration.md 价目表）同步改口；连接面 30,804 → **30,968**（+164），`tests/tool_defs_lock.json` 重刷，`CONNECT_CEILING` 随实测上抬（理由写在测试注释里）。钉住 `tests/test_bug_report2_regressions.py`（默认值断言改为 host-safe 口径）
- **随包技能语料只剩 agent 需要的话（2026-10-06）**：dhole-web 手册写给 agent，行文里却混着变更日志才需要的东西——"since 17.0 / 17.0 removed X / §3 / decision 31"这类版本与决策编号、"observed live 2026-10-05"这类实测日期、"整轮预算因何被删又为何回来（当时实测 223,599 字符）"这类决策叙事。它们不改变任何一次调用该怎么做，还占每次安装的 token。七份语料（SKILL.md + references/ 六卷）统一改写为只谈方向与现状：8 处日期、70+ 处版本/决策令牌全部剥除，历史叙事压成仍然可操作的规则句（例：整段预算战史压成"花完即停，`truncation=total_chars` 报数"）；`examples.md` 里 JSON 示例载荷中的日期是取值格式演示，保留。新守卫 `tests/test_the_corpus_carries_no_maintainer_history.py` 钉住：正文再出现版本号、ISO 日期、决策编号、批次代号或 § 引用，测试点名文件与行号

- **连接期去重：同一事实说两遍的地方删一遍（-339 字符）**：连接期文本里唯一能**无损**回收的量，是"同一件事在两个总是一起送到的位置各写了一遍"。三处：①放置规则（"参数住在顶层、没有嵌套"）仓库自己的决定就是**只在连接期说一份**（见 `server.py` 里 `_INSTRUCTIONS_RULES` 上方的注释），却仍在 `web_read` / `web_crawl` 的描述里各留了一份副本——删掉；②`web_request` 描述里的 "stored credentials for the host ride along automatically" 与它自己的 `confirm` 参数、`cred_defaults` 那两句重复（凭据自动随行在连接期已说、且由每次回执的 `identity_applied` 确认），删描述侧（`confirm` 参数本身仍自洽）；③`DHOLE_INSTRUCTIONS` 的 `sys_guide` 路由句与工具描述重复（四个触发词都在 `sys_guide` 自己的描述里），收短。**`web_request` 路由句里的方法清单没有删**：它同样在工具描述里，但"路由的家（INSTRUCTIONS）必须点名写方法"是一条被 `tests/test_web_request_b5.py` 钉住的契约——37 字不值一条契约（这一条是全量回归抓出来的，先前的守卫子集没跑到它）。**跨工具的 `timeout` / `ignore_robots` / `check_login` 重复不动**：每颗工具的 schema 必须自洽，一个只挂单颗工具的客户端仍要读懂，删它是有损的（上一版把这笔账记在"连接期重复"名下，而其中大部分是承重的）。实测 30,968 → **30,629**，工具颗数不变（11）；`tests/tool_defs_lock.json` 重刷、`CONNECT_CEILING` 随实测下调（棘轮只准往下，这一刀是真往下：删掉的是副本，不是信息）。

- **`web_crawl` 的默认预算在入口对齐承诺（2026-10-07）**：wire 描述、手册、账本三处承诺的都是 25,000ms、库层早已改到，唯独入口签名停在 120,000——按 wire 传参使用时库默认被原样盖住，30 秒就砍调用的宿主上 `web_crawl` 曾必然整次颗粒无收。入口改 25,000，`web_discover` 经同一入口继承；AST 互证守卫钉住「默认 N == 签名默认 == 库默认」三处同源
- **`web_act` 的 `destructiveHint` 回到 spec 默认（2026-10-07）**：显式 `False` 对宿主的 hints 消费者是主动错报——submit 门自己承认这一步不可逆，「only additive updates」不是真话。连接期 31,050 → 31,026（实测 −24；评审按源码字面量预估 −26，compact 序列化口径不同，以尺为准）
- **`web_discover` 有了连接期路由行（2026-10-07）**：11 颗里唯一「注册了却没有路」的工具补上——map / crawl / read 三件事共享一句（两把键同文，全量只发一次，任一只挂其一的子集也各有路）；`web_read` 行删 `pages=` 半句（schema 里已说，不丢信息）。31,026 → 31,016。守卫：每颗注册工具必须有路，反向不许有幽灵路
- **wire 再省 226 字符：枚举与 how-to 撤到已承重的手册（2026-10-07）**：`web_act` 的 dict-form 旋钮清单（decode_qr / full_page / save_to / image_type / quality）与 `doc_parse` 的 encoding 恢复 how-to 各换成 `sys_guide` 指针——手册侧逐项承重、新守卫逐名钉住；VISUAL ASSIST 首句与 charset 契约句留在 wire。31,016 → **30,790**。`web_crawl` 的 Caps 句未动：它与「三阶段确定性批量」的回收是同一句，只计一次
- **注释里的实测数要么带日期、要么 import 派生（2026-10-07）**：遗留的陈旧截面（13 颗时代的 instructions 数、「146 颗参数」、不可复现的子集数）清零或改日期戳＋复测命令；单工具形状守卫改为必须等于当日真实最大值；带日期的账本史行不删（删了才是篡改）
- **评测尺子改版（2026-10-07）**：tag-pages 的扇出断言接受等价形状（nested 三件套或贴上限 `urls=` 批量 len≥12 任一）——上一轮三档全红量出的是「哪条路才算对」上尺子与语料打架；三条 few_calls 探针降为记录项（照印不进分母，先探针一发再扇出是合理保守），分母 **18＋记录 3**，与旧读数不可同表读；新增两格只 references 能答的用例（mojibake-pivot / diagnosis-branch，跑前需操作员替换真实 URL）
- **评测尺子补丁：`require_success`（2026-10-07）**：`any_arg` 类断言原本只看「参数出现在某发调用上」不看成败——mojibake-pivot 的「转向」分支因此给过一发被拒的调用记绿（`web_read` 没有 `encoding=`，A 档试发 `encoding=gbk` 被 `invalid_request` 拒掉，该格仍记满）。断言新增 `require_success` 限定：转向分支只认那发调用真正成功的情况；`encoding=` 今天只在 `doc_parse`（且只收本地路径）上，转向判分的实质由「拒绝引用乱码」承担，分支保留是为杠杆哪天到场时尺子已经就位。第四轮证据回放：三档读数不变（原先的绿走的本来就是诚实分支），封死的只是假绿那条路
- **评测语料消歧与断言降级（2026-10-07）**：budget-said-out-loud 的 prompt「首页上所有标签的名言」同一模型三档读法分裂（A 读成遍历全部标签页并满分，B/C 只读首页自己 1/3），改写为「首页每个标签都链到它自己的标签页；把每个标签页上的名言都收齐，一条都不要漏」——只消歧读法，验收三句（nested_max 顶格、truncation 说出口）不变；shell-is-not-an-answer 的 tried_nested 降为记录项——prompt 预设的「推荐专题」栏目当日已不存在（三档多层核实一致），预设存在的断言量的是站点改版不是 agent，诚实三断言（no_fake_absence / tells_the_user_the_truth / escalated_tier）成为判分主体，换上有真实栏目的站时升回。降级后分母 **17＋记录 4**，旧读数（/18）不可同表读；新 prompt 的验收读数留下一轮真实重跑
- **三档校准第四轮读数（2026-10-07）**：A 17/18（整格绿 6/7）、B 15/18（5/7）、C 15/18（5/7）——tag-pages 三档全绿（本轮全部走 nested 扇出并顶格 nested_max，回执的 truncation 教会了补齐路径）、author-per-quote 三档满分；两新格 B 与 C 打平在满分处：SKILL.md 的排障表已把要的杠杆教到位，references 无可量的增量。两新格的「真实页面」当日两证不可得（真 GBK 页 charset 探测能解对，真反爬墙在抓取阶梯下不成立），可复现的失败形状由回环夹具供给，评测 CLI 因此新增 `--allow-private`（仅进程内生效，与测试夹具同一开关）。本轮顺带量出两个记录在案待议的缺陷：mojibake-pivot 的「转向」分支会奖励被拒的调用（`web_read` 没有 `encoding=`，any_arg 只看参数名不看成败）；浏览器超时信封的机器可读 `diagnosis` 是泛化句，点名 `force_fetcher='http'` 的专项建议只活在 `next_action` 里

### 移除

- **四颗状态工具 `web_recipe` / `web_watch` / `cred_vault` / `cred_login` 未发布即移除**：三件事各有更省的位置 —— 重复任务写一个 skill 就行（比一颗要维护状态库的工具更贴调用方的实际做法），凭据与登录流可以直接把账号密码交给能操作浏览器的 agent（CDP attach 与 `cred_defaults` 已经覆盖「用你自己那个已登录的浏览器」这条路），于是这四颗的**占用与使用率不成比例**：它们在连接期合计 **6,911 字符**（40,010 → 33,099；其中四颗工具自己的描述占 6,385 —— `web_recipe` 1,817 · `cred_login` 1,798 · `web_watch` 1,561 · `cred_vault` 1,209 —— 其余是 `_INSTRUCTIONS_ROUTING` 那四行与 `sys_state` 描述里点过的类名），是 40,010 的 **17.3%**，而每次连接都要付一遍。移除后默认注册面 **13 颗**、连接期 **33,099 字符**（instructions 2,636 + tools/list 30,463），`CONNECT_CEILING` 随之下调到 33,099（棘轮只准往下；后随 web_request 写门上抬到 33,460，见「安全」）。实现随之删净：`tasks.py` / `vault.py` / `login.py` 三个模块、`tasks.db` 这个库（`sys_state` 的 `watch` / `recipes` 两个清除类一起下线）、四颗工具的模型与 wire 省略项、`_INSTRUCTIONS_ROUTING` 里的四行。**`_REVALIDATE` 通道与 `304` 语义保留**：复验机制本身属于抓取内核（`_conditional_headers` 仍在、测试仍直接驱动它），只是 17.0 起没有公开触发点 —— 删它要动抓取内核，收益为零。语料（`SKILL.md` 与六卷 `references/`）同步删掉这四颗的段落，`sys_state` 那一卷改成四类状态
- **`dhole init` 整组删掉**（连带 `dhole model` / `dhole proxy` 与 `--probe`）：它存在的理由是替人写配置文件，而 17.0 之后写配置只有一条路 —— `dhole config set`（按次读，下一通调用就生效）与 `dhole doctor`（体检 + 配置审计）。留两扇门就会有两份会各自漂走的默认值，而 `init` 那扇门还额外要维护一份"哪些旗该开"的清单
- **能力开关 `DHOLE_MULTIMODAL` / `DHOLE_NEURAL_FOCUS` / `DHOLE_TASKS` / `DHOLE_VAULT` / `DHOLE_BROWSER_PROFILE` / `DHOLE_COOKIE_TTL_S` / `DHOLE_WIRE_FULL` 注销**：设了不报错，也不再被读。今天读的是 `DHOLE_TOOLS`（注册面）、`DHOLE_ALLOW_PRIVATE_HOSTS`（私网范围）、`DHOLE_SEARCH_PROXY` / `DHOLE_*_API_KEY`（出口与 keyed 引擎）、`DHOLE_USAGE_LOG`、`DHOLE_IGNORE_ROBOTS`、`DHOLE_HOME`、`DHOLE_DEFAULT_CONTENT_CHARS`、`DHOLE_DEFAULT_ENGINES`、`DHOLE_HF_ENDPOINT`。一份会说谎的安装说明比一份短的更糟：它读起来完全正常
- **`[slim]` extra 删除**：extras 只能往依赖里加、不能减，而重依赖（浏览器 / PDF / OCR / 重排）早已并入默认集，`pip install dhole-mcp[slim]` 解析出的依赖与裸装一字不差——一个承诺了 pip 做不到的事的空名字，却一直占着包元数据与安装文档。没有 Playwright wheel 的平台（Termux / aarch64）的诚实路径从来都是另一条：`pip install --no-deps dhole-mcp` 再手挑运行核心（README 安装一节已照此改写，并补上 `dhole skill install` 与开发安装两个此前漏写的入口）

### 修复

- **浏览器超时的机器可读 `diagnosis` 与专项建议各说各话（2026-10-07）**：HTTP 层已答出 200 壳页、升级后的浏览器渲染挂到预算烧尽时，`next_action` 里那句专项建议是对的（"是浏览器渲染超时，不是页面慢 —— `force_fetcher='http'` 直接拿 HTTP 层已得的答案"），但信封的 `diagnosis` 在 finalize 时被前缀表按 `timeout` 前缀重新归一成泛化句 "raise timeout, or switch source"——机器可读那份给的建议恰是专项 prose 要拦的动作（HTTP 层已经答了，再抬只是等一个到不了的渲染）。修法：`_over_budget_result` 直接携带结构化孪生（`retryable=false`，`recover_with` 点名杠杆），finalize 的前缀表归一降级为「没有现成值时的兜底」；prose 一字不删。三档校准第四轮在回环夹具上量出的 wire 侧发现，钉住 `tests/test_the_browser_timeout_twin_names_the_lever.py`
- **`actions` 在两条车道上被静默丢弃**：`cdp_url` 与 `actions` 同传时动作从来不会执行（attach 车道与隐身车道各建各的动作对象，attach 那份没接上）；`schema` 与 `actions` 同传同样被静默忽略。前者修为两条车道都执行动作，后者修为显式报错——静默忽略一个调用方声明的参数比拒绝更糟
- **截图动作拿不到图片像素（元素级截图 / `decode_qr` 的主路径失效）**：带 `actions` 的抓取在隐身层硬编码了 `disable_resources=True`（图 / 样式 / 字体全 abort 的提速开关），于是动作链里 `{"screenshot": {...}}` 截到的是**破图占位符**——页面自己的 `<img>` 连请求都没发出过，`decode_qr` 回 `[]`，而动作行照报 `ok: true`：调用方看到的是「这页没有二维码」，不是「我没拿到像素」。修法是截图动作让资源拦截让路（它的产品就是渲染结果，砍掉图与样式本身就是错的），其余动作链照旧提速。A/B 实测单变量确认因果：`disable_resources=True` 时浏览器发出 `GET /qr.png` 0 次，`False` 时 1 次
- **索引寻址的动作在收执与审计里丢掉目标**：`click` / `submit` 按快照索引寻址（`{"index": N}`）时，动作标签渲染成 `click None` / `submit None (unconfirmed)`——`_act_target` 把索引归一化成不带 `selector` 键的形状，而标签仍按 `selector` 取值。难看是小，泄漏到**写操作审计**是大：`DHOLE_USAGE_LOG` 的 `channel=browser_submit` 行里，`action` 是唯一说明「提交了哪个元素」的字段，它当时写的是 `None`。修法是复用已有的 `_target_label`（它本来就同时认索引与 `expect`）
- **调用日志把「被拒」记成「成功」**：`usage.jsonl` 的 `ok` 实际含义是「没抛异常」，而工具失败走的是正常 envelope——一次被 SSRF 守卫拒掉的 `feed_read` 在日志里是 `{"tool": "feed_read", "ok": true, "ms": 22.0}`，拒绝对读者完全不可见。这个日志存在的理由恰恰是回答「我的客户端到底有没有正确路由到这里、扛不扛得住」，而「看起来成功」是它唯一答不了的形状。修法是补记应用层错误：`ok` 保持原义（批量里一个 URL 失败仍是一次跑完的调用），工具自己判为失败的内容进 `error` 字段，`select(.error)` 一网打尽
- **私网健康探针把「内容判定」读成「目标不健康」，于是本机登录页的浏览器层被跳过**：强制隐身抓取一个回环 / 私网目标前，会先做一次直连健康探测（3 秒、不重试）以避开「死端口烧满浏览器预算」。判定条件是 `probe.error or status >= 500`，而 `ResponseModel.error` 同时承载**内容判定**（`auth_wall_detected` / `bot_wall_detected` / `js_shell_detected`）与传输失败——于是一个 HTTP 200、正文是完整登录表单、服务明明在跑的本机登录页被判成「target-unhealthy」，浏览器层被跳过，HTTP 层的结果直接成了答案：一个 `{"snapshot": true}` 动作**从来没跑过**，而调用方收到的说法是「这个页面上没有密码框」。代码旁边的注释本来就写着「2xx / 3xx / 4xx ⇒ 活着」，实现却多读了一个字段——修法是把健康判定收回传输维度（`status == 0 or status >= 500`），与它自己的注释对齐。实测（靶站 `/login`）：修复前 `escalation_path=private-probe:target-unhealthy(skipped_stealthy)`、`metadata.actions` 为 null；修复后正常进隐身层并列出三个元素。这条是 `evals/vault_login.py` 的端到端场景挖出来的——单元测试全绿，因为没有任何用例覆盖这个探针
- **`fill` 动作失败时把明文写进日志**：`actions.py` 的动作执行器在失败分支用 `logger.warning("page action %a failed: %s", a, ...)` 打印**整个动作字典**，而 `fill` 动作的字典里带着 `text`。响应收执、`metadata.actions`、`DHOLE_USAGE_LOG` 审计行都只记动作**目标**（`fill [1]`），唯独这条谁都不审的通道在泄漏——而登录流构造的正是带密码的 `fill`。修法是改记 `action_label(a)`（只报目标），与所有其它消费者一致
- **批量 `web_read` 完全不可用**：`urls=[...]` 的**每一种**形式（带 `options`、单元素、不带 `options`、走 `options` 口袋）都在返回前抛 `AttributeError: '_action_images'`——那是个只定义在单条结果模型上的私有属性，批量结果里**不存在**而不是空。客户端拿到的因此是崩溃的原始异常，而不是任何诊断。修法是守卫式读取（与同文件既有的 recipe 分支同一形状）；单条路径的元素截图不受影响，批量路径也没有丢图——批量本来就拒绝 `actions`，而元素截图的唯一写入点只跑单条路径
- **OCR 抢救成功的 PDF 被报成 `js_shell`（信任开关反了）**：一份扫描件被 OCR 读出文字、`quality_score` 0.9 之后，响应却是 `error=js_shell_detected` / `content_ok=false`，`next_action` 还让人「用浏览器渲染一遍再看」——而那正是它刚刚成功做完的事。「大 body、少文字」这条启发式描述的是 HTML 外壳，PDF 是容器，被 OCR 救回来的扫描件**按构造**就是少文字（一页扫描件约 100 字符对约 10 KB 文件）。判据现在在入口就排除 PDF：`_detect_content_issue` 只守住了它的零文字分支，而同一个谓词还回答「要不要升级到浏览器层」，所以守卫放进谓词本身——一处覆盖两个判断
- **本地扫描件的 `doc_parse` 不做 OCR 恢复**：同一份字节，走 URL（`web_read`）会自动 OCR 成功，走本地磁盘（`doc_parse`）却回「去装 `pip install dhole-mcp[all]`」——而 `[all]` 早就装好了。恢复逻辑原本内联在 `server.py` 的 PDF 分支里，只有 URL 路径跑得到；现在收进 `pdf_extractor.recover_scanned_pdf`，两条路径共用（`doc_parse` 的文档串本来就承诺「本地文件与抓回来的行为一致」，对这个分支它当时不成立）。顺带修掉一个只有本地路径有的毛病：**每个带标题的 PDF 标题都被打印两遍**（抽取器头部写一次、`parse` 又前置一次），而同一份字节走 HTTP 只写一遍
- **`scanned_pdf` 提示硬编码，误导已装 `[all]` 的机器**：扫描件提示一律是「装 `pip install dhole-mcp[all]` 就能自动 OCR」，在已经装了 OCR 的机器上这是事实错误的指引——照做只是重装一遍。提示改由 `pdf_extractor.ocr_advice()` 按本机实际能力给：装了就说「OCR 在这里没读出文字，用视觉工具或换个来源」，没装才给安装命令。四处 OCR 提示（全扫描死胡同、两个逐页标记、`web_read` 的 `next_action`）从此同源，不会对同一台机器给出互相矛盾的答案
- **零匹配选择器白等 30 秒，并把「元素不在 DOM 里」误诊为「页面没渲染完」**：元素级截图对零匹配的 locator 会等满 `timeout`（默认 30 秒），然后错误映射把超时一律读成「页面没渲染完」——把人引向调大 `timeout` 与加 `wait_selector`，而这两个补救对「选择器根本没匹配到」都无效。现在截图前先查计数，零匹配立即失败并直说「选择器在已加载的页面上没匹配到元素」，给出真正有用的方向（对照真实 DOM 检查选择器，或先整页截图看看）。实测（本地靶站）：修复前与真超时无法区分，修复后 3.6 秒返回，与一次**成功**裁切（2.9 秒）同量级——等待本身消失了，只剩页面加载时间
- **`web_discover`（这颗落地时的旧名叫 `discover_only`）+ `sitemap` 的三处「没人说」**（P2，报告为「无截断上限」，实测上限一直都在：floor 1000、顶 5000，349 条没被截断只是因为 349 < 1000；真正的缺口是下面三处）：① **没触顶的那条分支一个字都不说**，于是「mapped 349 URL(s)」读起来就是「这就是整站」——而 sitemap 是站点**选择发布**的索引，略掉整节是常事，现在这条分支会说清「这是 sitemap 的清单，不是它列全了的证明」；② 走 `<sitemapindex>` 时的**文件数 / 层数上限**（25 个文件 / 3 层）独立于 URL 上限，撞上它 `capped` 仍是 `False`、计数也看不出来——站点把 1,200 页拆进 30 个小 sitemap，这里映射出 1,000 条，剩下 200 条无声消失；现在这条也如实报（且**不**点名任何旋钮：`max_pages` 动不了文件读取上限，指一个点了没反应的旋钮比不指更糟）；③ `path_include` / `path_exclude` 过去在**截断之后**才过滤，于是站点把目标子树排在 cap 之后时一条都进不来——而响应会把它报成「**no sitemap.xml found**（robots.txt 没有 Sitemap 指令、/sitemap.xml 什么也没返回）」，sitemap 明明刚读完。现在过滤在 cap **之前**生效（`discover_sitemap` 新增 `accept` 谓词，`max_pages` 换来的额度花在调用方要的页面上），抓到了但过滤后为空的地图也不再谎报「没找到」，而是回「读到了 N 条、没有一条匹配你的 scope」并给出收窄建议。响应新增 `sitemap_truncated`（只在为真时上线，小 sitemap 的 wire 形状不变）
- **`web_act` 的截图像素在 dispatch 处被丢掉（回执说「有图」，客户端什么都看不到）**：`web_act` 把动作链委托给 `web_read` 执行，`_report_action_outcomes` 在那里把 base64 从回执挪进 `_action_images`（PrivateAttr，不进 JSON），像素再往前一步变成 ImageContent 是 dispatch 层的活——而那条线只接在 `web_read` 分支上，`web_act` 分支的 `_wire_result(result)` 少了 `images=` 一参。于是回执行照报 `image: true`、`decode_qr` 的结果也在，唯独像素本身在最后一公里消失：元素级截图与 `{"screenshot": {"full_page": true}}` 全部如此，`save_to` 落盘是唯一能拿到字节的形状。单元层一直绿，因为既有用例只看回执形状；这条管子住在 dispatch，真实协议层才测得到——2026-10-05 那次真实使用全真模拟第一次过 wire 时抓到。修法是补上与 `web_read` 分支同一形状的 `images=`，`tests/test_web_act_b5.py::TestScreenshotPixelsReachTheWire` 钉住（mock 带图返回，断言 ImageContent 真到 content）
- **爬链路不认存量 `allow_private`：四扇门开了，爬的门还是死的**：`cred_defaults` 存了 `allow_private` 后，`web_read` / `web_act` / `web_request` / `feed_read` 都放行回环，唯独 `web_crawl` / `web_discover` 仍拒——`crawl.py` 的种子 `validate_url` 在任何作用域之外裸跑。每页的真实抓取走 `web_read`（它的装饰器自己推存量放行），所以只有第一颗种子撞墙，而「爬我自己的 dev 站」恰是私网放行最常见的那一步。这是 `web_screenshot` / `feed_read` 当年同类修复（见「新增」的 allow_private 覆盖条）漏掉的第三颗——同一产品决定在一个工具上读「回环可取」、在另一个上读「回环拒之门外」的形状，本仓已经裁决过一次不该存在。修法同 `feed_read`：`allow_private_scope(identity.get("allow_private"))` 包住种子校验；`tests/test_crawl_honors_stored_allow_private.py` 两条真 socket 用例钉住（没放行 → 种子被拒一页不发；放行 → 真爬到 `/work/N`）
- **缓存指纹不含存量身份：先匿名抓、再存身份，同 URL 会命中设身份之前的缓存**：`_cache_context` 从 G7/G25 时代就消费 cookies / auth / proxy（判据是"它们改变回来的是什么"），但 §3-17 把 auth / cookies 从调用参数搬进 `cred_defaults` 之后，指纹的输入源断在了旧地址上——构建指纹的请求上下文包装器只读调用参数，而拆平后的调用参数里早就没有这三个键了。后果：先匿名抓一个 URL（进缓存，默认 1h）→ `cred_defaults` 存下该主机的 Basic Auth → 再读同 URL，**命中设身份之前的匿名缓存**，响应头里没有 Authorization，而 `identity_applied` 却声称这次身份在生效——调用方拿"带着你的凭据"的承诺，收的是匿名那份内容。修法是把管子接回去：web_read 的请求上下文包装器在构建指纹前，把**存量** auth / cookies（按这次请求的 host 取，与 `web_read` 自己用 `_identity_for` 的判定同一个）与 proxy（全局键，出口可能改变内容）并回指纹输入；`cache_ttl=0` 的对照行为不变。语义上这是"身份的每个取值各占一个缓存槽"：存了身份必须重取，unset 后匿名的旧槽仍然有效（它本来就是那次匿名抓取的正确答案）——与 17.0 之前逐次传 cookies/auth 的指纹行为完全对称。回归测试 `tests/test_cache_fingerprint_includes_stored_identity.py` 五条行为判据（数 `_force_fetch` 真调了几次，而不是比指纹字符串）

- **配置不再静默回落**：非法值（`DHOLE_DEFAULT_CONTENT_CHARS=abc` 这类）进 doctor 的配置审计，"我设了但没生效"第一次成为可见输出
- **README 两处与事实对齐**：「零配置可用」补上 fake-ip TUN 这个例外（现在已自动适配），连接期字符数重新实测（原数字比实际高 233）
- **CDP 的说明改口**：不再要求"编辑宿主 env 块 + 重启"。曾经那扇 env 门叫 `DHOLE_CDP_HOSTS`（**精确主机名**匹配，`example.com` 不覆盖 `www.example.com`），本版注销后按主机走一次性的明面授权：`confirm=true` 批准一次，记进 `cdp_hosts.json`，删掉那行即撤销 —— 主机粒度与「不覆盖子域」这条语义没变，变的是它不再需要重启、也不需要编辑宿主配置
- **测试不再往真实 `~/.dhole/config.json` 写**：它成为第七个被 fixture 接管的状态路径
- **README 的引擎名册停在 16.x**：正文一直写着「免密 14 个：默认池 6 个 …… opt-in 8 个 ……」，把 `duckduckgo` / `so360` 归进 opt-in，还点名了 6 家 17.0 已删的引擎（`yahoo` / `baidu_baike` / `sogou_weixin` / `wikipedia` / `grokipedia` / `mwmbl`）—— 照它点名会收到 `invalid_request`。真实形状是 8 免密全在默认池、没有 opt-in 层，正文已改写。同一处陈旧说明也在 `So360` 类 docstring 里（它自己上面一节标题写着「默认池」，正文却写「opt-in，不进默认池」），一并改掉。旧守卫只查「名字在 README 出现过」，这 16 个名字齐全它就绿了 —— 新增 `tests/test_engine_registry.py::test_readme_engine_roster_matches_the_registry` 钉住分池归属与「不许再出现退役引擎」
- **浏览器层在宿主编掉 env 的机器上整层不可用（2026-10-05 真实使用实测）**：调用方每一次 `web_act`、每一次 `force_fetcher='stealthy'` 收到的都是 `browser_binary_missing: chromium install failed (exit 0) - chromium-1228 is still missing`，而盘上**就有** `chromium-1228`（`INSTALLATION_COMPLETE` 与 `chrome-win64\chrome.exe` 齐全，两颗 headless shell 同样完整）。根因不是装不上，是一处路径解析与 driver 不同式：`browser._browsers_root()` 的 win32 分支返回 `Path(os.environ.get("LOCALAPPDATA","")) / "ms-playwright"`，`LOCALAPPDATA` 缺席时那是一个**相对**路径，`Path.is_dir()` 于是按进程 CWD 解析；driver 自己做对了（`LOCALAPPDATA || AppData/Local`），所以安装子进程把 193MB 写进正确的绝对目录并 **exit 0**，而 dhole 的安装后复查调用同一个坏函数，仍然说没有 —— 于是那句自相矛盾的话，以及 `~/.dhole/browser_install.json` 里一条 `{"status":"failed","error":"exit 0"}`。被谎报的第二句是 `dhole -v` 的「browser tier ✓ ready」，那是两个 import 检查而不是「起得来」；第三句是检测本身：`_detect_chrome_channel()` 只探三个环境变量、不读注册表也不查 PATH，全落空时返回字面 `chromium`，于是装着 Chrome 的机器改走下载路线。三千条用例全绿是因为**每一条 `_browsers_root` 用例都先把 `PLAYWRIGHT_BROWSERS_PATH` 指到临时目录**，「没指」那一路从来没被走过。修法四刀：①`_browsers_root()` 与 driver 同式且**结构性地永不返回相对路径**（`_win_local_appdata()` 以 `Path.home()/AppData/Local` 兜底；`PLAYWRIGHT_BROWSERS_PATH="0"` 按上游约定指包内 `.local-browsers`；driver 0 引用的 `PATCHRIGHT_BROWSERS_PATH` 删掉；取不到 home 时抛具名错误，两个调用方不可能再看见 CWD 相对值）②系统浏览器检测改读注册表 `App Paths` → 三根目录 → PATH，env 被剥光也认得 Chrome/Edge —— 认出来就一个字节都不下载，这一刀单独就让这次事故不可能发生，顺带恢复真实 TLS 指纹；系统浏览器的 UA 不再靠起 PowerShell（先纯文件系统取同级版本目录，PowerShell 只作兜底，失败时不再静默丢掉 UA 覆盖）③`chromium_status()` 说出它搜过哪个绝对目录，`reason` 五态（`system_browser` / `bundled_present` / `bundled_absent` / `manifest_unreadable` / `root_unresolvable`）取代一个布尔，安装后复查认出「装在别处」并把两个路径都印出来（`present` 照旧为假）；盘上有但起不来是新的一态，由启动热路径按窄表记录、60 秒节流、绝不抛④失败退避 `INSTALL_BACKOFF_S=900`：浏览器层坏着时不再每次重跑 193MB 安装（判定留在进程内 —— 跨进程抑制会把「刚在别的终端手动装好」的用户晾在原地）。`dhole --doctor` 第 3 项打印搜过的目录与盘上判词，新增 3b `chromium launch` 真起一次（套件里如实说「被挂具跳过」，探针永不触发安装器），`-v` 仍然一个浏览器都不启动。安全口径照实写：渲染沙箱按 driver 默认就是关的（`--no-sandbox` 一直由 driver 传，dhole 此前从没说出来），现在写成显式一行并交代清楚 —— dhole 真正的围栏是 SSRF 路由闸与逐主机 CDP 授权。实测：三相关节 stdio（完整 env / 剥掉 `LOCALAPPDATA`+`PROGRAMFILES` / 再补一颗绝对路径）改前第二臂整层不可用，改后 present 为真且 `web_act({"snapshot": true})` 真拿到元素清单。钉住 `tests/test_a_stripped_environment_still_finds_the_browser.py`（21 条，含「`chdir(A)` 与 `chdir(B)` 下 `chromium_status()["present"]` 必须一致」这条事故的机械复现）、`tests/test_a_browser_failure_names_the_directory_it_searched.py`（30 条，退避窗口内数 `subprocess.run` 次数 == 1）、`tests/test_the_version_panel_does_not_claim_a_browser_it_cannot_launch.py`（21 条）；`tests/conftest.py` 新增 autouse 闸 `_nobody_launches_a_browser`（`BaseException` 子类：`ensure_chromium` 吞掉一切 `Exception`，能被吞掉的闸等于没有闸 —— 实测过）
- **静态列表页被判成 JS 壳、整列书名消失（同一条因果链的两处）**：`http://books.toscrape.com/catalogue/category/books/travel_2/index.html` —— 37,009 字节纯静态 HTML、11 本书 —— 抓回的 markdown 是 **196 个字符**：11 个价格、11 个 `In stock`、**书名 0 个**；而 196 恰好压在 `_JS_SHELL_MIN_TEXT_CHARS=200` 下面 **4 个字符**，于是 `error=js_shell_detected` → `content_ok=false` → 一次注定失败的浏览器升级。书名不是没被算出来：同一个函数的链接恢复路径已经跑出 2,406 字符、11 个书名俱在，却被那句反灌水阈值 `len(md_with_links) < len(result) * 3`（196×3=588）当垃圾丢掉 —— 这个比例闸假设「trafilatura 不带链接的输出≈完整」，恰好在抽取塌陷时不成立。修法是两刀，一条链：①抽取臂加 `favor_recall`（塌陷时用召回臂重跑一次）并给恢复闸补绝对下限逃生口，灌水比例只在正文本身站得住时才起作用；②判据侧不再用「壳」盖住塌陷 —— `envelope.is_extraction_collapsed` 比较抽取文本与 DOM 可见文本，严重不符时报**显式的** `extraction_collapsed`（「抽取塌陷」是一个事实，「这是 JS 壳」是一个猜的替代解释），且 `quality` 不许再用 `page_type_from_error` 覆盖结构化 `page_type`。实测：同一份夹具 196 → **1,640** 字符、11 个书名全在、`content_ok=true`、`page_type` 不再被覆盖；爬取入口与读取入口同结果（G20 那对守卫的等式落在这一页上）。反向守卫同样钉住：`quotes_index.html` 那一类真壳页必须仍然判成壳 —— 把豁免做成万能开关比原来的误判更糟。钉住 `tests/test_a_list_page_is_not_called_a_js_shell.py`（12 条）与新增夹具 `tests/page_fixtures/travel_2.html`（sha256 前缀 12647dc86ef4e070）；它的 sidecar 把阈值与「塌没塌」两个测量一起记下，页面或口径变了必须重新看，不许默默漂
- **建议做一件它刚证明做不到的事**：浏览器层坏掉的那通调用里，`next_action` 仍然写着「page is a js_shell; re-fetch auto-escalates to the stealthy browser」，bot_wall 与 403 同样建议 `force_fetcher='stealthy'`，而 `browser_binary_missing` 早在抓取阶段就写进了同一份响应的 `escalation_path` —— 事实已经在了，只是没人读。门是 `_never_escalate()`，它只看本次请求形状（非 GET / `.pdf` / `image/*` / XML / JSON），对升级结果一无所知。修法是把 `escalation_path` 与错误标记读进门里（`_browser_cannot_help`），四处同族一起收口：js_shell、bot_challenge、bot_wall、403，以及一次没碰过浏览器却建议 `force_fetcher='http'` 「跳过渲染」的那条；措辞改成点名当前状态并给出真正可走的两条出路，绝不承诺本管路自己拒绝的东西（照本文件既有的两处先例写）。同一批还有 `doc_parse` 的截断标记：它印 `Next offset: N`，可 `offset=` 被这颗工具**自己拒收**（入参只有 `file_path/cwd/encoding`，分块调用点还硬写 `offset=0`）—— 现在 `doc_parse` 真的有 `offset=`，与 `web_read` 的分页语义一致，白名单、签名、wire、调用点四处同源。钉住 `tests/test_advice_never_recommends_the_tier_that_just_failed.py`（19 条，含「续读的第一段 + 第二段 == `total_extracted_chars` 且第二段不再被截」与四处同源的形状守卫）
- **钳了但没说**：`web_crawl(max_pages=41)` 换来 40 页、`max_depth=9` 换来 5、`concurrency=9` 换来 3、PDF 的 `pages='5-999'` 在一份 12 页的文件上变成 5-12、`web_act` 的 `{"wait": 900000}` 变成 30,000ms、`nested_max=100` 在一颗只有 5 条链接的页面上变成 30、`web_request` 批量里 `timeout=120000` 变成每次尝试 30,000ms —— 信封里一个字都没有。而同一族里 `web_read` 与 `web_search` 是说的（`max_content_chars clamped 1->500 (supported range 500-200000)`），于是「钳了但没说」读起来像只有某些工具才有的缺陷：调用方以为自己点的那个数生效了。修法按 `params.py` 写下的意图走 —— 边界上报**下沉到钳制真正发生的那一层**（crawl / pdf / actions / nested 各自就地写），不在上游装饰器里重抄一遍边界（= 在说明的上一层静默钳制；新增一条源码守卫钉这一条）；那句话只有一个作者 `context._clamp_note`，爬取走自己的 summary、读取走 ContextVar，两条通道读同一颗纯函数。上报通道本身也补齐：从前 `_ARG_NOTES` 只由 `web_read` 的装饰器填充，`web_crawl` / `web_discover` 没有那颗装饰器 ⇒ helper 在爬取里是**死的**（这正是 `crawl.py` 自己手拼一句同款式字符串的原因）；现在一次 MCP 调用一个 notes 桶，11 颗工具全覆盖。钉住 `tests/test_every_clamp_is_announced.py`（22 条）
- **一次调用 8 万 token 的载荷，与手册里那句「爬取没有整轮预算」**：`web_crawl(max_pages=41)`（钳到 40）× 默认 8,000 字符/页 实测 **223,599 字符 ≈ 81K token**，被宿主截断落盘；`tools.md` 与 `web_crawl` 的工具描述当时都明写着「爬取没有整轮字符预算」。新增 `max_total_chars`（默认 60,000，界 1,000–200,000，`0` = 不设帽）：帽砍下去时按 `nested` 那套 `truncation=` 的既有词汇说清返回了几页/多少字符、还剩几颗在队列里、最后一批里有几页抓回来却没返回，并把「一页的长度只有抓回来才知道」写进措辞；`next_action` 点名这颗旋钮与它的上限。工具描述与四份手册语料同时改口 —— 一份会说谎的安装说明比一份短的更糟。同批第二刀是样板：`metadata.ssrf_recheck` 那 273–361 个字符描述的是**进程属性**，却每颗 URL 一份，20 URL 的批量 ≈ 5.5–7.2KB 同一段话；改成每次调用一份（`BulkResponseModel` 的调用级字段，只在各行一致时提升，不一致时各行留着自己的那份），`web_crawl` / `web_discover` 也补上这个字段 —— 此前那两个模型根本没有 `metadata` 槽，这份说明在爬取里**无处可放**。为这两条新建**结果面**尺子 `tests/tool_payload_measure.py::bulk_payload_report`（此前只有 CONNECT 面的 `CONNECT_CEILING`，结果面没有任何可复用的尺，所以载荷 regression 只能靠人眼）：实测 20 行批量 328,365 字符、`ssrf_recheck` 出现次数 **20 → 1**；`40 × 8,000` 那一形 324,001 字符 ≈ 77,885 token，正是新帽要拦的那一形。钉住 `tests/test_ceiling_report_regressions.py`、`tests/test_fake_ip_auto.py::TestTheNoteIsSaidOncePerCall`、`tests/test_tool_payload_measure.py`
- **读数诚实（2026-10-05 实测的五处，五处都是「一句没有检查支撑的绿灯」）**：①`sys_state(show=["all"])` 的返回体里只有 `message` 一个键 —— clear 那一侧有 `all` 的展开，show 这一侧 `report = set(show_tokens)` 从来没有，而消费方全是精确成员测试（`cache` / `engine_state` / `cred_defaults` / `cdp_hosts` / `config`），`"all"` 一个都不匹配。现在 show 用同一份 `_STATE_TOKENS` 类名单展开（`config` 仍要点名 —— 静态配置不是运行时攒出来的状态，这条写在原处）。同族另一半也修：`config_keys` 是 `default_factory=list`，而 wire 省略表会把等于默认值的字段整个删掉 ⇒「一个旋钮都没设」与「数据被吞了」字节相同；今天报的是**几类、各几项**，没旋钮时明写 `none are persisted, so every knob is at its built-in default (this is not a missing read)`。②`metadata.published_time` 的 `str(d)[:20]` 是全 `src/` 唯一一处 `[:20]`，它把带时区的值**从偏移中间剪断**（`2026-03-13T14:17:06+08:00` → `2026-03-13T14:17:06+`、`…06.500Z` → `…06.`），残缺串原样进 transcript；帽抬到能容纳完整 ISO-8601 的 40。**没有**顺手「修」`content_age_days` / `is_stale`：`envelope._parse_date` 算得是对的，那句不是缺陷。③枚举参数改成 **dhole 自己报错**：12 处 wire `enum` 全部删除，且**先补齐校验器再摘**（`extraction_type` / `focus_mode` / `focus_engine` 此前根本没有自己的报错）—— `freshness="decade"` 从前被宿主的 JSON Schema 挡在门外、**不列合法值**，现在得到 `Invalid freshness: 'decade' (day|week|month|year)` 加上「没发出任何请求」。`mode` / `freshness` 的合法值名单**留在 `search.py`**（一颗值只有一个所有者，不在参数层重抄一遍）。代价如实记账：连接面 30,322 → **30,474**（+152：删掉 12 个 enum 数组，加回来的是描述里的值清单），`tests/tool_defs_lock.json` 重刷 —— 这是棘轮第二次上抬，理由写在账目注释里。④`dhole --doctor` 的 `resolver reality` 与服务进程判定相反：两处都用 `socket.getaddrinfo` 自己探，而服务进程的判词**缓存到进程寿命结束**、doctor 问的却是自己本进程（`_dns_recheck_enabled()`）—— 两处必然自洽，等于从未核对；于是两个会咬人的方向永远印不出来（doctor 看到公网就 return，而那个拒掉你每一个 URL 的进程还缓存着另一份答案）。现在测量者把 `{verdict, address, probe, pid, probed_at}` 落 `~/.dhole/resolver_probe.json`（第十个被 fixture 接管的状态文件，0600/0700），doctor 读它、点名是**哪个进程、什么时候**答的，不一致时说要重启 dhole —— 判词按进程缓存是设计（复查层不能在请求中途改立场），doctor 自己也不再调用那颗会写盘的探测，「除了探针不落任何东西」这条老承诺因此不必开豁免。⑤`doc_parse` 不再给猜出来的编码发绿灯：Shift_JIS 的 `sjis.csv` 与 EUC-KR 的 `euckr.html` 都被判成 `gb18030`、输出流式乱码（`名前|都市` → `柤慜|搒巗`、`한국어 텍스트입니다` → `茄惫绢 咆胶飘涝聪促`）却 `content_ok=true`、零警告，而显式传 `encoding=` 立刻拿到正确的原文 —— 解药存在，但它要求调用方预先知道答案，而那正是检测器该给的东西。GB18030 严格解得开那几种旧编码、出来的全是合法汉字（damage 0、无替换符、无 PUA），是**实测过的已知边界**，便宜的判别器不存在，所以能诚实说的只有 weaker 的那半句：`metadata.encoding_basis="guessed"` ＋ `next_action` 说明「没有人声明过编码，这一个是 dhole 选的；传 `encoding=` 就把猜测变成判定」。**不翻 `content_ok`** —— 一份真正的 GBK 文件满足同一条「没人声明」而读得完全正确，把它报成失败才是新的谎。钉住 `tests/test_sys_state_tool.py`、`tests/test_metadata.py`、`tests/test_the_enum_argument_values_are_checked_by_dhole.py`（26 条）、`tests/test_the_resolver_verdict_is_said_by_the_process_that_holds_it.py`（22 条）、`tests/test_charset_honesty.py`（15 条，含「编码猜测被说出来**同时** `content_ok` 不被翻假」这一对）

- **CDP 的授权闸与执行围栏读的是两个名字，所以 attach 从来没读回来过一个真实站点（2026-10-05 真实使用实测，两次复现）**：拿开好的调试端口去读一个已登录的站点 —— `web_read(url='https://i.mooc.chaoxing.com/…', cdp_url='http://127.0.0.1:9222', confirm=true)` 得到 `error=cdp_host_not_allowed`、`status=0`、`content=[]`，一个字节都没读回来，而同一通调用里前一句刚把主机「授权」完。根因是三处拿的不是同一个名字：闸门 `_cdp_attach_gate` 记的是**调试端点自己的主机**（`urlparse(cdp_url).hostname`，永远 `127.0.0.1`），route 层围栏与抓取入口的快失败却拿**每个请求的目标主机**去比那名册 —— 名册里那个名字永远不可能出现在比对表上，`confirm=true` 传多少次都不换结果，凡是点名去读非本机站点的 attach 必然撞死在围栏上。这不是语义变更：本版上面「逐主机一次性授权」那两条与「白名单外的导航拿不到你的认证 cookie」那条一直是按**站点**说的（「attach 你的真实 Chrome 等于交出其中所有已登录站点的读权限」），代码却按端口记 —— 一条从来没跑通过的路，文档写什么都不构成约束。修法把授权对象收成一处：`_cdp_target_hosts(targets)` 从这一次调用点名要读的那些 URL 取主机（`web_read` 传 `urls or [url]`，`bulk_stealthy_fetch` 传 `urls`），闸门按它们查、按它们落盘，围栏继续按它们拦；新增守卫把「同一份名册、同一个名字」钉成等式（`_create_route_handler(False, allowed_hosts=…)` 与入口预筛对同一主机必须同判，授权过的目标主机在预筛处**放行**），批量车道那记 raise 保留成纵深防御而不是唯一护栏。第二处收口是**拒答要整句送到调用方**：围栏那句实测 390 字符（主机名 `i.mooc.chaoxing.com`）从前经 `str(resp)[:200]` 过闸，剪断的位置是 `…fenced to the hos` —— 保头丢尾，被剪掉的正好是唯一可执行的那半句（`confirm=true` 怎么授、`sys_state(clear=['cdp:host=…'])` 怎么撤）；现在 `_tier_error_text` 让这两类 CDP 错误全文通过（其余仍剪到 200），`web_read` 入口的闸门拒绝就地返回一只有形的信封（`_cdp_refusal_result`：`status=0` / `content=[]` / `fetcher_used="none"` / 完整 `error`），`quality` 补 `cdp_authorization_required` 与 `cdp_host_not_allowed` 两支 `next_action`，summary 说 `awaiting approval` 而不假装抓过一次。措辞四处同步（`SKILL.md` 硬约束 11 与错误表、`references/configuration.md` 的「先问再记」、`references/troubleshooting.md`、wire 上的 'the FIRST attach for a site' / 'nothing executes without it'），连接面净 **-7**（30,474 → **30,467**，棘轮往下走；`tests/tool_defs_lock.json`、README 与手册价目表同批改口）。这次实验落进名册的 `127.0.0.1` 按工具路径撤销：`sys_state(clear=['cdp:host=127.0.0.1'], confirm=true)`。钉住 `tests/test_cdp_attach_g33.py`（30 条：未授权目标的 attach 一个请求都不发、`confirm` 后落盘的是目标主机不是端口、闸门与围栏同源、拒答信封的形状）

- **attach 连上了却没有登录上下文时，答复长得像成功（2026-10-05 实测：同一台 Chrome、同一个 URL，第一通信封里没有 `cdp_attached`，之后每一通都有）**：`_resolve_cdp_context` 在端点报不出任何 context 时退回 `new_context()` —— 那是一个隔离 profile，页面照样 200、内容照样是真的，只是没有你的 cookie；而 `cdp_attached` 的默认值是 False，wire 折叠把「等于自己默认值」的字段整个删掉，于是「登录态没跟上」与「这颗字段不适用」**字节相同**，`escalation_path` 还照旧写着 `cdp-attach:stealthy`。这是「空回执等于吞掉数据」那一族换了主语：调用方手里唯一的线索是一个缺席的键。裁决（操作员定）：**判失败，一个字节都不给** —— 点名 attach 的理由只有那个登录态，答一份匿名视图就是答非所问。落地五处：①新增 `CDPNoLoggedInContextError`（`cdp_no_logged_in_context`），resolver 不再建 context，而是数出它看见的东西（`0 browser context(s) (N target(s))`）并给两条真出路（原样重发一次，因为刚起步的端点可能报得晚；反复出现就是这台端点不是装着你登录的那个窗口，去核对 `--user-data-dir` 与 `--remote-debugging-port`）②`BrowserSession.start` 里这一支**不进** `record_launch_failure` —— 端点答过话了，这不是"装上了起不来"，而那块盘是写给下一个进程读的 ③`_tier_error_text` 让它的 remedy 全文过闸（与围栏那条同族，帽子照旧剪噪声）④`quality` 给它自己的词：summary 说 `no logged-in context` 而不是 `network error`（没什么网络可查），`next_action` 明写「这里的空回执不是一张空页面」，并点出「删掉 `cdp_url=` 回答的是另一个问题」⑤手册两处同步（`SKILL.md` 错误表一行、`references/troubleshooting.md` 一条）。一处顺带收益写在这里是因为它正是这次修改的**判据**：`cdp_url` 描述里那句「Every response fetched this way returns `cdp_attached=true`」从前会被证伪，现在**由构造成立**，所以 wire 一个字都没动（连接面保持 30,467，工具定义哈希不变）。钉住 `tests/test_cdp_attach_g33.py`（30 → 34 条，含两条反向守卫：拒答那一支不许被改回 `new_context()`、也不许被记成 launch 故障；以及「一个隔离 context 都不建」）

- **端点不在的时候，整条响应退化成一句裸 `{"error": ...}`（2026-10-05 实测：操作员把 attach 端点存进 `cred_defaults` 之后重启了浏览器）**：这一通 `web_read` 拿回来的是 `{"error": "BrowserType.connect_over_cdp: connect ECONNREFUSED 127.0.0.1:9222\nCall log:\n  - <ws preparing> retrieving websocket url from …"}` —— 没有 `summary`、没有 `next_action`、没有 `content_ok`、没有 `status`，正文里还带着 Playwright 的 call log。三处分别失守：①`connect_over_cdp` 的异常从抓取层一路逃到 `call_tool` 的通用处理器，而那只处理器只会把异常串塞进 `{"error": ...}` 并 `[:300]` 剪尾 ②补建议的那段话挂在 transport 层，条件是 `arguments` 里有 `cdp_url` —— 端点来自 `cred_defaults(set={'cdp': …})`（操作员存的那份，也正是"后续不用每次传"的用法）时**不满足**那个条件，实测一个字都没补上；显式传时补了，整句 368 字符又被 `[:300]` 剪掉最后 68 个字符（`…user-data-dir you approved hosts from), then re-call with the same cdp_url`）—— 与上一条被剪的围栏句是同一个病 ③那句话说 "Start **Chrome** with --remote-debugging-port"，而 Edge 是同一个协议、同一个端口，对 Edge 用户它是错的。修法把这四个字归给**知道端点的那一层**：新增 `CDPUnreachableError` 与 `_cdp_unreachable_text`（点名是哪台端点、只留原因第一行、"Chrome or Edge"、明写 NOTHING was fetched 与两条浏览器侧出路），`stealthy_fetch` 把这类设计好的拒绝收成正常信封（`status=0` / `content=[]` / `fetcher_used=none`），`quality` 给它词（summary `no cdp endpoint` 而不是 `network error`；`next_action` 明说"调大 timeout 没用"与"删掉 `cdp_url=` 回答的是另一个问题"），`escalation_path` 在被拒的那些行上改成 `cdp-attach:refused` 而不是继续声称走过 attach；transport 层那颗只读一个来源的补丁**删掉**（一句话一个作者，兜底留给真正没预料到的异常），`record_launch_failure` 也不记这一支（用户的调试端口故障不是 dhole 的安装故障，记错了下一次 `dhole -v` 就说假话）。手册三处同步，README 与 `configuration.md` 的启动示例补上 Edge 等价写法。钉住 `tests/test_cdp_attach_g33.py`（34 → 40 条：两条来源同一句话这条是这批的判据、非 CDP 的浏览器异常仍然照旧抛出、transport 不许再改写这句）

- **schema 把容器意图写在顶层时，静默只抽一条（2026-10-06 实测）**：`{"type": "array", "selector": ".athing", "properties": {...}}` 被 `_normalize_schema` 的宽容放行放过（有非空 `properties` 就过），而抽取端只读 `properties` —— 顶层 `selector` 与 `type` 被无视，每个字段各取页面第一个匹配，组装成**一条**记录。HN 首页 30 条帖子实测只回 1 条：调用看起来成功（200、`extracted_type=structured`），结果是错的 —— 错误答案比报错糟。修法是发请求前具名拒绝，报错带可直接抄写的容器嵌套示例；顶层纯标量 `properties`（页面级字段）与 `type/mode:"auto"` 不受影响。同批：`web_act` 动作形状报错此前只列合法键名清单，不带 index 寻址的载荷形状 —— 实测调用方写 `{"index": 0, "fill": "..."}` 被拒后只能靠猜（正确写法 `{"fill": {"index": 0, "text": ...}}` 躲在 examples.md 的示例区）。报错现在附示例。钉住 `tests/test_schema_param.py`（+6 条）与 `tests/test_wire_arg_type_regressions.py`；SKILL.md 的 web_act 行与 `tools.md` 的 Structured extraction 同批改口

- **check_login 在回答不了的档位上是缺席而不是答案（2026-10-06 实测）**：`check_login=true` 在 HTTP 档的返回体里字段整个不存在 —— `login_check=None` 被 wire 折叠成缺席，"查了但这档答不了"与"没查"字节相同（模型描述写着 "null on HTTP-tier results"，wire 上却是键都不在），agent 只能去翻手册才知道要加 `force_fetcher='stealthy'`。现在三条非浏览器车道（强制 HTTP、自动升级、缓存命中）都接 `_attach_login_probe_gap`：请求了就回显式标记 `{logged_in: null, method: "unavailable: …"}` ＋ next_action 指路，浏览器档的探测通路一字未动。同批四处"回执说真话"：①死域名的归因——`all_tiers_failed: unknown (HTTP status 0)` 是"从未拿到过任何 HTTP 响应"，反爬墙的推断从沉默里不可证伪（墙会答 403/挑战页），tips 按 `category=unknown && status=0` 改说"先查拼写与 DNS"；fake-IP 段地址（198.18.0.0/15，TUN 代理签发）补进错误分类，不再落 unknown ②`_auto_escalate` 的 tips 链抽成纯函数 `_all_tiers_failed_tips(category, status)`，各已知分支文案不变 ③offset 续读的回执带 `focus re-applied` 摘要注（focus 头住在分页文本里，续读窗口从它之后开始——focus 确实在重放，只是头被切掉了；注写在 summary，不动分页算术）④`escalation_path` 的 `cdp-attach:stealthy` 改为 `cdp-attach`（档位归 `fetcher_used` 说，两个概念不再拼在同一字段）。钉住 `tests/test_the_receipt_tells_which_tier_can_answer.py`（11 条，含 fake-IP 归类与"分页算术不许被动过"）

- **回执卫生四处（2026-10-06 实测）**：①data: URI 不再进正文——`_fallback_extract`（trafilatura 塌陷后的 markdownify 兜底臂）不剥内联图片，Instagram 一页 21.6KB"正文"大半是两个 base64 PNG，而 `metadata.media` 早就跳过 data: 资产（同一判定两处口径）；markdown 臂转换前剥 `img[src^="data:"]`，外链图片不误伤，html/text 档不动（那是点名要原始字节）。②`focus_mode="best"` 的 best 块可能只是个标题（实测 233 字节的 "## Why not Argparse?"）——bm25 档没有神经档的意图下限提示，现在块长 <200 字符时 header 自己说明"可能只是标题，没有答案；换 filter 模式或改写查询词"。③注入启发式豁免代码格式——`scan()` 匹配前把 fenced code block 与 inline code span 掩成等长空白（偏移不变，excerpt 仍切原文）：启发式标的是"指令形状"，而文档正文里 `web_read(url=...)` 的调用示例恰好就是那个形状，dhole 自己的 README 被标了 9 处；裸文本注入不受影响。④SPA 动作后回读指引——动作链全部 ok 但 `page_type=js_shell` 时（React 页的 DOM 本来就是壳），next_action 明说"回读是壳不是动作后状态；发生了什么看 metadata.actions，要看渲染结果加 screenshot 动作"。钉住 `tests/test_receipt_hygiene.py`（7 条）

- **`.docx` 与 `.xlsx` 的四条「看起来成功」（2026-10-06 实测；这两个格式是全仓唯一没有输出契约测试的）**：`.pptx`/`.odt`/`.json`/`.yaml` 有 20 条断言钉住输出形状，而最常用的两个格式全仓只有一句 `assert ".docx" in SUPPORTED_EXTENSIONS` —— 没有任何测试真的解析一份 Word 或 Excel 并断言它读出了什么，四条缺陷因此活了下来。①**xlsx 的截断提示写反了**：500 行的表回 `... (99 more rows shown, sheet may have more)`，而那 99 行正是**被丢掉**的行；数字也不是量出来的（读满 200 行就 `break`、只渲染 99 行、报的是 `200-101`），与真实剩余行数无关。截断读起来像完整，正是本仓最防的那类失败。现在按 `ws.max_row` 报真实剩余（`399 more row(s) not shown, of 500 in the sheet`），上限推出来的那句含糊话删掉。②**docx 把所有表格搬到文末**：`doc.paragraphs` 全部输出完再 append `doc.tables`，于是正文里「见下表」的引用指到了文末之后（实测 标题/段落/表格/段落 → 标题/段落/段落/表格）。现在按 `w:body` 子节点顺序走（`_docx_blocks`），表格留在原地。③**docx 丢了页眉/页脚/脚注/批注却一字不说**：同一个模块里 `.pptx`/`.odt` 都会在首行声明自己带不走什么，只有 `.docx` 不说 —— 实测页脚「机密 · 页脚里的重要声明」无声消失，一份少了页脚的稿件看起来和完整的一模一样。现在首行按 `.pptx`/`.odt` 同一契约声明，且**只在文件真有那些部件时报数**（`dropped: 1 header/footer(s)`，普通文档只给边界、不给数字）；匹配的是**部件名**而不是字节 —— `styles.xml`/`numbering.xml` 里也有 `<w:t>`，按字节判会把每一份文档都报成「丢了页眉」。④**xlsx 的公式单元格静默变空白**：`data_only=True` 取的是缓存值，而库写出的工作簿没有缓存，`=A2*B2` 渲染成空格子（`| 3 | 100 |  |`），读起来像「这格没数据」而不是「这格是算出来的」。现在第二遍流式扫描取公式文本（上限与渲染同帽），无缓存的格子回 `=A2*B2` 并在表末报数；整行都是公式的行也不再因为「全是 None」被当成空行丢掉；公式扫描失败只降级成「没有公式」，不赔上整张表。钉住 `tests/test_a_docx_and_a_sheet_say_what_they_left_behind.py`（13 条 —— 对旧实现 9 红 4 绿，那 4 条绿的是层级映射、字面量表与「不许误报」的不变量守卫），`tools.md` 的 doc_parse 段同批改口

- **旧版 Office、`.xlsm`/`.tsv` 与加密/多页 PDF 的三条「本地读不如网上读」（2026-10-06 实测）**：同一份字节，走 URL（`web_read`）与走磁盘（`doc_parse`）给出不同答案 —— 正是 `_parse_pdf` docstring 承诺不会发生的事。①**旧版 Office 静默走错路**：`.doc` / `.xls` / `.ppt` 是 OLE2/CFB 容器、不是 zip，此前 `.doc` 被回一句「rename it to .txt」（改扩展名不改字节，照着做只会再错一次），而一个被改名成 `.docx` 的 CFB 文件（最常见的自救尝试）直接掉进 python-docx，回的是 `PackageNotFoundError: Package not found at '...'` —— 一个既不说真格式、也不给下一步的内建异常。现在**字节先于扩展名决定**：魔数（`D0 CF 11 E0 A1 B1 1A E1`）命中就按它真实的格式命名，并给一条真能走通的路（`soffice --headless --convert-to docx <file>` 或原应用「另存为」）；`.msi` 一类**非 Office** 的 CFB 容器只报通用行，不被教「另存为 .docx」。②**`.xlsm` / `.tsv` 被挡在门外**：openpyxl 直接就能读宏表，`_sniff_delimiter` 本来就认 `\t`，缺的只是放行 —— 零成本的能力，拦截才是在替调用方做错决定。③**加密 PDF 的报错在骗人**：`encrypted_pdf` 那条教调用方「pass the correct one via the 'password' option」，而 `doc_parse` **没有**这颗参数 —— 照做就是 `TypeError: got an unexpected keyword argument 'password'`（经 server 实测）；40 页的本地 PDF 还会整份回来、外加一份 40 条 `table_of_contents`，而调用方无从下手。现在 `doc_parse` 有 `pages=`（`'1-5'` / `'1,3,5-7'`，只收窄**抽取量**、文件仍整份读）与 `password=`（打开加密 PDF），**与 `web_read` 同名同义、逐字复用其描述**（同一事实一个作者，也最省连接期）。连接期 30,824 → **31,065**（+241；棘轮第四次上抬，理由同前三条：参数名与判据必须在连接期到场 —— 藏在签名里的名字会被 dispatch 当表外名拒掉，调用方永远够不着）。钉住 `tests/test_a_legacy_office_file_and_a_locked_pdf_say_what_they_are.py`（16 条 —— 对旧实现 14 红 2 绿，那 2 条绿的是「无口令报错原文」与「真 zip 型 docx 不被魔数检查误伤」的不变量守卫），`tools.md` / `recipes.md` / `SKILL.md` 的 doc_parse 段与 `configuration.md` 价目表、README 的连接期数字同批改口

- **`.pptx` 的页序按文件名排，于是拖过页序的稿子读出来是乱的且零提示（2026-10-06 实测）**：`slide1.xml` / `slide2.xml` 里的数字是**文件名**，不是**位置** —— 在 PowerPoint 里拖动一页，改的是 `ppt/presentation.xml` 的 `p:sldIdLst` 清单（`<p:sldId r:id="rIdN"/>` 的先后），文件名一个都不动。旧实现按文件名数字排序，于是任何被重排过的稿子读出来的顺序都是错的；而错得**很安静**：内容一行不少、第一行的「N slide(s)」也对，调用方没有任何办法从输出里看出顺序错了。一份页序错乱的摘要是错的摘要，读起来和一份正常的摘要一模一样 —— 本项目当作比报错更糟的那一类失败。实测（手工造的稿子，清单明写 slide2 在前）：旧输出 `## slide1.xml` 然后 `## slide2.xml`，新输出反过来。现在页序取那张清单，经 `ppt/_rels/presentation.xml.rels` 把 `r:id` 翻成部件名；判据是 `<p:sldId>` **元素出现的先后**，不是它的 `id` 属性（`id` 是创建序，递减的清单上取 `id` 会给出相反答案，有专门一条测试钉住）。**清单读不到就退回文件名顺序，而不是丢页**：`presentation.xml` 或 rels 缺失（第三方导出器确实会写出残缺包）退回文件名；清单只点名一部分时，点名的按它的顺序在前、其余按文件名补齐；`r:id` 指向不存在的部件就跳过它。修一个「顺序错」不能换来一个「少几页」。**连接期一个字没动**（工具描述里没有提过页序，31,065 与锁哈希不变），`tests/tool_defs_lock.json` 无需重刷。钉住 `tests/test_a_slide_comes_out_where_the_deck_puts_it.py`（11 条 —— 对旧实现 4 红 7 绿，那 7 条绿的是「无清单/无 rels 退回文件名」「没拖过的稿子逐字不变」「输出契约一字未动」的不变量守卫），`tools.md` 的 doc_parse 段同批补一句

- **`.pptx` 的讲者备注不读，同时正文里混进了页码（2026-10-06 实测）**：两件事同一批，因为它们改的是同一个函数（`_pptx_paragraphs`）。①**备注从来不读**：幻灯片正文常常只有标题级短语，讲稿、数据来源与要点住在 `ppt/notesSlides/` 里 —— 而「总结这份 PPT」正是这颗工具最常见的用法。旧行为是**明确声明**「speaker notes … not included」（不是静默丢弃，这点它一直做对了），但对这个任务来说信息密度更高的那一半被留在了门外。现在备注按每页**自己的 rels** 挂回它那一页，标成 `> Notes:` 引用块。四条判据各有理由：**挂在它自己那一页下面**（关联是按页的，搬到文末就丢了「这条属于哪一页」，那信息找不回来）；**标成引用块**（不标就和正文糊在一起，读的人会把讲者备注当幻灯片内容引用，那正是「看着对、其实错」）；**没有备注的页不写占位符**（同 `.docx` 那条规矩：只在文件真有的时候才说）；**页序变了备注跟着走**（备注跟的是配对，不是位置）。②**`<a:fld>` 布局字段被当成正文**：幻灯片编号/日期占位符里包着一个真的 `<a:t>`，于是实测一份正文是「第三季度营收 / 机密」的幻灯片回的是「第三季度营收 / **3** / 机密」—— 一个凭空多出来的数字，读起来像作者写的。字段一律不抽。**这一条与备注无关**（幻灯片路径上本来就有这个毛病），但备注页每一页都带编号字段，所以过滤它是读备注的前提 —— 这也是把两件事放进同一批的理由：分两次做等于把同一个函数改两遍。第一行随之从「speaker notes, layout and diagram text are not included」变成「layout and diagram text are not included」，wire 上同一句把 `speaker notes, ` 划掉。**连接期 31,065 → 31,050（-15，棘轮往下）** —— 本批唯一一次向下：删掉的是一句不再为真的话，不是信息。**这是契约变更不是纯缺陷修复**：一份从前只有正文的输出现在会多出 `> Notes:` 块，逐行比对输出的人会看到差异；有备注的稿子输出也会变长，但 `offset` / `is_truncated` 照旧如实报数，所以是「如实变长」而不是「静默截断」。钉住 `tests/test_the_notes_come_with_the_slide_they_belong_to.py`（13 条 —— 对旧实现 10 红 3 绿，那 3 条绿的是「无备注不生成块」「悬空 rels 跳过」「版式仍在带不走的清单里」的不变量守卫）；g30 那条 `test_speaker_notes_stay_out_and_the_output_says_so` 钉的正是这条被推翻的旧契约，拆成「没有任何 rels 挂上的备注部件不算内容」（这一问仍然成立，且是配对判据的另一面）与「第一行说清剩下什么」两条。`tests/tool_defs_lock.json` 与 `CONNECT_CEILING` 随实测下调，`tools.md` 的 doc_parse 段与 `configuration.md` 价目表、README 的连接期数字同批改口

- **手册与 README 对齐线路（2026-10-07，三批）**：①`extraction_type='check'` 第六类补齐（changed 三态 / first_divergence_at / diff_window，字段语义照实现逐条核对）——线路活着、手册查无此人，条件请求一节同步改真；②六颗全语料查无此人的参数（`web_crawl.max_depth`、`web_search` 的 max_results / exclude_sites / region / fetch_schema、`doc_parse.cwd`）与 `mode='find_similar'` 补进手册，两条守卫按参数表全工具参数化钉住「wire 上的每个参数都查得到」与「能力特征串必须可达」；③「凭据值不进转录」改双向真话（存储不回吐；传参就在转录里；要完全不出现就操作员预配置）——无条件的旧句是假话；④`cdp_hosts` 名册「无过期、与端点无关」显式化；⑤`cdp_attached` 的审计声明改条件句（审计通道默认关）；⑥readOnlyHint 一节把三重辩护（GET-only / 进程内簿记 / CDP 首挂围栏）与「据 hints 自动放行 readOnly 的宿主不会为 ignore_robots 再问人」的残余偏差同段写明；⑦README 的工具子集数改实跑口径（旧数是全量 instructions 口径，与裁剪路由行的真实行为不符）
- **回执说真话（2026-10-07）**：页内样板折叠进 trafilatura 主路（`duplicate_lines_collapsed` 不再只在空结果回退路上兑现，折叠规则零改动）；`metadata.title` 三条来源统一过实体解码，与正文同一套口径——标题里的 `&amp;` 不再以原样进转录
- **`fetch_content=true` 的内联扇出按宿主的 30 秒分钱（2026-10-07）**：搜索结果的相关行逐页抓取是串行无闸的，默认预算下实测 46.5 秒——宿主在 30 秒处剪断，**什么都不回来**（裸的 "tool execution timed out"，比回一份残缺答案更糟）。现在每一发都从"这一发还剩多少"里取一页的份额（单页下限 3s、上限 15s，并给每页预留 1.5s 收尾宽限 + 0.5s 缓冲；总预算给宿主回复留出 2s），预算见底就把剩下的行登记成 `error="inline_fetch_budget"` 的空位、summary 说 `fetched N, skipped M`。同刀补齐"每个洞自带理由"：抓取失败不再只留一个缺行的列表——失败行照样占位，`error` 拿不到就依次取 `diagnosis.code` / `page_type` / `summary`，不再有"fetched_pages 少一条而不知道为什么"。A/B 实测（本地靶站每页睡 14 秒）：无闸 46.5s / 有闸 25.0s，五行全有交代
- **CSS 选择器能不能编译，在发包之前就知道（2026-10-07）**：`:visible` 在 Chromium 里合法、在 lxml/cssselect 里非法，而抽取引擎对后者的态度是"吞掉异常、返回整篇"——于是 `css_selector=':visible'` 的响应读起来像选择器生效了。新增 `validate_html_css_selector`：先过注入守卫再真的试编译，编译不过就在**一个请求都不发**时点名是哪颗参数、解析器抱怨了什么；`css_selector` / `nested_selector` / `schema` 每条 property 的 selector 同走这一道。引擎里保留第二道防线并让它说话（`_note_selector_not_applied`：「这个选择器没有被应用，返回的是整篇文档」）——两道守卫各有读者，可编译性前移只认抽取层认识的语法，浏览器层那把 CSS 仍由 Chromium 判
- **screenshot 一颗参数、一条规则、一句报错（2026-10-07）**：它接受四种形状（`true` / 选择器串 / 带 target 的 dict / 带 index 的 dict），而拒绝句按分支各写一小段——从任何一条都读不出另外三种，于是"改一个方向去猜"变成唯一出路。现在 `_screenshot_shape_error` 一处写全四种形状与全部旋钮。裸的 `{"screenshot": true}` **仍拒**：实现成视口截图要加 wire 面，那是待判决（见「待 operator 判决」），本刀只把报错文本改对
- **`mode='find_similar'` 的 query 死锁（2026-10-07，本轮唯一动 wire 的一刀）**：这颗模式要的是**一颗 URL**，而 wire 的 `required` 把 `query` 钉成必填——照文档调用必然被自己的校验拒掉，或缺 query 时得到"请给 url"，两句都不是真话。判决搬到认识 mode 的那一侧：`query` 默认空串，`_validate_mode` 先跑，只有非 find_similar（或确实带了词）才走 `validate_search_query`；`required` 从 web_search 的 schema 摘掉（dispatch 的缺参表同步去掉这颗键），参数描述改说"仅 find_similar 下可省"。连接面 30,768 → **30,750**，锁文件重刷，README / configuration.md / tools.md 的实测数字跟着走（子集数用 `_compose_instructions(subset)` 重算，不是全量数）。真 stdio 复现：`required=[]`，不带 query 的 find_similar 拿到 3 条真实相似页，进程 0 退出
- **命名会话登录之后，读回来的还是匿名时代的那份抄本（2026-10-07）**：缓存按 URL 建 key，不认识"这次是谁"——`session_id` 登录成功后，TTL 内的 `web_read` 直接命中登录前抓的正文（实测页面写着 Login 而非 Logout），而回执里只有 `cached` 一个字。先改语料（tools.md / recipes.md / SKILL.md 三处教"登录后带 `cache_ttl=0` 把这一页读回来"），再补行为：`session_id` 在场且这一发是缓存命中时，summary 追加一句"这份抄本可能早于本会话的 cookie"。**没有**动缓存键：给键并进身份会让每次登录把公开缓存作废，那是另一笔判决
- **探测过期时，那道 SSRF 守卫报的是别人的错（2026-10-07）**：fake-ip TUN 在会话中途上线，而进程记着本进程**首次使用时**那次探测的 `public`，从此公网名一律解析进保留段、复验层每一发都咬。信封却写着 `invalid_request` +「Correct the argument」——这里没有一颗参数可以改；真话（重启宿主让探测重判，`dhole --doctor` 能看见现判）此前从未出现在响应里。现在这条拒绝有自己的 diagnosis（`resolver_guard` / `retryable: false` / `recover_with` 点名那条路），next_action 与 error 说同一句；`_tier_error_text` 的 200 字符帽子豁免 dhole 自己写的 `SecurityError`（`web_discover` / `web_crawl` 那条 300 字符的解释此前正断在"出路"之前）；缓存查找整块搬成 `_cache_hit_response`，并在守卫之前先问一次——**命中一个包都不发**，"不许往这个地址发包"这条理由对它不成立，于是一次陈判定不再吞掉一份已经在本机里的正文。这份礼貌只给普通的单 URL GET 文档读（actions 要交互后的页面、schema 要在原始 HTML 上重跑选择器、`check` 要站方对校验子的答复、HEAD 要站方的当前状态、cdp_url 要已认证视角），robots 只读缓存里的规则（为合规再发一发，发的正是守卫拦下的那一发），站方规则说不许就不交抄本。**探测机制本身未动**：仍是"首次使用时跑一次、进程内不重探"（F-C2-J2 判决），重判要不要做归操作员
- **展示批量五处：话要说准（2026-10-07）**：①`sys_guide` 的主题列表把别名表查反了方向（`ALIASES` 是 别名→主题，那一行按 主题→别名 读），没有别名的那几卷打出 `(aka None)`；现在补一张反向表，有别名才打，而 section 没猜中时同一批标题不再列两遍。②`web_search` 的 engine / engines 形状 / location / language / page / site / exclude_sites / 日期这几类参数拒绝只带 error 不带出路——这些全在任何引擎被问到之前，改对再调是零成本，现在每条配一句"怎么改"，engine 那条念出 `360`=so360、`ddg`=duckduckgo。③`cred_defaults set={allow_private:false}` 的拒绝不说真出路：关上 = `unset=["allow_private"]`，closed 是这颗键不在库里而不是它的一个取值（拼错的值拿不到这句）。④`web_crawl` 停早的样板无条件推荐 `path_include=['/docs']`——站点没有 /docs 就是一条修不了任何东西的建议；现在看没抓到的那批 URL 的第一段路径，占 3 条以上且过半才推荐那一棵，并说出它占几成。⑤动作报错与凭据形状句里的 `{{...}}` 是花括号转义漏在非 f 字面量里，读者看到的是两个字符而不是一个形状。计划里第 5 项「doctor 能力面板印两遍」实测复现不出来：`--doctor` 全程只有一处 `print_capabilities` 调用，输出里 capabilities 也只出现一次（检查项报本次实测，面板报可选档位，说的是不同的事）——没有现场就不改行为
- **手册两处与提取链对齐（2026-10-07，只改文档）**：①"A field that matches nothing on that page comes back as `""`" 把话说满了——选择器落空后仍跑三道按**字段名**的回填（页面 metadata / JSON-LD / 正文里的模式搜索），于是 `title` / `author` / `date` 会从页面外壳拿到非空值；实测核对过（`{'title': {'selector': '.nope'}}` 配 metadata 拿到的是 metadata 的值，同页的 `bio` 拿 `""`），并补上逐记录 `fields_no_match` 与整轮 `nested_empty` 两级失败名。②uniform-column 那句警告是跨记录比"整列同值 / 整列空"，所以"每页各自抓错到本页外壳"（books.toscrape 实测形状：每个作者页有各自不同的侧栏 / 页脚文字，N 个各不相同的错值）**静默通过**，响应里没有任何标记；手册现在写明这个盲区，并给出那条一次性的自查：信一次扇出之前，先不带 `nested_*` 读其中一页对一眼
- **重启后实测：解释洞的那句话被自己的帽子剪掉了出路（2026-10-08）**：三处剪尾同一种病，一次收掉。①`fetch_content` 的失败行——`_inline_hole_reason` 的 `[:200]` 把 `extraction_collapsed` 那句剪在 "…Do NOT re-fetch for rendering; "，出路（`take extraction_type='html' (or 'text')`）正好落在被剪掉的尾巴上，而这一行没有第二个字段兜着它（实测现场是 pypi.org 的一页）。帽子留着（外来的 dump 可以无限长），但只落在词界，且被剪过时明写 `[reason clipped]`，帽提到 400 让 dhole 自己写的所有 reason 整句到齐。②`invalid_request` 的 summary 用 `msg[:80]`，实测打出 "…can compile (S"——整句在 `error` 里，标签可以短，但不能短得像坏掉的字符串。③同一条规则给 SSRF resolver guard 的 summary（R3刀1 承诺过"拒绝句不剪尾"，当时收了 `error` 与 `next_action`，漏了标签这一格）。三处共用 `_summary_label`；测试钉法按各自的不变式：塌陷页的长 reason 原样到齐、超长外来文本落在词界并带剪痕、summary 剪口后的第一个字符必是空格
- **升级链替沉默编理由：`unknown` 把可达性问题说成"去查你的 DNS"（2026-10-08）**：现场是宿主里 `web_read(example.com)` 两档全 status 0，回执 `all_tiers_failed: unknown (HTTP status 0)` + 「Check the URL spelling and DNS first」，而同一分钟 `dhole --doctor` 刚把 example.com 判成公网可解析、本机直连同一路 8/8 成功。机制在升级链自己：分类器 `errors.classify_network_error` 认 10054/10061/198.18.x/`ERR_NAME_NOT_RESOLVED` 一堆原话，可 `errors` 那两条只写 `HTTP failed (status 0)`（当年怕原始异常句把 all_tiers_failed 撑爆），而分类优先吃**浏览器档**的 error —— 浏览器档一沉默就退回报纸标题，于是所有沉默都归进 `unknown`。实测（给进程塞一根死代理）HTTP 档的 `result.error` 明明带着 `(os error 10061)` 原话：话说了，没带上来。修法一处：两档的 `Failures` 行各带上限长 140 的原话（前缀形状不变、无话就不加），分类与回执同时受益；浏览器档自己开口时优先级一字未动，两层真沉默仍旧只报"没有收到任何响应"，不发明线索。同刀附带：`browser_launch` 的模式表不认它**自己注释**点名的裸句 `Timed out while launching browser`（只认带 `browserType.launch:` 前缀的形状），裸句落进 `timeout` 便劝人"把 timeout 调大再试"——正是要避免的假出路，而 `actions.py` 早就按 `while launching browser` 片段认它，两份名单就此对齐
- **出路指向的那一节答不出出路问的问题（2026-10-08，真实使用模拟刀10 · P3 批量）**：四处同一族——说的和指的对不上。①`mode='find_similar'` 的第一条结果**就是调用方给的那一页**：实测 `url=https://clig.dev/` 把 clig.dev 自己排在第 1 位，`url=https://docs.python.org/3/library/os.path.html` 把它自己的 `http://…?highlight=abspath` 变体排在第 4 位。机制是注定的：候选来自"拿原页标题去搜"，交叉编码器打的又是（原页正文, 候选）这一对，原页与自己的相似度全场最高——而"给我与这页相似的页"里最没用的就是这页自己，它还占掉一个名额。现在这一页及其同页变体（协议 / `www.` / 尾斜杠 / 查询串 / 锚点之别）不进结果，名额让给真相似的一页，**去掉几行照旧 announce**（少一行却不说的列表，读起来就是"索引里没这一页"）；同 host 不同 path 的页一律保留，普通搜索模式一寸不动（反向锁）。判据故意比 `crawl.normalize_url` 松——那边的问法是"要不要重复入队"，这边的问法是"这行是不是调用方手上那一份"。实测重跑：clig.dev 与 docs.python.org 两例原页都不再出现，仍满 8 行，`fetch_hint` 带上那句交代。②整页截图的回执念出一个不存在的目标：`{"screenshot": {"full_page": true}}` 按设计就没有 selector/index，`_target_label` 对这种 dict 交回 `repr(None)`，`metadata.actions` 于是写下 `screenshot None`——同一个洞 `submit` 早补过并留了注释（"审计轨迹丢掉了它本该记录的那一步的目标"），screenshot 这一格漏了；现在照形状说（`screenshot whole page`），并把"任何合法动作的回执都不许念出 None"铺满整张动作表。③手册 `Page interaction` 一节（正是动作形状错的拒绝句点名的出路）写着"**Element screenshots** are always available"，而 `full_page` / `save_to` / `image_type` / `quality` 四个键一个都不在那一节里——它们只在另一节"The remaining tools"；被拒的调用方照着出路走，走到的是否认他那条出路的页面。现在四个键与两种目标都在那一节写清，锁法是不再重抄一份清单，而是**从拒绝句自己嘴里取出它承诺的键表，逐个到那一节里查**。④手册里 submit 那条不可逆动作的钥匙少一只花括号：`re-run the same actions chain with {"confirm": true`——全语料只有 tools.md 花括号不平（67 对 66）就是这一处，调用方直接抄的文本解析不了。锁到每个手册页面花括号数相等，并把 `{"confirm": true}` 这类片段真解析一遍。顺手：`.gitignore` 忽略 `scratch/` 却不认 `.scratch/`，现场脚本的目录从此不进 `git status`
- **主内容检测器把一份规范判成大半是模板，99% 的正文静默丢失却报成功（2026-10-08，真实使用模拟刀8）**：`web_read("https://www.rfc-editor.org/rfc/rfc9110.html")` 实测交出 **8,357 字符、`content_ok: true`**，正文从 "### 6.4." 开始——一份 1,187,554 字节、剥掉标签后仍有 379,285 个非空白字符的文档，**第 1 节到 6.3 节整段不见了**。机制在 `extract_content` 的采纳判据："trafilatura 候选非空即采纳"，可"非空"与"完整"是两件事，检测器认定大半是样板时交回的那一片同样非空，markdownify 兜底（同一份字节实测 587,795 字符）于是永远不被问第二遍。判据现在是比例而不是有无：提取量低于**文档自身正文**的 12% 且文档够大（≥20,000 个非空白字符）就改用兜底提取器，并在 `summary` announce 换了谁、为什么（丢了 98% 这件事不能让读者自己发现，与"每一处钳制都要 announce"同一纪律）。**阈值是量出来的**：`.scratch/measure_trafo_yield.py` 跑 20 个真页面，唯一异常 rfc-editor 在 2.20%，次低的正常页 pypi.org 在 46.74%，其余全部 ≥71%——异常与正常之间有 20 倍空档，12% 落在正中间；小页有独立的下限（447 字符的页本来就只有那么多正文，检测器交回 340 个字符是提取对了）；`css_selector=` 一律豁免（调用方自己收窄的范围，交回的少是预期不是丢失）。实测：rfc9110 8,357 → 580,900 字符（3 页读完，首片 200,000 带 `next_offset`），而 docs.astral.sh / pypi.org / example.com / cursor.com 四条健康页字符数一寸未动、无一 announce。两件事是这一刀真正难的地方：①**守卫钉住了函数，还得钉住路径**——线上主路 `server.py` 直接调 `_trafilatura_extract()`，不经 `extract_content`，判据写在提取器里一次也没跑（与 2026-10-06 折叠那一刀同一个洞换了个判据重现），因此这条链从 `web_read` 全链路重新钉了一遍：回环靶站 + 检测器只交回一片切片，断言文档结尾 `end599` 在答复里、且 `summary` 认得出换了人。②**量不到就 fail closed**——判据是来评判厚薄的，没权利把一次成功的抓取弄成异常：全套跑出来 5 个原本绿的 `bulk_get` 测试挂了，挂具的 `.content` 是自动长出来的桩物（属性在，但不是文本），正则门口 TypeError；`extract_content` 自己的 docstring 就写着收"Response 或兼容对象"，所以非文本 / 抛异常的 `.content` 是存在的调用约定而不是不可能，一律算作"量不到"（0）而不许换答复，字节则解码后照量（不许误判成 0）。验证：14 绿（含 3 条反向锁：健康提取不动、小页不触发、选择器豁免；一条按量出来的两个数 2.20%/46.74% 各判一次，并锁"换了必 announce、没换必不 announce"）；全套 3848 passed / 6 skipped；ruff 干净；连接面 30,750 未动
- **翻一页付一页的章节地图：`offset=` 与 `pages=` 都不裁大纲（2026-10-08，真实使用模拟刀7）**：arXiv 2005.11401（19 页）实测——`max_content_chars=500` 首片带回 29 条 `table_of_contents`，`offset=500` 续页**同一份再带一遍**，`doc_parse(pages='1-2')` 也带着 "H Retrieval Collapse, page 19" 这种读者手上没有页可对照的条目。按 `approx_tokens` 量那 29 条约 1,000 token，而 `offset=` 恰恰是这条响应自己的 `next_action` 推荐的动作——翻页的代价里十分之一花在一份用不上的地图上。机制：书签大纲（`_extract_toc`）读的是整本文档，`pages=` 只裁了正文没裁地图；无书签那一路早就按提取范围构造（`_heading_outline(..., page_nums, ...)`，注释写着 "Clamped to the extracted page range"）——**两条路口径不一致**。现在：①`pages=` 时地图按实际交回的页裁，裁掉几条进 `summary`  announce（与"每一处钳制都要 announce"同一纪律）；②`offset>0` 的续页不再重发，省略同样说明——字段"缺席"和"不存在"不能混为一谈，改在局部副本上、缓存里那份仍完整；③放不进任何页的条目（缺 `page`）宁可多留一条，不许悄悄丢一章。实测：续页响应 3,807 → 1,671 字符（−56%），`pages='1-2'` 的地图 29 条 → 2 条；不带 `pages=` 的整本读取与首片一寸不变（反向锁）
- **一条失败说两件事，还把最要紧那件排在后面（2026-10-08，真实使用模拟刀6）**：现场是 `check_login=true` 读 github 的已登录页被重定向到登录墙，`next_action` 打成一句："page truncated. Use focus='query' … or offset=500 to continue paginating check_login is live-only on the browser tier: re-call with force_fetcher='stealthy' …"。三处坏在一起：① 截断建议抢在 `auth_wall_detected` 自己的建议之前，而**一页登录表单翻页翻不出内容**，那条出路是错的（404 早就因同一理由让位给状态码，墙是穿了另一件衣服的同一种错）；现在登录墙 / 反爬墙 / JS 壳三类都不许被截断建议占位，反向锁保住真截断的长正文照旧拿到 `offset=`。② 两条独立建议之间只有一个空格，读起来是一句坏掉的长句——拼接处补句子 terminator（前一句已带标点就不重复加）。③ 复验时照出来的另一件：`fetcher_used != "stealthy"` 认的是"这一档没跑到"，可它同样匹配"跑到了没跑完"，实测 `http→stealthy` + 浏览器档超时，回执却说 "this result never reached the browser tier" 并劝人重发**刚刚烧完预算那一档**；现在两种缺口两种说法，到了却没跑完时不再推荐 `force_fetcher='stealthy'`。同刀收 `nested_no_match`：它此前只有 `error` 与 `next_action`，`summary` 是空串、没有 `diagnosis`，而每一条别的拒绝都有——按名字匹配分类的调用方在这一条上瞎。验证：4 红 → 307 绿（含 nested/envelope/server 全套）；全套 3826 passed / 6 skipped；ruff 干净；连接面 30,750 未动
- **feed 的标题与摘要交回来的是源码片段和没解开的转义（2026-10-08，真实使用模拟刀5）**：`feed_read` 是"看这个源发了什么"的工具，实测两条真源都不像答案——HN 首页源的 `summary` 整段是标记语言（`<p>Article URL: <a href="…">…</a></p>\n<p>Points: 1023</p>`），读者要自己再解析一份文档才读到一条标题大小的事实；The Verge 的标题与摘要里是 `Microsoft&#8217;s`、`[&#8230;]`——站方把 HTML 实体**双重转义**了（`&amp;#8217;`），XML 解析只解得掉外层，留下内层当字面量。`feed.py:_text()` 一直是 `child.text.strip()`，原样交出。现在：标题做实体还原（不动尖括号），摘要再叠一层去标签——块级边界换成换行而不是空格（`<p>A</p><p>B</p>` 并成 "AB" 是坏掉的排版），标签正则要求 `<` 紧跟字母或 `/`，所以 "if a < b and c > d" 这种正文不会被吃掉。**清洗在剪 500 字之前**，于是 `summary_truncated` 数的是读者会看到的字数而不是标记语言的字数；`injection.scan` 同步改吃清洗后的文本（扫一份读者看不到的字节串等于漏报，测试用一段藏在 `<div>` 里的指令钉住）。顺手补一个同族的洞：Atom `type="xhtml"` 的 summary 正文在**子元素**里，`child.text` 看不见它，整条摘要此前静默为空。500 字预览预算、`url`/日期字段一寸未动
- **内网地址被拒时，那句话在教一条本来就写对的 URL 怎么写（2026-10-08，真实使用模拟刀3）**：`http://127.0.0.1:8080/admin`、`http://localhost:6379/`、`http://[::1]/`、`http://10.0.0.1/`、`http://0.0.0.0/` 一律被拒——拒得对，一个包都没发。错的是随后那句建议：web_read / web_request / web_act 落进 `_invalid_request_result` 的**默认模板**（"Correct the argument and call again — web_read takes an absolute http(s) URL"），而这条 URL 本来就是绝对 http(s)，那句话把读者推去改一个本来就对的东西（`_invalid_request_result` 自己的 docstring 就立令禁止这类话）；web_crawl / web_discover 退得更远——"Fetch failed … retry with different parameters"，一个包都没发却劝人重试；feed_read 反倒是六颗里唯一说真话的。现在收成守卫侧的一份 `PRIVATE_ADDRESS_RECOVERY`（与 `RESOLVER_GUARD_RECOVERY` 同一惯例：判据与出路各只有一份定义），六条路共用，并指出这一类**真有**出口——`cred_defaults` 的 `allow_private`（`true` 放开回环，`['<host>']` 点名一台），同时写明云元数据段永不因此开门。配套两件：①**云元数据按规范化后的地址认族**，不再只按字符串——`::ffff:169.254.169.254` 与 `0xA9FEA9FE` 是同一颗端点，从前前者会退化成一句泛泛的 "private IP"，"任何拼法都不放行"这条保证于是取决于写法而不是地址；现在剥开 IPv4-mapped 再比一次，且这道检查仍在 allowlist 询问**之前**。②SSRF resolver guard 的 next_action 开头那句"the resolver verdict was taken once, at this process's first hostname check"是 R3刀2-B 之前的旧话——隧道段的拒绝现在会先重探，那句话正把自己新增的出路剪掉了，改为"this process fetches on a resolver verdict it took earlier"。测试：18 红 → 23 绿（五种内网拼法 × 三颗工具 + 爬/发现/信息流各一条 + 四种元数据拼法都无 allowlist 出口 + 一条反向锁"真拼错的 URL 不许被换成内网教程"）；R1刀3 那颗"共享校验路要点名调用方"的锁换了载体（改用一颗真没写对的 URL），判据一字未松
- **summary 标签收在一个需要宾语的词上（2026-10-08，真实使用模拟刀4）**：上一批把 `msg[:80]` 改成"落在词界"，治好了 `…can compile (S` 那种半截 token，却留下另一种坏：`…stored credentials for`（悬空介词）、`…POST / PUT / PATCH /`（悬空斜杠）、`…Valid values:`（冒号后面正是那串合法值——被剪掉的正是解药）。一轮真宿主调用里三处。词界只保证不剪断一个 token，不保证那句话说得完；而一个以 "for" 或 ":" 收尾的标签不是"短"，是承诺了一个自己没有的东西。现在按**子句**边界剪：句末（`. `）优先，其次子句分隔（`; ` ` - ` `, ` `: ` ` (`），两级都没有才退回词界（一条边界都没有的长句不许剪成空）。整句照旧全量在 `error` 里，`msg.startswith(summary 尾段)` 由测试钉住；上一批那条"剪口后第一个字符必是空格"的锁一字未动——它测的那句正好没有子句边界，走的是回退支
- **设计过的参数拒绝仍从 transport 兜底裸逃，`[:300]` 剪掉的正是解药（2026-10-08，真实使用模拟刀1）**：`web_crawl` 传一颗它不收的参数（如 `max_content_chars`），调用方拿到的是裸 `{"error": "Unsupported argument(s) for web_crawl: [...]. Supported: ['cache_ttl', … 'resume_token', 'schem"}`——没有 summary / content_ok / next_action / diagnosis，而支持列表全长 354 字符，被剪掉的尾巴正是 `schema / search / selector / sitemap / timeout / url`，即"那我该怎么写"。`web_act` 的 `fill` / `screenshot` 形状错同病（`{"screenshot": true}` 实测只回一句裸文本）。transport 兜底的注释写着"走到这里说明这不是 dhole 设计过的拒绝"——判据没错，错的是这类拒绝一直没有自己的出口。现在它们抛 `ArgumentRefusal`（**ValueError 的子类**：`_dispatch` 照旧抛，CLI 与全仓十处 `raises(ValueError, match="Unsupported argument")` 的锁一字未动），只有协议边界 `call_tool` 认识它并折成标准 invalid_request 信封，每条自带与其处境相符的 next_action（陌生键 / 搬了门的键 / 解 options 袋子 / 动作形状，四种说法四种出路）。分层守住：信封只在边界生成，`_dispatch` 以下不认识信封。经**真 SDK in-memory 回路**（InMemoryTransport + 真 ClientSession）钉住"整份支持列表到达"这一条
- **裸 MCP 客户端的缺参调用把 KeyError 当 error（2026-10-07）**：`web_crawl {resume_token}`（无 url 键）经裸 MCP 客户端（`ClientSession.call_tool` 不做客户端校验）调用，返回体就是 `{"error": "'url'"}`——KeyError repr 当 error 文本，无信封无 next_action；`web_discover` 缺 url 炸成 TypeError；`web_crawl {url: ""}` 的空串不被拒，静默落进 plan 校验链。宿主按 wire schema 的 required 拦缺参调用、碰不到这些分支，但服务端不能假设客户端会校验：dispatch 层新增必需键表（web_crawl / web_discover 的 `url`、web_search 的 `query`、doc_parse 的 `file_path`），缺席或空串一律回与「多余参数」同款式的标准 invalid_request 信封——error 点名缺哪个并列出该工具接受的全部参数名，`next_action` 教传什么重试，一个请求都不发。方法体自检信封更优的 web_read / web_act / web_request / feed_read 不进表；守卫测试把表与 wire 的 required 声明钉在一起防漂移
- **next_action 说的话与 error 对不上（错误指引家族，2026-10-07）**：三类同病一个修法。①**tavily/bocha 缺 key**：error 正确点名 `DHOLE_TAVILY_API_KEY` 未设，next_action 却教「Rephrase (more specific…)」——换说法修不了一把不存在的钥匙；现在 next_action 从 error 里提出 env 名、指向「设好重启宿主」，两份缺 key 文案（单引擎 raise 与 `search_json` raise）都认。②**共享校验路的工具名**：web_read / web_act / web_request 三颗共用的 URL 校验拒绝永远念 web_read 的样板；现在工具名跟着调用方走（`_invalid_request_result` 新增 `tool` 占位，actions 到场必来自 web_act、非 GET method 必来自 web_request）。③**SSRF 永久边界不再给假出路**：云元数据端点与 DNS rebinding 服务任何拼写永不放行（allowlist 也进不去），next_action 却说「Correct the argument and call again」——改参数永远不可能成功；现在给专属句「No argument fixes this — it is a design boundary」。④**同刀附带**：`_rank` 把空结果读成「重排器缺席」——rerank 对空输入一律答 None（没有东西可排），与重排器在不在无关；重排器暖着的进程里，空结果的错误响应会带「the reranker has not run in this process yet」的假话出去。空输入不再产生任何缺席说明，「真没跑过才说」由分支结构保证

### 安全

- **fake-ip 判定不再关掉一整层，一颗域名也不再决定整机（2026-10-08，评审R3刀2）**：三件事都往**严**的方向。①**合取**：`probe_fake_ip` 从前只看 example.com 一颗的答案，而那颗决定整台机器的 SSRF DNS 复验层——敌意解析器毒化一颗（或 hosts 钉住一颗、某条 DIRECT 规则漏掉一颗）就能让这一层对整机停摆。这条路径 2026-10-06 记在 F-C2-J2 判决里当"可选加固"，今天补上：两颗独立注册的域名（example.com / example.org）**都**答进隧道段才判 fake-ip；只有一颗答得出算 unprobed，不确定从来不是放宽的理由。②**按段豁免**：判成隧道从前等价于"这一层不看了"——公网名解析到 `127.0.0.1` 或 `169.254.169.254` 也不再有人复查；现在复验照跑，被豁免的只有 `198.18.0.0/15` 与 `fc00::/7` 这两段（隧道确实会发的地址），同一个名同时给出 ULA 与回环时回环那一条仍然算数、不许在第一段停下。③**中途自愈**：会话中途才挂上的 TUN 让缓存判词过期，现场是"每个公网 URL 都被拒、唯一出路是重启宿主"（而重启丢掉内存里的会话与凭据）；现在一次**因隧道段而起的拒绝**之前会重探一次（每 60s 至多一次），采纳后的放宽照旧在 `ssrf_recheck` 里可见。危险段的拒绝不重探（拒对了没什么可自愈），两类拒绝说的话也分开——隧道段的拒绝不许说"这种地址不是代理会发的"。配套：镜像 `resolver_probe.json` 把两颗探测域名都列出来（只印一颗等于让 doctor 替一个更窄的证据说话）、`--doctor` 那一行按新事实重写并守住 280 字符行预算（写长了被 `_short` 打掉的正是头，测试逐支量过）、`DHOLE_SSRF_DNS_RECHECK` 依旧注销且无人读
- **host 白名单落到机制而非配置**：主机名单在 route 层执行（今天那份存在 `cdp_hosts.json`，env 那颗 `DHOLE_CDP_HOSTS` 已注销）——attach 后页面 JS 发起的子资源请求同样被拦，不只是导航；抓取入口再快失败一次（`cdp_host_not_allowed`）。白名单外的导航拿不到你的认证 cookie
- **attach 事件全部留痕**：写进 `DHOLE_USAGE_LOG` 的审计通道，响应回带 `cdp_attached: true`——每次借用你的登录态都可见
- **attach 不碰你的浏览器指纹**：跳过 stealth 注入 / cookie 写入 / 拟人行为（往真实 Chrome 里注入指纹脚本反而制造与真实指纹不一致的暴露点）；`close()` 只断开连接，不关你的浏览器
- attach 走一次性会话，不进自动会话池，不与其他调用混用
- **`web_request` 的写操作确认门（G25 评审 Blocker）**：POST / PUT / PATCH / DELETE 现在需要 `confirm=true`，缺了就在闸门处整体拒绝（一个请求都不出，`next_action` 指回「问过用户再重发」）——与 `web_act` 的 `submit` 闸、`sys_state` 清会话闸、CDP 首挂闸同一语义。此前这扇门是注册面上唯一没有确认门的写路径，而 `cred_defaults` 存的凭据按主机自动随行：被页面正文（间接注入）诱导的 agent 可以直发已认证写请求，服务端没有拦截点。HEAD 是探测，不需要 confirm。`confirm` 因此进 schema 与 `_TOP_LEVEL_ARGS`——藏在签名里的 confirm 会被 dispatch 当表外名拒掉，拒绝消息教的重试根本到不了闸门
- **`web_screenshot` 注解不再撒谎（G25 评审）**：`readOnlyHint` / `idempotentHint` 改为 false——`save_to` 会往调用方指定的路径写盘（parents created），`anti_detect` 的 auto/aggressive 会替 Cloudflare 挑战页作答，两件事都不是只读；注解是宿主免审批的依据，等于把写原语放进免审通道。`_resolve_save_to` 的后缀校验同步从「已存在的非图片文件」扩到「任何非图片命名」（新建的 notes.txt / task.bat 一样拒），代码这才与描述「a non-image file name is refused」对齐
- **HTTP 传输面（G25 评审）**：streamable-HTTP 传输现在把 `security_settings=TransportSecuritySettings(...)` 传进 mcp SDK 的会话管理器——此前这层不传 settings，SDK 缺省**显式关闭** Host/Origin 校验，DNS rebinding 的浏览器页面与本机任意进程都能直呼全部工具。allowed_hosts / allowed_origins 按 `--host` 显式构建（SDK 的空列表会拒掉一切 Host）。另加可选鉴权 `DHOLE_HTTP_TOKEN`：设了之后每个 `/mcp` 调用必须带 `Authorization: Bearer …` 或 `X-Dhole-Token`（常量时间比较），未设保持开放行为、`--host` 绑非回环时启动打警告——「要不要鉴权」从「开 HTTP 就全开」变成一次主动选择
- **降级重定向不再带凭据（G25 评审）**：`fetcher._hop_headers` 的凭据同源判定从「同主机」收紧为「同 scheme + 同有效端口」（生产调用点传 `origin_url`；实测 `https://api.example.com` 302 到 `http://api.example.com` 时 Authorization 原样走明文线）。浏览器按 Fetch 规范视异 scheme / 异端口为跨源并剥掉凭据，HTTP 层现在同一口径
- **连接期总账 33,099 → 33,460（+361）**：写门的 `confirm` 属性与描述句必须住在连接期（理由见第一条），`CONNECT_CEILING` 随之上调——这是棘轮第一次上抬，理由写在测试注释里；README 与 `sys_guide(topic='config')` 的价目表同步到现值
- **CDP 授权的撤销粒度收到主机（G25 评审）**：此前撤销只有一条路——删整个 `cdp_hosts.json`，授权 10 台想撤 1 台就得全部重来；代码自己早在注释里许诺「一个像样的出口要等 B5」，B5 落地后出口却没接上（`sys_state` 的清除词表里没有它）。现在 `sys_state(clear=['cdp:host=<name>'])` 撤一台、`clear=['cdp_hosts']` 撤全部（都走与 sessions 同一道 confirm 闸——丢掉的后果要到下次 attach 那台主机才暴露），并入 `all`；名册里没有的主机按「未知 token 即报错、绝不静默 no-op」处理。四处「删文件即撤销」的文案（web_read 的 confirm 参数、CDP 拒答回执、browser 围栏错误、`dhole doctor` 修复行）与手册同步改为教工具路径
- **`sys_state` 名册补全（G25 评审）**：它自称「本机状态名册」，`cdp_hosts.json` 与 `config.json` 两间房却不在册。现在默认 census 带上 CDP 授权名册（主机名，`show=['cdp_hosts']` 可点名）；`show=['config']` 报 config.json 里设过的旋钮**名字**——值可能带着 secret，值走操作员侧的 `dhole config list`；`clear=["config"]` 被点名拒绝（show-only：旋钮的另一扇门在 `dhole config unset`，留两扇会各自漂走的门是本仓自己裁决过的事）。`sys_state` 描述同步收紧措辞（顺带收紧 `ANTI_DETECT_DESC`，它在 wire 上是两份）作对冲，总账 **33,460 → 33,452**，`CONNECT_CEILING` 随实测下调（棘轮只准往下，这次是真往下）
- **凭据默认只在内存里（G25 评审 H4）**：session cookie 与 `cred_defaults` 存的凭据此前**明文落盘** `~/.dhole/sessions.db` / `identity.db`——0600 在 Windows 上是空操作（NTFS ACL 说了算），同步盘、备份外带、任何同用户进程都能直接读到 cookie 值与 Basic 密码。现在默认 store 是**内存**（SQLite `:memory:`）：文件根本不创建，进程结束即清，「重启后登录态还在」这个 16.x 的承诺变成显式选择；升级机器上的旧明文库会在 store 首次使用时**吸收进内存并删除文件**（内存模式）或**原地重加密**（落盘模式）。要跨重启保留登录态/默认身份，设 `DHOLE_PERSIST_CREDENTIALS=1`：值经 **Windows DPAPI** 加密后才落盘（ctypes 直调 `CryptProtectData`，纯标准库零新依赖，密钥绑定本 Windows 用户，存盘形如 `dpapi:<base64>`），旧明文值首次使用即重加密。边界如实说：非 Windows 平台没有 DPAPI，这颗旗**宁可拒存也不回退明文**——保持内存并在 `dhole --doctor` 报 fail（doctor 新增「credential storage」一段报当前策略，落盘模式的说明里印明「防文件被拷走，不防同用户进程」）；`config.json` 里的 secret（API key）仍是操作员侧明文（README 已披露，照旧用 env 块），本轮不动。连接期总账零变化——行为开关住在手册与 doctor 里，不占 wire
- **抓回正文里的"指令形状"点名标记（G25 评审 阶段4）**：抓回的页面/订阅摘要是**数据**，但数据可以长成指令的样子——"忽略之前所有的指令"、"ignore previous instructions"、"你现在是…"、页面文本里直接出现 `web_read(...)` 调用形状、`scheme://user:pass@host` 的带凭据 URL、`curl --data … https://…` 的外传命令。这些文本落进调用方 agent 的 transcript，被当成指令执行的那一刻，抓取层就成了注入的运输通道。现在三个正文落点（`web_read`/`web_request`/批量/缓存/存档共用的响应信封、`web_crawl` 的每页、`feed_read` 的每条摘要）都过一遍**确定性启发式扫描**（纯 stdlib 正则、零模型，`dhole_mcp/injection.py`：指令覆盖中英 / 角色劫持 / 工具点名（13 颗工具原名）/ 带凭据 URL / 外传命令五类形状），命中时响应带 `untrusted_content: {hits: [{kind, excerpt, count}], total}`，summary 追加一句。**边界如实说**：这是标记不是拦截——dhole 不删改不遮蔽任何正文，"算不算指令"的裁决归调用方语言能力；一篇*讲*提示注入的安全文章会命中同样的形状（启发式标记，不是判决）；新措辞会漏，钉的是实测载荷里反复出现的形状，不是会过时的攻击清单。摘要有界（≤90 字符），标记本身不成为新的注入面；干净时字段不上线（省略表登记）。手册 fields.md 与 README 同步教纪律：正文一律按不可信数据对待仍是第一道防线，本标记是第二道
- **连接期总账 33,452 → 33,270（-182，棘轮第二次真往下）＋ 三个零 wire 成本的口径修正（G25 评审 阶段5）**：①**描述逐字去重**——`web_act.anti_detect` 的 168 字符阶梯与 `web_read` 逐字相同（tools/list 上纯重复），换短指针（完整阶梯只住 web_read 一处，-83）；captcha 纪律的墙清单归 `web_screenshot` 一处（-41）；`doc_parse` 的扩展名清单归 `file_path` 参数（-58）。实测与评审时代估计（~966）的差如实说明：评审基于 33,099 时代的文本，阶段 1-4 的编辑已让 timeout/ignore_robots 等组分叉并各自携带信息，硬凑那个数就要造重复。②**`web_search` fetch_content 每页帽 8000→3000**（附注三/M4）：3 页满打满算 9,000 字符 ≈2.2k token 回来的是"带焦点的摘录"，不再把三页全文倒进对话；要全文走 `web_read` 的 offset 分页。③**`sys_guide` 名册补体量**（附注二/M2）：`topic` 名册每行带该卷字符数并提示"长卷先 section="——behavior 卷 3.5 万字符整卷误读是实测发生过的浪费，事前提示只住在错题回显与成功 header，连接面零成本。④**文档口径**：四处「docs 24h」旧账（README/SKILL/tools.md/recipes.md——代码真值 `_TTL_BY_PAGE_TYPE` 只有 article 6h，其余 1h）统一为「article 6h」；README 工具表删掉 17.0 已删的「条件请求」；「每次连接付一次」补多轮口径（目录整段会话在场，无缓存宿主每轮全价，`DHOLE_TOOLS` 才是每轮都省的闸）；server.py 两处旧注释（~250 tokens/all 9 tools、21,282/6,508）改实测值；引擎名册守卫扫描面从单节扩到 README **全文**并钉死 `DEFAULT_ENGINES` 八颗字面（附注一/M1：判据扫不到的位置必然漂移——env 表里「默认池 6 颗」的旧账就是这么活下来的）。

- **阶段7（G25 评审 A1/A2/M10）：工具面 13 → 11，棘轮史上最大一次回落**：①**`web_screenshot` 并入 `web_act` 的截图动作链（A1，评审 High）**——两者跑同一台隐身浏览器，动作链里早就有元素级 screenshot 动作，独立工具多出来的只有四颗捕获旋钮：`full_page` / `save_to`（落盘并回报绝对路径＋字节数，text-only 宿主的出路，也是 2MB 内联帽的绕行道）/ `image_type` / `quality` 现在是**动作对象里的键**（`{"screenshot": {"full_page": true, "save_to": "..."}}`），`wait_selector` / `anti_detect` / `timeout` 本来就在 web_act 上；坏 `save_to` 的拒绝提前到动作归一化层（浏览器冷启动之前）。截图像素的内联通道、`decode_qr` 本地解码、验证码纪律（≤3 次即转交，墙的清单搬进 `sys_guide(topic='behavior')` 按需付费）全部随迁；捕获失败经 `_screenshot_error_message` 净化表整形后落在动作回执行里（helper 三个与 `_BROWSER_ERROR_HINTS` 从 server.py 搬进 actions.py——它们此后唯一的消费者是动作执行器）。web_screenshot 的只读注解问题（阶段 1 已修）随工具一起消失：门只剩 web_act 一扇，注解诚实由它继续承担。②**`web_resolve` 下线（A2，评审 Medium）**——短链落点由 web_read 覆盖（HTTP 层同一入口跟随重定向并回报落点，meta-refresh 跳数同源），验活由 `web_request(method='HEAD')` 同次数覆盖；它连 allow_private scope 都不读，自己教的「逐条 pre-screen」恰是 nested 注释点名要消灭的 N 次反模式。两个旧名（作为工具名或旧参数键）撞进来都在拒答里教到新的家（`_REMOVED_TOOL_HINTS` / `_MOVED_TO`）；注入扫描的形状名单**保留**这两个名字——页面指挥 agent 去调"已经不存在的工具"恰是最值得点名的形状。③**主代码净删约 400 行、连接期总账 33,336 → 30,322（-3,014）**：instructions 减两条死路由句（2,636 → 2,580），web_act +301（旋钮进描述），web_request +43（验活指针改指 web_read）；`CONNECT_CEILING` 随实测大幅回落，理由写在测试注释里；价目表（configuration.md）与 README 同步到现值，11 颗、~7.3k token。④**CI ＋ 描述哈希锁（M10，评审 Medium）**——`.github/workflows/ci.yml`（~40 行，push/PR 跑 pytest，windows runner：DPAPI 与 NTFS 措辞是功能不是巧合）让棘轮与全部守卫第一次有了强制执行点；`tests/tool_defs_lock.json` 钉住 `_TOOL_DEFS` 的 sha256，tools/list 的任何字节变动都必须显式改锁——锁文件由脚本口径生成，作者手抄丢过开头的 "3"，测试注释里立了纪念碑

- **阶段6（G25 评审）：变更探测有了「问法」＋ 四个收口**：①**`extraction_type="check"`（G1，评审 High）**——「这页变了吗」不再等于「把页面再取一遍」：基线就是 dhole 自己落的 html 缓存（envelope 里带着源站校验子），带校验子回源，304 即站方亲口的「没变」（几百字节替掉整页重取），200 才下载正文并与基线逐字比对、首个分叉给 ≤200 字符窗口；无基线先按普通抓取落一次、如实回 `changed: null`——第一次见面不冒充「没变」。`check` 只进 web_read 的枚举（web_act / web_request / web_crawl 不动），与 `actions=`/`schema=`/`nested_selector=` 的互斥在闸门处拒绝、一个请求都不出。连接期总账 33,270 → 33,336（+66：枚举 10 + 指针描述 56，评审原文把这笔记在预算账内），`CONNECT_CEILING` 随之上调、理由写在测试注释里。②**schema 重抽吃缓存（M3）**——同一页换个 schema 重抽，第二遍不再付整页网络：schema 抓取以 html 落缓存，第二遍同键（url + html + selector）命中，`cache_ttl=0` 与条件调用「不吃自家缓存」的语义照旧不被偷走。③**搜索结果带 `official` 标记（G4）**——每行按 `classify_source` 标出是否官方来源，agent 挑源时多一个免费信号；输出侧字段，连接期零成本。④**CDP 死端点指路（G5）**——`cdp_url` 指向的端口连接被拒时，错误尾巴教一句「remote debugging 随浏览器进程死：带上同一个 --remote-debugging-port 与 --user-data-dir 重起 Chrome 再来」。⑤**预热按启用面收口（G6）**——浏览器预热只在 web_read/web_act/web_screenshot 任一启用时才跑，reranker 预热只在 web_search 启用时才跑：`DHOLE_TOOLS` 裁掉的面不再偷偷付启动成本

- **注入扫描前先归一不可见字符（零宽绕过）**：G25 阶段4 的注入启发式用纯正则匹配"指令形状"，而匹配前不做任何归一——攻击者把零宽 / 双向控制 / 变体字符插进关键词中间（`ig\u200bnore previous instructions`，字形看着一样、正则写的是**可见字形**），五类形状**整段被静默绕过**，而"没命中"与"这页干净"字节相同。现在 `scan()` 在掩码与匹配前先归一：`_INVISIBLE_RE` 覆盖软连字符（U+00AD）、阿拉伯字母标记、蒙古元音分隔符、ZWSP/ZWNJ/ZWJ/LRM/RLM、bidi 嵌入与覆盖（U+202A–U+202E）、词连接符与不可见运算符（U+2060–U+2064）、bidi 隔离（U+2066–U+2069）、BOM、行间注释符；归一取两个视角——**整字去除**（治词内插入 `ig\u200bnore`）与**替换为空格**（治词间插入 `ignore\u200bprevious`），逐类在各视角上试、取命中更多的那个（同数取整字去除：一份文本里两种插入都可能出现，取并集会把同一处数两遍）。正文里没有不可见字符时零额外开销（先判 `_INVISIBLE_RE`，不命中就直接扫原文一份）。边界一字未变：这是**标记不是拦截**，dhole 不删改任何正文

- **注入扫描补掉同形与分隔绕过（NFKC + 同形字表 + 分隔符）**：上一条堵的是"看不见的字符插在词中"，同一类绕过还有三种没堵 —— **全角字形**（`ｉｇｎｏｒｅ previous instructions`）、**西里尔/希腊同形字**（`іgnore` / `οverride`，字形看着一样、码点不同）、**分隔符**（`ignore-all` / `ignore_all` / `i g n o r e`）。现在 `scan()` 在掩码与匹配前先做一次 `_fold`：`unicodedata.normalize("NFKC", …)`（治全角）＋一张显式同形字表（`str.maketrans`，西里尔 а/в/е/к/м/н/о/р/с/т/у/х/і/ј/ѕ/ԁ/һ/ԛ/ԝ/ѵ 与希腊 α/β/ε/ι/κ/ν/ο/ρ/τ/υ/χ 映射到 ASCII）＋把五类形状正则的词间分隔由 `\s+` 放宽到 `[\s\-_]*`（并去掉名词前的 `\b`，让 `ignoreall` 这类粘连写法也命中）。误报面保持：菜谱正文、俄语正文、含 "forget" 的普通句子均不命中；ASCII 文本折叠恒等（零额外代价）。边界一字未变：**标记不拦截**。钉住 `tests/test_injection_scan.py::TestTheObfuscationBypasses`（8 条）

- **本地文件与 SSRF 红线六刀（2026-10-07）**：①IMDS 门——规范化地址分支先于白名单拦截云元数据主机，裸名 `metadata`、整数 / 十六进制字面量（`1684301000` / `0x646464c8`）与阿里云备用端点 `100.100.100.200` 全部 SecurityError；②`doc_parse` 本地文件黑名单 +6（`~/.dhole`、`~/.config/gcloud`、`~/.config/gh`、`~/.azure`、`~/.kube`、`C:\ProgramData\ssh`），Windows 前缀改 %SystemRoot% 派生（装在 D 盘不再漏）；③`save_to` 过同一份黑名单（realpath 后判定，浏览器冷启动之前拒绝；`.png` 的覆盖语义不动）；④NTFS ADS 流路径（`file.txt:hidden` 的冒号流语法）拒绝，仅 nt 启用——POSIX 上冒号是合法文件名字符，报告给的无条件判定在本仓不成立；⑤hosts 解析首行赢（与 glibc / Windows 语义一致），黑洞钉位不再被后行翻成元数据钉位；⑥`cred_defaults` 错误面收口——auth.type 固定句不回显、cookies 非可迭代给形状句、拼装点过脱敏（全库最后一个不过脱敏的错误面）
- **注入扫描前移到被丢弃的正文（2026-10-07）**：扫描此前挂在截断 / focus 分块之后——被丢弃或落在窗口外的段落里的注入形状以干净面貌进转录。现在对分块前的整页全文扫描，命中挂结果；不经分块的路径保留原位扫描作后备。`fetch_content` 扇出的每页（`fetched_pages`）携带同一标记（命中才挂键，干净页零字节）。`injection.py` 的归一化零改动，既有判定不受影响
- **`--http` 无 token 的启动警告不再豁免回环（2026-10-07）**：同进程双实例就是两个客户端，零配置读走彼此的存储。警告删掉回环豁免并把共享内容说出口：同一端口共享一份凭据存储 / 会话 / 缓存，token 是端口门禁不是主体隔离——要主体隔离就分端口 / 分进程
- **落在提交型控件上的 click 绕过了 submit 审批闸（2026-10-07）**：`{"submit": N}` 无 confirm 正确暂停，但同一个按钮换 `{"click": N}` 就直接提交——quotes.toscrape.com/login 实测无 confirm 登录成功，闸只认动作名不认动作效果，而安全红线管的是"提交须经确认"这个后果。现在 click 执行前对目标做一次提交型判定（`input[type=submit|image]`、form 关联的缺省 / submit 型 button；`role=link` 的按钮状链接除外），命中即走 submit 同一道闸：elicitation 能力的连接上人点头才放行，decline / 问不到人 fail-closed（`confirm=true` 救不回来）；无能力连接上 confirm 落在 click 的 dict 上（`{"click": {"index": N, "confirm": true}}`，字符串简写不带键、暂停行教它改写）。放行的回执带 `submitted: true` 进写审计；普通控件与探测不出的元素一字不改（探测失败不发明闸：元素等不到原样超时）。wire 零变化，手册 Page interaction 一节同步

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
