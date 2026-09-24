# 更新日志

本文件遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的格式：每个版本按 **新增 / 变更 / 弃用 / 移除 / 修复 / 安全** 的顺序分组，只列有内容的那些；日期用 ISO 8601；最新版本在最前。版本标题在结尾有对应 diff 链接（未打 tag 的版本不链接）。本文件在 11.1.8 重新建立，更早的发布未回溯补记（见 `git log`）。

> 自 13.14 起本仓库为个人衍生作品，版本号是自己的序号、不承诺语义化版本，与上游版本不可比；`src/dhole_mcp/__init__.py` 的 `__version__` 是唯一权威来源。

## [15.1] - 未发布

第四轮（约 60 次调用、8 工具全覆盖，记作「报告4」）与第五轮（`dhole_fix_prompt.md`，记作「报告5」）外部实测的处置。两份报告各用**自己的**编号体系、与前几批同名不同物，所以回归测试按**症状**命名而不是照抄编号（`test_bug_report2_regressions.py` 的文件头说明了理由）。第六轮（`2026-09-24-dhole压测`，91 场景 / 8 工具全覆盖，记作「报告6」）同样按症状命名（`test_loadtest_report_regressions.py`），并在文件头记下三处**报告本身判断有误**的地方。

### 修复

**报告5**

- **`smart_crawl` 的 `path_include`/`path_exclude` 把字符串前缀当成了路径子树**（P0）。两处调用点都是 `path.startswith(p)`，同一组链接实测四种失败、**全部静默**：`"docs"` 与 `"/docs/*"` 把整站过滤成 0 条；`"/docs"` 多留了 `/docs-old/legacy`、`/docsomething`；`path_exclude=["docs"]` 什么都没排除。最糟的是第一种 —— 起点页永远会被抓，所以现象是「抓了 1 页、`error` 为空、`summary` 说 `1 content_ok`」，调用方只能读成「这站就一页」，而 `docs` 恰好是最自然的写法。
  - 新契约：模式命名一个**路径子树**，在路径段边界上匹配（`/docs` 与 `/docs-old` 是两棵不同的树）；`'docs'`/`'/docs'`/`'/docs/'`/`'/docs/*'` 等价。只接受尾部 `/*` 这一种通配，其余（`'/docs?'`、`'/api/*/v1'`）**直接报错** —— 把通配符当字面量匹配的结果就是上面第一种失败，报错能让调用方一次改对。校验前移到任何网络动作之前，并配一条专门的 `next_action`（不能复用 `classify_network_error` 的「try a different source」，这次一个请求都没发）。
  - 两处调用点合并为一个实现（`path_allowed`）。wire 描述从 `(path prefixes)` 改成 `(path subtree: '/docs' keeps /docs and everything under it, NOT /docs-old)` —— **「prefix」这个词本身就在教调用方用 `startswith` 思考**，而那个心智模型正是 bug 的来源。测试 12 条，变异 3 组全红。

- **`parse` 读 GBK 文件返回乱码却报 `content_ok=true`**（P0）。`.csv`/`.html` 硬编码 `utf-8, errors="replace"`，中文 Windows 上 Excel 默认导出的 CSV（GBK/GB18030）解出来是一串 U+FFFD，而 `content_ok` 仍是 true、`error` 仍是空 —— 引用它必然出错。复现：90 字节 GBK CSV → `"| ���� | ���� |"` + `content_ok:true`。
  - 修法不是再猜一次编码，而是把**候选顺序**与**损伤判定**显式化，落在新的 `charset.py`：BOM → `encoding` 参数 → 头部 NUL → 严格 UTF-8 → 严格 GB18030，都不严格通过时按损伤打分取较小者。`metadata.encoding` 回报实际生效的字符集；**解出来仍带损伤时不当作成功** —— 正文照常返回（那正是调用方要看的），但 `error` 置 `encoding_undecodable`、`content_ok=false`、`next_action` 指向 `encoding=` 重试。
  - 边界是量出来的：GB18030 是 GBK/GB2312 的超集，7/7 个 GBK 样本零损伤。**Big5 可检出**（落 PUA 私有区），**Shift_JIS / EUC-KR 不可** —— 它们被 GB18030 严格解成「看起来合理的中文且损伤为 0」，没有廉价字节特征能区分，只能靠 `encoding=` 显式指定；这条缺口写进 README。**cp1252 刻意不进链**：它能把任何字节解成零损伤的拉丁文本，一进链损伤信号就彻底失效（Big5 会被洗白），代价是 Latin-1 文件会被解成 GB18030 并标记有损伤 —— 取舍方向是**看得见的失败优于看不见的错误**。
  - 顺带修掉一个此前无人发现的脆弱点：`parse.py` 的降级路径曾 `from dhole_mcp.fetcher import _misdecode_score`，而 `fetcher` 顶层 import `primp` —— 「`parse` 不依赖抓取栈」这个承诺在没装 primp 的环境会直接 ImportError（本轮实测撞到）；损伤打分与错码标记现归 `charset.py`，由 AST 守卫钉住。CSV 分隔符也顺带探测（Excel 在部分地区写 `;`，按逗号解会让整行挤进第一列 —— 静默错误，不是空表）。测试 14 条。

- **`max_content_chars` 静默忽略调用方的值**（P1-5）。旧代码 `if not isinstance(v, int): v = MAX_CONTENT_CHARS` —— 非 int 一律静默换成 40000。而「客户端把数字字符串化」是真实行为，于是 `max_content_chars="2000"` 静默拿到 40000：**20 倍上下文超额，以普通 200 的形式交付**。姊妹缺陷 `offset` 负数会 `text[-5:]` 从末尾切片，即「返回错误结果却报 200」。
  - 处置与 `_coerce_options` / `_strict_options` 已在上一层用过的规则一致：**转换能转换的、报出不能转换的**。`None` → 默认值；bool → 报错（`True` 是 `int` 子类，不拦就静默变成「1 个字符」）；能解析成 int 的字符串 → 转换并照调用方意图执行；其余 → 报错并写出实际类型。新增 `_coerce_int_arg` 承载这条策略。
  - 钳制保留但不再无据可依：`[500, 200000]` 上限刻意保留（硬顶优于给调用方一个难看的 parse error），但 200000 **此前从未写进 wire**（描述只说 `min 500`），现在写成 `range 500-200000`（+9 字符，连接期总量 12,601 → 12,610）。`offset` 负数**改成报错而不是钳到 0** —— 钳到 0 是静默纠正一个没有合理解读的值。`next_action` 不再张冠李戴：参数错误此前复用「smart_fetch 要传绝对 URL」那句，把调用方指向唯一本来就正确的那个参数。
  - 测试 20 条，变异 3 组全红。**同类但未动**：`cache_ttl`/`timeout` 传字符串会抛 TypeError（响，但不可行动）；`max_content_chars_per`/`max_total_chars` 由 `crawl.py` 的 `int()` 转换。是否把 `_coerce_int_arg` 铺到这几处，单独决定。

- **`screenshot` 把 Playwright 的原始日志原样发给 agent**（P1-3）。`raise captured["error"]` 直接抛出，`call_tool` 把它 `str(e)[:300]` 后发出去，agent 收到的是 `Timeout 30000ms exceeded` 加一整块 `Call log:`（`waiting for fonts to load...`）。这是 `is_error` 结果，调用方拿它的唯一用途就是决定下一步 —— 而日志块描述的是**驱动内部**的步骤，没有任何参数能影响它，还占满截断预算、把唯一命名了故障的那一行挤在中间。截断本身又多一层：`[:300]` 会切在日志中途，产出「看起来完整、实则半句」的错误。
  - 新增 `_screenshot_error_message`：保留**异常类型 + 首行**，丢掉日志块，按首行给一条可行动的提示（超时 → `options.timeout` / `options.wait_selector`；会话已死 → 重调或 `close_session`；缺二进制 → `playwright install chromium`）。**提示只匹配首行**：日志里出现 `timeout` 而首行不是超时时给超时建议，是自信的错误指引，比不给更糟。原始异常不丢，抛出点先 `logger.debug` 留给运维侧。
  - 长度是契约的一部分：`call_tool` 发 `[:300]`，最坏形状实测 267 字符，不会被切在半句。空消息回退为 `(no message)` 而非重复类型名。wire 一字未改，预算不受影响。测试 12 条（含一条走 `_dispatch` 的端到端），变异 6 组全红 —— 其中「空消息不回退」第一版**漏网**：原断言只查 `startswith("Exception:")`，而字段留空时输出是 `"Exception:  - ..."`，照样以 `Exception:` 开头，测的是错的东西。
  - **同类但未动**：`smart_fetch` 走 stealthy tier 时 `error` 同样是原始 Playwright 文本（7 个 `redact_api_key(str(resp)[:200])` 落点），实测 200 字符且切在半句。但它比 P1-3 轻：`next_action` 仍可用（`classify_network_error` 匹配**首行**，首行在截断中幸存），可行动的通道没坏。不改的原因是该文本参与 `classify_network_error` → `_agent_hints` 的分支选择，牵动一整片既有断言，需要单独评审。

**报告4**

- **`content_ok=true` 但正文是失败页**。x.com 回 200 + `Try reloading`、douyin.com 回 200 + `Please wait...`（249 字符），两者 `content_ok` 都是 true —— 判据只有 `2xx/3xx + 无 error + 正文非空`，而这类页面不含任何验证/登录措辞，`_is_bot_wall` 与 `_is_auth_wall` 都够不到。新增 `_is_soft_failure`（明确失败措辞表 + 600 字符门限），命中时把 `soft_failure_detected` 写进 `error`、`content_ok` 变 false。
  - 与 15.0「软 404 检测评估后不做」的关系：那次判据是「200 + 正文 <1KB」，62 条样本里 0 例、朴素判据 80% 误报；这次窄得多（**必须命中具体失败措辞**，长度门限从 1KB 收到 600），且有了两例真实反例。它仍是词表驱动 = 打地鼠，**不覆盖**本次同时发现的 Google 法语屏蔽页（无失败措辞、只有地域屏蔽措辞），那条维持现状等更多样本。
  - 必须排在 `_is_js_shell` 之前，否则这两页会被判成 JS shell，`next_action` 就让 agent 去烧 30~40s 的浏览器升级换回同一个错误页；且不触发 archive 回退（新 error 不以 `all_tiers_failed` 开头、status 200 不在回退状态码里），失败页不会被悄悄换成 Wayback 的旧版本。

- **错误状态的 `next_action` 被「截断」抢走**。实测 Wikipedia 404 页返回 `page truncated... offset=...` —— 根因是截断提示排在 `_agent_hints` 的 elif 链最前，而 404 页的正文足够长会被 `max_content_chars` 截断，于是 agent 被引导去翻一个不存在的页面的下一页。现在截断提示加 `status < 400` 约束，404/410、403、429 各有自己的指引。顺带换了一处契约测试的锚点：`test_error_result_content_ok_false` 原断言 `next_action` 含 "failed"（那是网络错误兜底文案的措辞），改为断言含 "404" —— 钉的是「有可行动的指引」这个行为，不是某个恰好出现过的词。

- **声明的 charset 与字节不符时不再产出 mojibake**。实测 `you’ve → youâ€™ve`、`· → ??`。根因是 encoding **只**从 Content-Type header 取、页面自己的 `<meta charset>` 完全不参与、也没有一致性校验 —— Apache 默认发 `ISO-8859-1` 而内容是 UTF-8 时声明编码无条件获胜（复现：写了 `<meta charset="utf-8">` 也照样乱码）。修法不是猜字符集，而是**两种解码各打一次分、取更干净的**（`_decode_html_bytes`）：错声明的 UTF-8 会留下 U+FFFD 或 `Â/Ã/â€` 前缀，而真·非 UTF-8 内容按 UTF-8 解码会产生**更多**替换符，因此被保留。接进 HTML 主体路径与 JSON / old-reddit 两处直解路径；`feed.py` 与 `search_engines.py` 的解码点本轮**未动**（输入形态不同，需要各自的样本）。两个方向的变异都做过：去掉回退 3 红；改成无条件 UTF-8 2 红 —— 后者是这条修复最该防的事，中文站改坏比不改严重得多。

**报告6**

- **`smart_fetch(urls="https://example.com")` 按字符迭代，返回 19 条垃圾结果**（D-01，P1）。`urls is not None` 不校验类型，字符串进 `_smart_fetch_bulk` 后 `len(urls)=19`、`for u in urls` 逐字符迭代，于是调用方拿到 `total=19, successful=19`、每条 result 的 url 是单个字符 —— **没有任何错误信号**。
  - 修法不是把字符串包成 `[s]`（逗号算不算分隔符？无从判断），而是**报错并指向 `url=`**：单 URL 已经有专门的参数。JSON 数组字面量仍被接受（`'["a","b"]'`），因为那是若干 MCP 客户端序列化嵌套结构的真实行为，也是 `_coerce_options` 存在的同一个理由。

- **`cache_clear(all="false")` 清空全部缓存**（D-03，P1，数据丢失）。`args.get("all", False)` 把字符串原样交给 `if all:`，而**任何非空字符串都是真值** —— 意图「只清过期」的调用方得到全清。
  - 新增 `_coerce_bool_arg`，策略与既有的 `_coerce_int_arg` 一致：`None` 取默认、`bool` 直通、`0`/`1` 直通（数字型客户端就是这么写布尔的）、**字符串只认白名单**（`true/false/yes/no/on/off/1/0`，大小写不敏感），其余报错。白名单是唯一不可能在「看起来像假值」方向出错的做法。

- **`force_fetcher="magic"` 静默跑隐身浏览器**（D-02，P2）。签名声明 `Literal["http","dynamic","stealthy"]`，但手动分发器用 `args.get()` 取值，`Literal` 从不参与运行时校验；值只要不等于 `"http"` 就落进 stealthy 的 `else` —— 最重的一层（~5s + 反检测开销），以普通 200 交付。
  - `smart_crawl` 的 `options.force_fetcher` 同样补了校验：它直接转发给 `smart_fetch`，是同一个失败模式。

- **中文 `focus` 完全 no-op**（D-04，P2）。`_TOKEN_RE = re.compile(r"[a-z0-9]+")` 只匹配 ASCII，中文 query 的 token 集是**空集**，`focus_content` 于是原样返回全文 —— 无错误、无注记，与「每个块都相关」不可区分。
  - 分词改为 Unicode 感知：一般文字按 `\w` 词元（顺带救回西里尔/希腊/阿拉伯等此前被整体丢弃的文字），**无空格文字（汉字 / 假名 / 泰文等）按字符二元组展开**（`如何创建任务` → `如何,何创,创建,建任,任务`）。整段当一 token 几乎匹配不到任何东西，单字又会匹配到几乎所有东西，二元组是不引入分词器的中间解。混排（`Python教程`）在文字边界切开，得到 `python` + CJK 二元组，而不是一个谁也用不上的 token。
  - 边界写在 `focus.py` 的模块 docstring 里：**不是语言分词器**，无词干化、无词典。

- **`force_fetcher` 不在缓存键**（D-05，P2）。`_cache_key` 的 raw 串与 `_cache_context` 都不含它，于是 force=http 命中了片刻之前 stealthy 写入的条目（`cached=true, content_ok=true`），pin 被静默忽略。**危险方向是反的**：http 层抓到的 JS 壳（`content_ok=false`）一旦入缓存，后续 auto/stealthy 请求会拿到这个坏正文而**不再升级**。现在 `ff=<值>` 进指纹。

- **`max_results` 的钳制注记在缓存命中时丢失**（D-07，P3）。`_clamp_note` 由**本次请求**推导、不写进缓存行，缓存命中路径只恢复了 `_pool_health_notes`。调用方看到 50 条结果，没有任何一处说明 50 是上限而不是总数。

- **`max_content_chars` 越界静默钳制**（D-08，P3）。499 被钳到 500，响应里没有任何信号 —— 而姊妹工具 `smart_search` 是会报的。钳制本身**保留**（有文档的硬顶优于难看的 parse error，且 `range 500-200000` 已写在 wire 上），但调用方现在会在 `summary` 里看到 `max_content_chars clamped 499->500 (supported range 500-200000)`。注记走 `_ARG_NOTES` ContextVar（与 `_FOCUS` 同一套作用域机制），不新增字段，也不动 `next_action` 的「空 = 无事可做」契约。

- **`urls=["https://...", 123]` 泄漏裸 Pydantic 错误文本**（D-09，P3）。它一路走到 `ResponseModel(url=123)`，回来的是 `1 validation error for ResponseModel` —— 框架内部文本，既不说哪个元素错了，也不给恢复提示。现在元素逐个校验，报 `urls[1] must be a string, got int`。

- **`cache_clear` 每次都带完整 `engine_health`**（D-10，P3）。默认的「清过期条目」是高频运维操作，却要为搜索池的逐引擎状态（n/mean/status/verdict，~1KB）付费 —— 而它问的是内容缓存。现在只在 `engine_state=true` 时取快照，字段描述同步说明。

- **`feed_fetch` 的错误风格与信封不一致**（D-12，P3）。它抛 `ValueError`，`call_tool` 把它变成 `is_error` 结果、payload 只有 `{"error": ...}`：调用方要维护两套错误检测逻辑，且这条路径**丢掉了 `next_action`**。现在与其他 7 个工具一致，返回 `{"feeds": [], "error": ..., "next_action": ...}`，`is_error` 为假。

- **`smart_fetch` 重定向后 url 被静默改写**（D-14，P3）。调用方唯一的线索是一个自己没输过的 URL。新增 `original_url`，**只在确实不同时**才写（空 = 你传的就是应答的），所以常规路径不付费；比较时只归一化 scheme/host 大小写与结尾斜杠 —— 那正是 fetcher 自己会加的东西，其余（路径、查询串）都算变化。

### 边界（实测记录，不改代码）

- **`focus` 的过滤强度随查询词在页面里的分布剧烈摆动**，两个方向都会出问题。274 block 的合成页实测：`focus='zebraqnix'`（词只出现在 1 个 block）保留 **1/274**；`focus='artificial intelligence'`（页面主题词，半数 block 都含）保留 **135/274**；`focus='systems'`（单词查询）只保留 **7/274**。根因是阈值是**绝对值** `threshold=1.0`，而 BM25 得分随查询词个数、词频、block 长度大幅变化，单词查询下典型 block 得分约 0.9~1.1，正好卡在阈值两侧。
  - **不改**：把阈值调高只是把「几乎不过滤」变成「漏召回」，调低反之 —— 省 token 与保召回是同一根杠杆的两端，这是产品取舍不是 bug。用 `focus` 时把头部那句 `showing N of M blocks` 当**子集**看，要全量就 `focus=''`。

### 变更

- **wire 去重裁剪：13,605 → 12,601 字符（−7.4%，≈250 token/连接）**，起因是「每次启动这个 MCP 很费 token」的反馈。先量再改：连接期固定成本 = `instructions` + 8 个工具 schema，其中 **62% 是散文**（工具描述 4,361 + 参数说明约 3,960），其余是 JSON 骨架与 `annotations`。只动散文，三类：**同一事实写两遍**（描述 ↔ `options` 包 ↔ `instructions` 三处重复，−340）；**agent 无法据此行动的机制**（archive.org 的触发状态码清单、相对路径的四步解析顺序，−190）；**顺带修掉一处真错误** —— `smart_search` 的描述只列了 5 个 opt-in 引擎而注册表里是 8 个（漏 `so360`/`sogou`/`sogou_weixin`），改为指向 `options.engines`，两份手写清单只留一份。
  - **明确不动**：每个属性的 `type` 与 `enum`（删掉会诱发错误类型的调用）、`annotations`（`readOnlyHint` 决定客户端是否免确认，是能力不是冗余）、`CHECK BEFORE CITING` 那行、全部路由规则。
  - **预算同轮下调，规则是「只降不升」**：`tools/list` 13300→12300、`instructions` 1500→1465、连接 14000→13800，各工具上限也按「实测 + ~10% 余量」重推并封顶为不高于旧值 —— 否则裁剪会变成一次免费的额度膨胀。`smart_fetch` 是唯一不动的：旧上限相对当时的实测只有 1.1% 余量，按 10% 重推反而会把天花板抬到 4216，那是放松守卫。
  - **边界**：这轮只砍 7.4%。连接期成本本身不是大头 —— 单次 `smart_fetch` 默认最多返回 **40,000 字符**（≈10k token）、`smart_crawl` 硬顶 **500,000 字符**，**一次抓取就超过整张工具表**。杠杆在 `max_content_chars` / `max_total_chars` / `focus=`，以及每个响应里那 ~667 字符的固定信封（`ResponseModel` 30 个字段整体 `model_dump_json()`，空值与默认值照发）。这两项本轮**未改** —— 改默认值会改变默认行为，需要单独决策。

- **三处新增守卫**：
  - **引擎清单不许只写一半**（`test_search_engine_list_is_never_partial`）：从 `search_engines._INDEX_FAMILY`（14 个后端）反查 `smart_search` 的**每一处**引擎清单，每处要么为空、要么等于真实的 opt-in 集合。**第一版写错过** —— 把整份载荷当一个字符串扫，于是 `options` 包里那份完整清单把描述里缺的 3 个「补」成了 8，对原始 bug **静默通过**，变异实验当场揭穿。整词匹配也是刻意的：`sogou` 是 `sogou_weixin` 的子串。
  - **同一个名字不许定义两次**（`test_import_provenance.py::test_no_definition_is_shadowed_by_a_duplicate`）：本轮真实踩到 —— 一个测试类被追加了两次，**第二次定义静默遮蔽第一次**，pytest 照常收集、照常全绿，但 24 条只跑了一半。守卫扫 `src/dhole_mcp` 与 `tests` 全部模块的**直接**子节点是否重名（不看 `if`/`try` 内部 —— 条件定义是合法写法，遮蔽是无条件的）。
  - **docstring / wire 的漂移**：客户端收到的是 `_TOOL_DEFS` 里的 `description`，工具方法的 docstring **不上 wire**（全项目零处消费 `__doc__`）。两边没有同步机制，于是 docstring 必然腐坏 —— 本次实测两处：`screenshot` 的 `:param:` 把早已搬进 `options` 的键描述成顶层参数，还列了 `wait_selector_state`，而它**根本不被接受**（不在 `_SHOT_OPTIONS` 白名单里）；`smart_search` 的 docstring 说 "ranks by neural relevance"，而神经重排是**可选**的（lean install 或离线时回落到 consensus + 引擎位置序）。**三个实例方向一致：docstring 错、wire 对** —— 所以「docstring 更详细 = 更权威」这个默认假设是错的，改之前要拿 wire 和实现各对一次。守卫：docstring 里的 snake_case 标识符若在整个 wire 载荷里找不到，必须登记进 `_DOCSTRING_ONLY_*` 白名单并写明理由；另一条防白名单长草（本次抓到 3 个已失效条目）。

- **archive.org 第三层降级写进工具描述与 README**：升级实际是三层（`http → stealthy → archive.org`），此前只存在于响应字段 `source` / `archived_at` 的 Field description 里 —— agent 拿到响应后能看懂，**调用前**完全不知道。现在 `smart_fetch` 的 wire 描述写明触发条件、代价（10–30s）、辨认方法（`metadata.source` + `archived_at`）以及**没有任何参数能关闭它**。顺带补上 3 个可见性缺口：`escalation_path`、`source_type` / `is_official` 此前只存在于 docstring，而它们直接影响「要不要引用这份内容」，已搬进 wire 的 CHECK 行；`content_type` / `duration_ms` / `total_size_bytes` 判定为诊断字段，刻意不暴露并登记在白名单。

- **`smart_crawl` 描述写明 500000 字符硬顶**：`max_total_chars` 被钳在 500000，而 `max_pages` 只在未显式给出 `max_total_chars` 时参与推导 —— 撞上钳制后**再调大 `max_pages` 没有效果**，实测传 100 只抓到 31 页。

- wire 体积（`json.dumps` 默认渲染的字符数，非 token）：15.1 新增描述把 `parse` 874 → 1335（预算 960 → 1480）、`smart_crawl` 2147 → 2213、`smart_fetch` 3719 → 4009、instructions 1399 → 1473，连接合计 12,562 → 13,605 —— 把 15.0 留的余量吃光了。两个预算的上调都**写明理由**，而不是从别处砍描述来付账：`parse` 的 `encoding` 是调用方从乱码里恢复的**唯一**通道，`smart_crawl` 那句子树说明替换掉的 `(path prefixes)` 本身就是 bug 的成因。**随后的去重裁剪把整表压回 11,270（连接 12,610）**，15.1 对 wire 的净效果是 −843 字符。

- **`smart_fetch` 的 inputSchema 现在声明 `anyOf: [{required:[url]}, {required:[urls]}]`**（D-13）。此前 `required` 完全缺席，客户端无法在调用前判断必填。`cache_clear` 同样被报告列为「required 为空」，但它两个参数**本来就都可选**，所以保持为空 —— 给它编一个必填才是 bug。
- **`smart_fetch` 的 `schema` 描述写清 scalar/array 契约**（D-06）。报告读成「`attribute` 只返回第一个匹配」，实际上 `"type": "array"` 就会返回全部 —— 缺的是文档而不是能力，报告建议的 `{"all": true}` 会变成 `"type": "array"` 的同义写法。现在描述里写明：不带 `type` 返回**首个**匹配，带 `"type": "array"` 返回全部。
- **`feed_fetch` 的 `content[0].text` 与 `structured_content` 统一为 `{"feeds": [...]}`**（D-11）。此前文本通道是裸数组、结构化通道是 dict —— 同一次调用，客户端读哪个字段就得到哪个类型。

### 安全

- **README 补 fake-IP TUN 环境的说明**（E-01）。Clash / sing-box 的 fake-IP 模式把公网域名解析到 `198.18.0.0/15` / `fc00::/7`，SSRF 的「解析到内网即拒」于是把**所有**公网站点判成内网，`smart_fetch` / `resolve_url` / `feed_fetch` 全部被拦。工具的错误信息已经点出了诊断与逃生口（`DHOLE_SSRF_DNS_RECHECK=0`），README 此前没写。逃生口是**全量开关**、不是白名单，这一点也写明了。

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
