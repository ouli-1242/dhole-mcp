# 模拟测试（计划第十章 eval 基线）

> 2026-10-01 执行　·　被测版本 **17.0**（`24b3aed`）　·　零真实站点流量
> 2026-10-02 追加：B0（大众化一刀切的量尺与地基）开工，见「B0 · 排序/去重评测地基」

## B0 · 排序/去重评测地基（2026-10-02 动工）

大众化一刀切（17.0 重构）的硬约束是：**动排序/去重必须先有可复现评测**。B0 就是
那把尺子。两个集，**各出各的数，永不合并**——合起来会掩盖"模型涨了但解析器拖后腿"。

### 集 A：真实 SERP 冻结语料 + 盲评

- `evals/queries.jsonl`：72 题活集（只增不减）。每条四件：`查询 + 来源`
  （`repo_evidence`（仓内证据）/ `public_data`（公开常见题）/ `usage_scenario`
  （真实场景））+ 语言 + 备注；盲评标注与 labeler 在 qrels 侧落盘。
- `evals/capture_serp.py`：冻结采集器。**确定性分层抽样**（crc32(query_id) 播种，
  同一天重跑抽到同组；每题 3–4 家引擎、横跨 ≥2 个索引家族——共识分母是家族数；
  sogou 家族进所有中文题，英文题只给少数派名额，理由见下）；**拒写无结果页**
  （usable=0 一律跳过并记 `failures.jsonl`）；断点续跑（已有页面跳过，重跑只补缺）。
- 语料落 `evals/corpus/serp/<qid>__<engine>.html` + `.meta.json`
  （`captured_from_url / sha256 / item_nodes / usable / scrubbed / labeler`）。
- `evals/ranking_baseline.py`：整条链重放（引擎 parser → `_normalize_url` 去重 →
  `_rank(mode="merge")` → `_apply_quality_boost` → `_diversify` → 两级过滤，与
  `smart_search` 无神经模型路径同序）。有 qrels 的题出 P@5 / P@10 / MRR / nDCG@10。
  **神经重排不在此处**——它归集 B。
- **去重准确率**：集 A 的免费副产品，不另造数据集。机械可判两类直接出数：
  可疑合并（host 不同并成一个 key=必错）、可疑拆分（host+path 相同分成不同 key）；
  其余等价性判断列清单供人抽查，不冒充自动结论。

### 集 B：纯模型（MIRB / MTEB-zh）

接口只有 `score(query, docs) -> [float]`，与引擎无关、跨版本可比。数据集几百 MB
**不进 git**：手动放 `evals/.runtime/eval_sets/<name>/qrels.jsonl`（每行
`{query, positives: [doc], negatives: [doc]}`）后 `ranking_baseline.py` 才跑，
否则如实报 `not_run`。换型判据（现在定死）：两标一致率 <80% 的题剔除；剔除后
不足 20 条 → **判"无证据，不换"**；分差不明显 → 不换。

### 标注协议（避开"用旧模型输出当标准答案"）

**盲评**：只给 查询+标题+摘要（截 160 字）；引擎名、返回顺序、URL 一律不给
（URL 会泄漏引擎，顺序有位置偏置）。**逐条二元**（relevant: 0|1），不让判官排
十条——LLM 判官的位置偏置与长度偏置在排序式判断里放大。**provenance 落盘**：
qrels 记 labeler 身份与盲表 sha256，没记录就不可复现，等于没标。

工具：`evals/label_serp.py emit|collect|agree`。标注者两份：作者侧一份
（labeler=agent-b）+ 一个与本任务无关联上下文的第三方 agent 一份（labeler=agent-a，
独立上下文、只拿到盲表与判断纪律），逐题算一致率，分歧由作者仲裁。

### 集 A 语料的 robots 例外（显式登记，与 D1–D5 同文体）

搜索引擎的 robots.txt 普遍 Disallow `/search`，而 `robots.py` 默认遵守。造语料
必须豁免：`evals/capture_serp.py` 顶部显式设 `DHOLE_IGNORE_ROBOTS=1`。这是
**造评测语料的专用例外**——不进产品路径、不进任何测试、不默认开。（实情：引擎
抓取走 search_metasearch 的自建 HTTP 客户端，本就不经过 robots.py；这行是纵深
防御。它**不是** conftest.py 那句 `setenv` 的复用，也不能被产品代码引用。）

### B0 采集现状（2026-10-02，诚实账）

- 110 页入库 / 72 题 / 覆盖 67 题。本网络可达：sogou、baidu、so360、bing、yandex；
  **duckduckgo、brave 全部被拒（连接层失败），bing_global 返回 0 容器** ——
  这三家在有 VPN 的环境重跑 `capture_serp.py` 即补齐（断点续跑，只补缺）。
- 礼貌间隔：每引擎 3–5s，全量 239 次请求一次跑完无封禁。
- 我的 12 题 qrels 已收（labeler=agent-b）；第三方 agent 的标注与一致率随语料
  补齐推进。
- 首批指标（agent-b，n=12，冻结语料重放、无神经重排）：P@5 **0.333** ·
  P@10 **0.275** · MRR **0.472** · nDCG@10 **0.520**。样本里 5 题（q019/q036/
  q047/q055/q068）是"整轮无一相关结果"的失败样本——它们正是这把尺子要抓住的
  对象：引擎返回了站点根页/事件页/日历页，没有一条回答查询意图。语料补齐、
  双标一致率筛过后再谈这些数字的含义。

### 双标一致率（2026-10-02 首轮，定 B8 换型判据的尺子）

- 覆盖：labeler=**agent-a**（独立上下文第三方 agent，67 题 / 774 条）、
  labeler=**agent-b**（作者侧，12 题 / 133 条）。
- 重叠 12 题、119 条判断上的一致率：**118/119 = 99.2%**。唯一分歧：q012 的一条
  Launchpad 旧 bug 单（agent-a 判 0：太老太偏；agent-b 判 1：主题确实切题）。
  两份卷各自保留原判；按"拿不准 → 0"的纪律，将来合成 gold 时这条应落 0 ——
  仲裁在 gold 被真正消费（B8）时落笔，不在这里提前改卷。
- **没有任何一题低于 80% 线** → 本轮没有因一致率剔除的题。判据重申：剔除后
  不足 20 条 → "无证据，不换"；分差不明显 → 不换。
- 两标各自的全量指标同量级（agent-a n=67：P@5 0.373 / MRR 0.559 / nDCG@10
  0.654；agent-b n=12：P@5 0.333 / MRR 0.472 / nDCG@10 0.520）——尺子本身
  没有系统分歧。

---


## B0 开工基线（2026-10-02，`928d832`，分支 `feat/mainstream-config`）

大众化一刀切（B0–B8）动工前量定的四个数。**后面每批都对照这一条**——没基线就没法证明"没变坏"。复测命令都在仓库根：

| 数 | 值 | 命令 |
|---|---|---|
| 全量测试 | **2,923 passed / 7 skipped / 61 deselected**，121s | `env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY python -m pytest -q` |
| 连接期字符 | **24,030**（instructions 2,345 + tools/list 21,685）——B0 当时 17 颗口径；`DHOLE_TASKS` / `DHOLE_VAULT` 两旗已在 17.0 随四工具移除，当前 13 颗口径 33,099 | `python tests/tool_payload_measure.py` |
| MCP 快照 | **35 次 tools/call，isError 真 14 / 假 21**，markers=none，11.3s | `python tests/mcp_snapshot.py` |
| ruff | **All checks passed**（基线原有 8 个 W 批次残留 F401/F541，已在 `928d832` 先修掉） | `python -m ruff check src tests` |

注：交接文档写的 2,398 例是 17.0 早期数字；D1–D5 修复批（+43 例）等把套件推到了现在的 2,923。以本条为准。
另注（沿用 2026-10-01 的环境注记）：本机 `HTTPS_PROXY` 指向沙箱代理，跑任何真实网络脚本前先摘代理变量。

---
> 2026-10-01 追加：四项缺陷已修复并复验（见文末「修复与复验」）
> 2026-10-01 追加：补齐两处 eval 缺口 —— `feed_fetch` 的 `since` 增量端到端、视觉辅助基线（见「视觉辅助基线」）
> 2026-10-01 追加：L3/L4 重复任务端到端（见「L3 / L4 重复任务」）
> 2026-10-01 追加：凭据来源 + 独立登录流端到端（见「凭据来源 + 独立登录流」），并因此挖出缺陷 **D5**（私网健康探针把内容判定读成"目标不健康"）

## 一句话结论

**7 个场景中 5 个通过、1 个暴露真实限制、1 个按预期跳过；另发现 4 个缺陷，其中 1 个命中 17.0 的招牌能力（元素截图 + 二维码解码在动作链里拿不到图片像素）。**

四项缺陷**已全部修复**：`src/dhole_mcp/actions.py`、`security.py`、`server.py` 三处改动，新增 `tests/test_eval_fixes_g35.py`（43 个用例）；全量 2490 通过。

---

## 怎么跑

```bash
# 1. 起靶站（另开一个终端，或后台）
python evals/fixture_site.py            # 127.0.0.1:8399

# 2. M-1 基线（BM25 vs 神经 focus，零网络）
python evals/focus_baseline.py --json

# 3. 图片拦截 A/B 实验（需要靶站在跑）
python evals/check_image_blocking.py

# 4. 修复复验（需要靶站在跑）：走完整 web_read 路径确认 D1/D2 与 R1 已修好
python evals/verify_fixes.py

# 5. feed_fetch 的 since 增量端到端（需要靶站在跑，D3 之后才走得通）
python evals/feed_since.py

# 6. 视觉辅助基线：二维码解码 + 转交纪律（不需要靶站）
python evals/captcha_baseline.py
#    验证码试读：出题 -> 多模态 agent 读图 -> 判卷
python evals/captcha_baseline.py --emit 8 --level normal
python evals/captcha_baseline.py --score evals/.runtime/captcha/answers.txt

```

场景 1/2/5/6 由**外层 agent 直接调 dhole 工具**执行，逐条调用见下。

运行时产物都在 `evals/.runtime/`（已 gitignore）：
`orders.log` 订单日志、`requests.log` 请求落盘、`focus_baseline.json` 基线数据。

---

## 场景结果

| # | 场景 | 结果 | 关键证据 |
|---|---|---|---|
| 1 | 快照 → 索引动作 → submit 闸 | **通过** | 闸门运行只有 GET、**无 POST**，订单日志不变；带 `confirm:true` 重跑后 POST 落地，订单日志恰好 1 行 |
| 2 | cookie 双向桥 | **通过** | 浏览器层登录 → HTTP 层 32ms 读到"欢迎回来"；HTTP 层登录 → 浏览器层同样读到；`check_login` 三态正确 |
| 3 | `focus_mode="best"` | **通过** | 10 块中精确定位目标段；`filter` 模式同样正确；页头如实声明打分器 |
| 4 | `feed_fetch` since / loopback | **暴露限制** | 见 D3：无 `allow_private`，loopback 被 SSRF 拒。**D3 修复后已补端到端**（`evals/feed_since.py`，5/5 通过） |
| 5 | robots 诚实度 | **通过** | `metadata.robots = "bypassed (DHOLE_IGNORE_ROBOTS=1, process-wide)"` |
| 6 | 写审计核验 | **通过** | 4 条 write 事件与操作一一对应（闸门 `pending:true` → 确认 `pending:false` → 2 条 HTTP POST） |
| 7 | `neural_focus_model_missing` | **跳过** | 本机模型已下载，线上分支无法触发；单测已覆盖（`tests/test_neural_focus_m1.py:188/238`） |

### 场景 1 的服务端旁证链

```
[10:56:52] GET /checkout        ← 闸门运行：只有导航，没有提交
[10:57:21] GET /checkout
    !! ORDER WRITTEN: {'item': '无线鼠标', 'qty': '3', ...}
[10:57:21] POST /checkout       ← 带 confirm:true 重跑后才出现
```

### 场景 2 的 `login_check` 三态

| 状态 | `logged_in` | `signals` |
|---|---|---|
| 已登录 | `true` | `logout_control_present`, `login_control_present`, `no_login_form` |
| 未登录 | `false` | `password_field_present`, `login_form_present`, `login_control_present` |

> 观察：退出按钮文案"退出登录"**同时命中 logout 与 login 两个信号**（前者是后者子串）。结论正确，但信号集有噪声——真实中文站点普遍用"退出登录"，值得留意。

### `expect` 守卫（额外验证，通过）

索引 5 实际是"订阅源"，期望"登录"：

```
element 5 reads '订阅源', expected ~'登录' - the page changed since the snapshot;
re-run {"snapshot": true} and act on the fresh indices
```

页面**未跳转**，内容仍是索引页——"过期索引响亮失败"这一承诺成立。

---

## M-1 量化基线（BM25 vs 神经 focus）

语料：仓库内 `README.md`（91 块）/ `CHANGELOG.md`（140 块）× 5 个自然语言问句，共 10 个有效用例。
接地关键词判定答案是否被保留（只报保留率不报召回就是自欺）。

| 打分器 | 保留率 | 答案召回 | 均耗时 |
|---|---|---|---|
| BM25 | 22% | 90% | **23 ms** |
| 神经（bge-zh cross-encoder） | 13% | **100%** | **12 483 ms** |

三点结论：

1. **神经更紧且不漏答案。** BM25 在 CHANGELOG 的"神经 focus 用哪个环境变量打开"一题**丢了答案块**，神经保留了——这是神经路径的实质价值，不只是"分更高"。
2. **代价是单页 12.5 秒，约 540 倍。** 计划文档把 M-1 的代价表述为"抓取延迟的变化远大于搜索"，实测比这个措辞严重得多：一次带 `focus` 的抓取要多花 12.5 秒 CPU。**默认关是正确的决定**，但 README/工具描述里值得给出这个量级。
3. **计划里的"55~76% 保留率"是修复前的旧数。** 当前代码的锚定相对阈值（`relative_threshold=0.33`）已把 BM25 压到 22%，所以神经的边际收益是 **+10pp 召回 / −9pp 保留**，而不是文档暗示的"从 55~76% 大幅下降"。

> 另注（**此读法后被推翻，见文末「仍未处理」第 2 条**）：91 块与 140 块的耗时几乎相同（12.5s vs 12.2s），当时据此读作「耗时由**固定的分批开销**主导而非块数线性」。后续实测推翻了它——主导量是**分块数**（202 分块 14.0s vs 214 分块 14.8s，约 69 ms/分块），而这两个语料的分块数本来就接近，所以耗时接近。原读法把「分块数接近」当成了「与规模无关」。留在这里是因为这个读法本身值得记住：**两个样本在一个自变量上接近，不构成「与它无关」的证据**——那正是「看起来有依据、结论却反了」的形状。

---

## 视觉辅助基线（验证码试读 / 二维码解码）

计划第十章要求："图形验证码试读 ≤N 次通过率与转交率、二维码解码成功率——本地
fixture 生成扭曲文字与二维码图片，不依赖外站。"靶站新增 `/captcha`、
`/captcha.png`、`/captcha/verify`；基线脚本 `evals/captcha_baseline.py`。

**答案由 token 确定性派生**（`captcha_text`），靶站无状态、重启后同一 token 的
答案不变；出题（`--emit`）与判卷（`--score`）分离，出题时**不打印答案**。

### 二维码解码成功率（确定性，9/10）

与 dhole 的 `decode_qr` 同一实现（`cv2.QRCodeDetector`）。基准图 + 9 种常见失真：

| 失真 | 结果 | | 失真 | 结果 |
|---|---|---|---|---|
| 原始 | ✓ | | 缩到 0.35x | ✓ |
| 旋转 5° / 15° / 30° | ✓ ✓ ✓ | | 高斯噪声 σ=25 | ✓ |
| 缩到 0.5x | ✓ | | 高斯噪声 σ=60 | ✗ |
| 模糊 5x5 | ✓ | | JPEG q=40 | ✓ |

**9/10。** 唯一未过的是 σ=60 高斯噪声——那个信噪比下二维码在信息层面已不可解，
属已知上限而非缺陷（判据阈值因此设为 ≥8/10）。

### 验证码一次读对率（agent 侧）

dhole 里没有视觉模型（安全红线 6），读图发生在**外层多模态 agent**。本会话由
多模态 agent 亲自试读，**判卷前看不到答案**：

| 难度档 | 一次读对 | 一次读对率 |
|---|---|---|
| `normal`（逐字旋转 ±30°、正弦振幅 3.5~6.5） | 5/14 | **36%** |
| `hard`（旋转 ±55°、振幅 5~8 + 长波长、干扰线 5 条、噪点 380、笔画擦除） | 1/8 | **12%** |

错误呈**系统性混淆**，不是随机噪声：Z↔2、A↔4、A↔8、P↔R、W↔H、5↔S、9↔D、
Q↔D/J、B↔M。即"扭曲把字形推向易混对的另一侧"。

> **方法论警告（本轮踩到的）**：先前一次试读得到 6/6，原因是那时我在图的上方
> 已经打印了真值——**等于被喂了答案**。把答案从视野里拿掉后同一批难度掉到 36%。
> 任何"agent 读图率"如果不隔离答案通道，数字都是虚的。

**≤N 次通过率与转交率**：真实站点在提交失败后**换一张**验证码，靶站 `/captcha`
每次 GET 也确实发新 token，所以 N 次 = N 张独立新图。在独立假设下
`P(≤N 次通过) = 1 − (1 − p)^N`：

| 难度档 | p | N=3 通过率 | N=3 转交率 |
|---|---|---|---|
| `normal` | 0.36 | **73%** | 27% |
| `hard` | 0.125 | **33%** | 67% |

> **同图重读测不到，而且它本来就不该被算作重试**：本 harness 里图像只投递一次
> （重复投递相同字节会被去重），重试拿到的不是"新的一眼"，而是模型对同一份
> 上下文的再推理——信息已经在 context 里，第二次不会真的"重新看"。
> 所以上表的独立假设是**对的做法**，不是偷懒的近似。
>
> 顺带说明 17.0 选 **≤3 次**的合理性：`normal` 档 3 次覆盖 73%，`hard` 档只有
> 33%——对真读不出的图继续重试只是浪费轮次，转交才是终点。

### 转交纪律在不在 wire 上

| 判据 | 结果 |
|---|---|
| `DHOLE_MULTIMODAL=1` 时 `screenshot` / `smart_fetch` 描述变长、关旗时不含 `VISUAL ASSIST` | **通过**（+592 / +689 字符） |
| 描述写明 "at most 3" 与转交终点 | **通过**（`hand off` 出现 screenshot 4 次 / smart_fetch 1 次） |

纪律**只跟着 flag 上线**：关着 flag 的调用方看不见像素，纪律对它是噪声，还白占
connect tokens。

不变式由 `tests/test_captcha_fixture.py`（9 个用例）钉住——重点是"答案只由 token
决定、与难度档无关"，这条一旦悄悄破掉，基体会变成"用错的键判对的卷"且不报错。

---

## D5 是 `evals/vault_login.py` 挖出来的（该脚本已随 17.0 移除四工具删除）

**私网健康探针把"内容判定"读成"目标不健康"，于是本机登录页的浏览器层被跳过。**

第一版跑出来 3/6，`login` 报"这个页面上没有密码框"——而靶站的 `/login` 明明有一个
`<input type="password">`。根因不在登录流：`smart_fetch` 对回环 / 私网目标在启动
隐身浏览器前会做一次直连健康探测（3 秒、不重试），判定条件是
`probe.error or status >= 500`。而 `ResponseModel.error` 同时承载**内容判定**与传输
失败——`_is_auth_wall` 在 HTTP 层把登录页判成 `auth_wall_detected` 并写进 `error`，
探针据此认定"目标死了"，跳过浏览器层，把 HTTP 层的结果直接当成答案。于是
`{"snapshot": true}` 动作**从来没跑过**，而调用方收到的说法是"没有密码框"。

实测对比（靶站 `/login`）：

| | `fetcher_used` | `escalation_path` | `metadata.actions` |
|---|---|---|---|
| 修复前 | `http` | `private-probe:target-unhealthy(skipped_stealthy)` | `null` |
| 修复后 | `stealthy` | `direct:stealthy` | 三个元素（用户名 / 密码 / 登录按钮） |

修法是把健康判定收回传输维度（`status == 0 or status >= 500`），与代码旁边那句
"2xx / 3xx / 4xx ⇒ 活着"的注释对齐。**这条值得记下来的是它的形状**：单元测试全绿，
因为没有任何用例覆盖这个探针；而它命中的是"看起来成功了、诊断却是错的"——
正是这个项目最当回事的那类失败。回归网见
`tests/test_loopback_no_proxy_g31.py::TestThePrivateHealthProbeReadsTransportNotContent`。

---

## 缺陷清单

### D1（严重，命中 17.0 招牌能力）动作链里的元素截图拿不到图片像素

`smart_fetch` 的 stealthy 路径**硬编码 `disable_resources=True`**（`server.py:7091`），而 `DISABLED_RESOURCE_TYPES` 含 `"image"`（`browser.py:119-123`）。于是动作链运行期间，页面的 `<img>` 子资源被路由层 abort。

**复现**：对 `/qr` 页执行 `{"screenshot": {"selector": "#qr", "decode_qr": true}}`，返回：

```json
{"action": "screenshot '#qr' + decode_qr", "ok": true, "qr": [], "image": true}
```

截到的是**破图占位符 + alt 文字**，`qr: []`。

**A/B 实验坐实因果**（`evals/check_image_blocking.py`，同页面同浏览器层，只翻转一个变量）：

| `disable_resources` | 浏览器发出 `GET /qr.png` |
|---|---|
| `True`（动作链当前值） | **否**（0 次） |
| `False` | **是**（1 次） |

**排除其他嫌疑**：模块层 `decode_qr_bytes` 对同一张 PNG 返回正确载荷，解码器没问题；`_is_entry_target` 同主机同端口豁免，SSRF 不是原因。

**影响**：工具描述承诺"用它读取你即将在**同一动作链**里回答的验证码"。对**图片型**验证码与二维码，这条路径当前拿不到像素。而且失败是**静默的**——`ok: true`，`qr: []` 与"这页本来就没有二维码"不可区分。

**方向**：带 `screenshot` 动作时不要禁图片；或对 `screenshot` 动作单独放行 `image` 资源类型。

### D2（中）索引寻址的 `click` / `submit` 动作标签渲染成 `None`，并泄漏进审计

`action_label`（`actions.py:673-685`）对 `click` / `submit` 取 `val.get("selector")`，但 `_act_target` 把 index 寻址归一化成 `{"index": N}`（**不带 `selector` 键**）→ 传 `None` 给 `_target_label` → 落到 `repr(None)`。

实测输出：`"click None"`、`"submit None (unconfirmed)"`、`"submit None"`（成功路径）。应为 `click [0] ~登录`。

**且已泄漏进安全审计通道**：

```json
{"event":"write","channel":"browser_submit","pending":true,
 "action":"submit None (unconfirmed)"}
```

写操作留痕没写明提交的是哪个元素——审计通道恰恰是最需要写明目标的地方。

**方向**：两处改调 `_target_label(val)`（该函数已能正确处理 index 与 expect）。

### D3（中）`screenshot` / `feed_fetch` 缺 `allow_private`，与 `smart_fetch` 不对称

- `screenshot` 工具对 loopback 直接报 `URL targets internal/private IP`，**无逃生口** → 无法截取本地/私有页面。
- `feed_fetch` 同样无 `allow_private` → 无法轮询本地 feed（本轮的场景 4 因此无法走 MCP，`since` 逻辑改由已有单测覆盖：`tests/test_feed.py:163`、`tests/test_revalidation_g23.py:428-483`）。

`smart_fetch` 有 `allow_private`，另两个没有。是否补齐是设计判断（`screenshot` 起浏览器、SSRF 面更大），但**三者的口径不一致**值得有意识决定，而不是各自为政。

### D4（轻）`_log_tool_call` 的 `ok` 是"没抛异常"，不是"成功"

被 SSRF 拒的 `feed_fetch`（响应含 `error` 字段、`feeds: []`）在 `~/.dhole/usage.jsonl` 里记成：

```json
{"tool": "feed_fetch", "ok": true, "ms": 22.0}
```

`server.py:8321` 传的是 `True`（未抛异常），`8325` 传 `False`（抛异常）。对一个自我定位为"回答我的客户端到底有没有正确路由到这里、扛不扛得住"的日志，应用层失败与成功不可区分是误导。是否算缺陷取决于 `ok` 的定义意图，但它与读者的直觉不一致。

### D5（中，命中"看起来成功但诊断是错的"）私网健康探针把内容判定读成"目标不健康"

回环 / 私网目标的强制隐身抓取，在启动浏览器前会做一次直连健康探测，判定条件写成
`probe.error or status >= 500`。而 `ResponseModel.error` 同时承载**内容判定**
（`auth_wall_detected` / `bot_wall_detected` / `js_shell_detected`）与传输失败——
一个 HTTP 200 的本机登录页被 `_is_auth_wall` 判成登录墙、`error` 被写上，探针据此
认定目标死了，跳过浏览器层。于是 `{"snapshot": true}` 动作从来没跑过，而调用方
收到的说法是"这个页面上没有密码框"。

**复现**：对靶站 `/login` 执行 `smart_fetch(actions=[{"snapshot": true}], allow_private=True)`：

```json
{"fetcher_used": "http",
 "escalation_path": "private-probe:target-unhealthy(skipped_stealthy)",
 "metadata": {"title": "登录", "lang": "zh", "robots": "bypassed"},
 "content_ok": false, "error": "auth_wall_detected: page is a login/sign-in wall, not content"}
```

`metadata.actions` 为 `null`——动作一次都没跑。代码旁边那句注释本来就写着
"2xx / 3xx / 4xx ⇒ 活着"，实现却多读了一个字段。修法是把健康判定收回传输维度
（`status == 0 or status >= 500`）。详见上文「D5 是 `evals/vault_login.py` 挖出来的」。**这条值得记下来的是它的形状**：单元测试全绿，因为没有任何用例覆盖
这个探针。

---

## 覆盖边界（如实说明）

**已覆盖**：submit 闸的零副作用与放行、cookie 桥双向、`check_login` 三态、`expect` 守卫、`focus_mode=best`/`filter`、robots 标注诚实度、写审计四类事件、M-1 基线、元素截图线上行为。

**未覆盖**：
- `DHOLE_MULTIMODAL=1` / `DHOLE_NEURAL_FOCUS=1` 的**关闭分支**（本会话四个 flag 全开）；关闭分支的拒绝文案由单测覆盖（`tests/test_multimodal_visual_g34.py:105/281`）。
- 神经 focus 的线上触发（模型已下载）。
- **行为墙 / 2FA 的线上转交**：纪律文案已在 wire 上验过（见"视觉辅助基线"），但 DataDome / Akamai / 交互式 Turnstile 这类"无图可看"的墙与 2FA 因子不在页面上，本地靶站造不出真东西，只能靠文案断言，端到端仍未覆盖。
- 视觉验证码**试读**已覆盖（见上），但**滑块类**需要拖拽原语 + 拟人轨迹，超出 dhole 动作范围，靶站未搭。
- CDP attach 端到端（`DHOLE_CDP_ATTACH` 未开，需用户真实 Chrome）。

**已知的环境注记**：本机 `HTTPS_PROXY` 指向沙箱代理，跑仓库测试前需 `env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY`。

---

## 修复与复验（2026-10-01 追加）

四项全部修复。改动落在 `actions.py` / `security.py` / `server.py`，新增
`tests/test_eval_fixes_g35.py`（43 个用例）。全量 `2490 passed / 7 skipped / 0 failed`，`ruff` 全绿。

| 缺陷 | 修法 | 回归测试 |
|---|---|---|
| **D1** | 新增 `actions.actions_want_visual()`：动作链里有截图动作时，`_force_fetch` 不再关资源（截图的产品就是渲染结果，砍图与样式本身就是错的）；其余动作链照旧提速。`_force_fetch` 的 `disable_resources` 由硬编码改为参数，默认仍为 `True` | `TestAScreenshotActionKeepsPageResources` / `TestActionsWantVisual` |
| **D2** | `action_label` 的 `click` / `submit` 分支改调 `_target_label(val)`——它本来就同时认索引与 `expect` | `TestIndexAddressedActionLabels` / `TestTheAuditRowCarriesTheSubmitTarget` |
| **D3** | `security.allow_private_scope()` 提供 smart_fetch 那套机制的可复用形态；`allow_private` 成为 `screenshot` / `feed_fetch` 的**真实形参**（仓库有不变式：被转发的选项必须是方法接受的参数），作用域开在方法内、紧贴它保护的几行 | `TestAllowPrivateScope` / `TestThePrivateTargetOptionReachesBothTools` |
| **D4** | `_result_error()` 从 envelope 里取出工具自己报告的失败，记进既有的 `error` 字段；`ok` 保持"跑完没抛异常"的原义（批量里一个 URL 失败仍是一次跑完的调用），文档串写明两者区别 | `TestTheCallLogRecordsApplicationLevelFailures` |

**端到端复验**（`evals/verify_fixes.py`，走完整 `smart_fetch`：真浏览器、真动作链、真截图、真解码）：

```
[通过] D1 元素截图拿到像素并解出二维码
    qr=['https://example.com/checkin?token=DHOLE-EVAL-8399'] 内联图片=1 张
[通过] D2 索引寻址 submit 的标签与审计写明目标
    响应标签='submit [2] (unconfirmed)' pending=True 审计 action='submit [2] (unconfirmed)'
```

对比修复前：D1 是 `qr: []` + 破图占位符；D2 两处都是 `submit None (unconfirmed)`。

**代价**：连接期开销 +352 字符（两段选项描述），由仓库自己的
`tests/tool_payload_measure.py` 从源码独立测得：**23,572**（`tools/list` 21,431 + `instructions` 2,141）。

> 顺带发现：README 原先记的 23,566 在 HEAD 上**复现不出来**——同一工具在改动前测得 23,187（差 379）。该数字应是 17.0 amend 链中间态的产物，不是当前源码的值。README 已按工具当前报出的数字更新。

## 仍未处理

> 2026-10-01 追加：下列 1 / 2 / 4 已处理（见各自说明），3 仍待办。

1. ~~**M-1 措辞校准**~~ **已处理**：12.5s/页的量级已写进 README 与 `smart_fetch.focus` 的工具描述（`docs(readme)` / `docs(focus)` 两次提交）；计划文档里"55~76%"已标注为修复前数值。
2. ~~**神经 focus 的耗时结构**~~ **已查清**：耗时由**分块数**而非块数主导（202 块 14.0s vs 214 块 14.8s，约 69 ms/块）。此前"固定分批开销主导"的读法被推翻。据此还测了一个"按长度分桶"的加速方案：快 37~66%，但模型**不是 padding 不变**的（同块在 300 字批与 71 字批里得分差最大 2.2e-2；四路长度切分让单块得分最多移 0.34、top-20 进出 2~3 条），**已否决**。结论写进 `src/dhole_mcp/focus.py` 的代价模型注释。
3. ~~**视觉验证码试读 / 2FA 转交**的 fixture~~ **已搭**：靶站新增 `/captcha` 三端点（扭曲文字、两档难度），`evals/captcha_baseline.py` 量出一次读对率 `normal` 38% / `hard` 12%、二维码解码 9/10、转交纪律在 wire 上（详见"视觉辅助基线"）。**行为墙与 2FA 仍只有文案断言**——本地造不出真东西。
4. ~~`feed_fetch` 的 loopback 端到端~~ **已补**：`evals/feed_since.py` 走完整工具路径验 5 条判据（无过滤时计数保持 0、`since` 保留更新的并如实报出被丢掉的条数、被过滤时无日期条目仍在返回内容里、`since` 在未来、`since` 不可读时报错而非当成"全是新的"）。5/5 通过。

> 2026-10-01 追加，2026-10-04 修订：第九章编排轴最后两级（L3 `recipe` / L4 `watch`）
> 与第七章凭据来源 / 独立登录流当时已落地并各有端到端场景（7/7、6/6）；**17.0 把
> 这四颗工具一并移除**（`web_recipe` / `web_watch` / `cred_vault` / `cred_login`），
> 对应的 eval 脚本（`evals/tasks_l3l4.py` / `evals/vault_login.py`）与单测随之删除。
> 它们当年挖出的缺陷（D5）仍由 `tests/test_loopback_no_proxy_g31.py` 钉着。

## 技能本体校准：读了说明书会不会发对这一次调用（2026-10-03 动工）

上面所有基线量的是**工具**（排序、focus、验证码、冷启动）。这一把量的是另一件事：
`src/dhole_mcp/skills/dhole-web/` 这份说明书有没有让一个 agent 在**第一次调用**就选对
机制。一个参数可以被完整接通、被测试逐位钉住，同时被描述写成让人不敢用它 —— 前者绿
不代表后者成立。

### 尺子

- `evals/dhole_eval_cli.py`：MCP 的替身。dhole 的 server 没接进跑评测的会话，所以它把
  一份 JSON 参数喂给真实的 `MasterFetchServer._dispatch`，并原样打印客户端会收到的
  `TextContent`。要测的是 agent 的**参数选择**，这一层管道换不掉它。每一次调用同时
  落一份服务端 JSONL —— 评分读的是"实际发出去了什么"，不是 agent 对它的回忆。
- `evals/skill_eval_score.py` + `src/dhole_mcp/skills/dhole-web/evals/evals.json`：
  三档消融（A 只给连接期 schema / B +SKILL.md / C +全套 references）× 五格任务。
- 证据与导出物落 `evals/.runtime/skill-calibration/`。

### 第一轮实测（2 档 × 5 格，真实站点，非 mock）

| 档 | 断言通过 | 扇出被发现 |
|---|---|---|
| A-bare-schema | **9/20** | 1/4 格 |
| C-full-corpus | **15/20** | 4/4 格 |

读出来的不是"分数差 6"，而是**差在哪一类**：A 档在三格干脆没用 `nested_*`，退化成
"读首页 + 逐条手抓第二层"（正是 E-13 要消掉的那 8 次调用）；C 档五格全部用上了扇出，
失分集中在一类断言 —— `few_calls` / `one_page_only`，即**先探针一发再扇出**。两档都这样，
所以那不是文档缺口，是 agent 的合理保守行为：这条断言目前惩罚的是探针而不是错误做法，
`no_hand_rolled_second_layer` 才是要守的那一条（A 档 1 格失败、C 档全过）。

第一轮的 A 档有一个**作废条件必须记下来**：提示词指向的 `A.schema.json` 没被导出到
那个路径，所以那一档实际测的是"什么说明书都没有"，不是"只给连接期描述"。它仍然是有用
的下界（地板 = 9/20），但"A 档 vs C 档"的差值里混进了这一格混淆；下一轮先重新导出再跑。

### 回答文本里读出来的四处缺陷（都已处理，都有回归钉子）

1. **`force_fetcher` 在整篇读的那两条路上一直没落地。** 一个只读过参数描述的观察者点了
   浏览器层读站酷，拿到 `nested_no_match`，于是在回答里写下"这站的专题不存在"。真相是
   它抽的是 HTTP 层那 300 字符的壳页。根因不在新代码：`schema=` 分支直接叫
   `_auto_escalate`，而那架梯子不认识 `force_fetcher`（针在下面的车道，整篇读永远走不到）
   —— 所以这是 **16.0 起就在**的静默忽略，`nested_*` 复用那条路时把它变成了会被读成
   "内容不存在"的假答案。修在 `Server._document_tier`，实测站酷 + `stealthy` →
   `fetcher_used: stealthy`、87 元素命中 → 61 条去重链接。
2. **去重被报成了截断。** `quotes.toscrape.com` 首页 10 个容器去重成 8 位作者，
   `nested_max` 根本没生效，summary 却写 `truncation=nested_budget` —— 照这句话抬上限，
   抬到多少都拿不回那 2 条。现在两笔账分开写，且顶到 ceiling 时不再指"raise nested_max"
   这条走不通的路，改指 `web_crawl`。
3. **仓库自己的示例在它自己的示范站点上是错的。** `examples.md` / `tools.md` 对
   `quotes.toscrape.com` 作者页写的 `{"selector": "p", "type": "array"}` 抽回来的是
   登录 / 版权 / 署名段落，不是简介 —— 而这两份文档是 sys_guide 的同一份语料，一个
   "worked example" 就跟着进线上了。C 档的 agent 照抄之后拿到错数据，靠
   troubleshooting 里"先不带 `nested_*` 读一页重推选择器"才做完。已按实测选择器改正
   （`h3` / `span.author-born-date` / `div.author-description`，一次调用 8 条记录 8.2s），
   并在示例旁边写下这条区分：**要点住装值的那个元素，不是它外面的标签**。
4. **超过 30 条第二层链接之后没有 documented 的走法。** A 档与 C 档都独立报告了同一处
   空白（"no way to resume past the ceiling with web_read alone"）。实测确认
   `schema=` 可以与 `urls=` 组合，于是把出路写成两步：第一层用
   `a[href^="…"]` + `attribute: href` 取名册，然后 `urls=[...]` 分批。这条也补进了
   examples.md，并附了一格"说明书没写但确实成立"的组合规则。

顺带修的尺子自身两处：`dhole_eval_cli.py` 在 GBK 控制面上 `UnicodeEncodeError`
（三格各丢一次调用，而且长得像工具故障）—— 改成 UTF-8 + replace，并另落一份 sidecar
原文；`resolve_nested_links` 原来在 `limit` 处早停，导致 `skipped` 只数到前 `limit` 个
节点，两个交代性数字都偏小 —— 现在名册先收齐再切片。

### arm B 跑完了，以及它读出来的一处产品改进（2026-10-03 第二轮）

| 档 | 断言 |
|---|---|
| A 只有 schema | 9/20 |
| B schema + SKILL.md | 16/20 |
| C 全语料 | 15/20 |

B 这一格全绿的只有 1/5（`asked-for-one-page-only`）。A 与 C 的"全绿格子"数当时没记，
这里就不补一个猜的数。

**这三行不能横着读成排名。** A 与 C 是在三处工具修补（`force_fetcher` 在整篇读的那两条
路上落地、去重不再被报成截断、针住档位）与语料改写**之前**量的，B 是在之后量的 ——
16 对 15 这个差里混着"工具变了"和"文档变了"两种东西，本表拒绝把它拆成一个结论。
B 补上的是原本空缺的那一格，它回答的问题（"路由表起作用还是 references 起作用"）
在这轮仍然没答上，因为对照的两端不是同一版本。

B 读出来的一件事很清楚：**5 格里 3 格的唯一红项都是 `few_calls`**，而且形状相同 ——
agent 猜一个 `nested_selector`，收到 `nested_no_match`，再发一整页
`extraction_type='html'` 的探针去问真实类名，然后才摊平。DOM 在第一步就已经在服务器手上了。

于是补了这颗：`nested_no_match` 的 `next_action` 现在报出**这一页上成组的链接**，
计数口径与扇出一致（绝对化 + `urldefrag` + 去重），并且报三种形状 —— 链接自己
（`a.tag`）、装着链接的容器（`.quote`）、以及 href 前缀那一组
（`a[href^="/author/"]`）。第三种不是锦上添花：这一轮里两个 agent 最后各自摸到的
恰好都是这种形状，而类名在那类站点上根本帮不上忙。实测（quotes.toscrape.com，
故意给一个不存在的 `.does-not-exist`）：

```
Link groups actually on this page: a[href^="/tag/"] (36 links, e.g. "change")
  | a.tag (36 …) | .col-md-8 (35 …) | .quote (34 links, e.g. "(about)")
```

**省没省下那一发探针，这轮没有证据。** 改完立刻重跑最费探针的两格（`B2/`）：
两个 agent 第一次就用了 `a[href^="/author/"]` / `a[href^="/tag/"]` 这种属性选择器，
一次命中、`nested_no_match` 根本没触发，所以那颗新字段这一次没被走到。
调用数 4→3、5→3 是换了一次 agent 的方差，不是这颗改动的效果 —— 想证它得专门造一个
"选择器必然猜不到"的格子。它的根据目前是 4 条单元测试 + 上面那一次实测输出。

尺子自己的第三处缺陷也是这轮抓到的：`dhole_eval_cli.py` 的 `error` 字段只记
**抛出来**的异常，而 `nested_no_match` 是一发"成功返回"的错误回答，于是 37 发记录里
`error` 全是 `None`。我据此先数出"整轮零次 no_match"，那是一个假的 0 —— 真相在
`text_head` 里。改成同时读 `audit._result_error`（服务器自己就是这么区分
"调用跑完了"与"工具说它失败了"的）。

### 第三轮：三档同日同 wire 重跑（2026-10-06）

批次 12–16 之后的 wire 与语料上，三个同模型子代理各自独立、只读本档材料
（A.schema.json 当日重导出；A 13 发、B 12 发、C 12 发，全部真网）：

| 档 | 断言 | 全绿格 |
|---|---|---|
| A 只有 schema | 12/20 | 1/5 |
| B schema + SKILL.md | 13/20 | 1/5 |
| C 全语料 | 13/20 | 2/5 |

三件事读得出来：①扇出价值仍集中在 author-per-quote —— A 档 `urls=` 手搭第二层
被抓、B 用了 nested 但先发两发探针、C 一发命中（唯一满格）；②**tag-pages 三档全红
且形状一致**：30 个标签恰好贴着 `nested_max=30` 的上限，三档都退回 `urls=` 批量 ——
`urls=` 是文档教的合法路径，断言只认 nested 一条路，这是尺子与语料在"哪条路才算对"
上打架，记为待议（补一句"贴上限的扇出也成立"，或让断言接受 urls= 的等价形状），
本轮不改；③`shell-is-not-an-answer` 三档全诚实：站酷首页确实没有「推荐专题」栏目
（懒加载/改版），三档都升级 stealthy、如实说拿不到、零捏造 —— 这一格量的"不把空报
成无"由三档同时兑现。B≈C 打平：SKILL.md 已把扇出教到位，references 的增量在这五格
上不明显。横比限制同前：A/C 的旧数字量在旧 wire 上，不可与新数字同表读。

### B8 换型评测真跑了一次 —— 判**不换**（2026-10-04）

集 B 第一次真跑起来。来源 `C-MTEB/T2Reranking`（中文重排，MTEB-zh 的子集），
**确定性取前 300 题**（`evals/fetch_eval_sets.py`，`qrels.jsonl` sha256
`eeae69da6d2e170c…`），每题 ~7.5 条正例 / ~9 条负例，共 4,967 个候选。两个后端
跑同一批题、同一个 nDCG@all（算法与 `ranking_baseline._set_b()` 逐字一致）：

| 后端 | nDCG@all | MRR | 耗时 |
|---|---|---|---|
| `cross:bge-zh`（现状） | **0.8040** | **0.7762** | 760s |
| `bi:gte-multilingual-base@512` | **0.8070** | 0.7745 | 1744s |

逐题配对（bootstrap 5000 次，种子 17）：ΔnDCG 均值 **+0.0030**，95%CI
**[−0.0088, +0.0143]**，胜/负/平 = 140/135/25 —— **区间跨 0，分差不明显**。

按 §10 现在定死的判据（分差不明显 → 不换）：**不换**。

两条附带事实，都是量出来的：

- **候选在 CPU 上慢 2.3×**（1744s vs 760s）。它唯一的**结构性**优势是"能建索引"，
  而今天的架构每次只重排几十条候选、不建索引 —— 那个优势这一轮一次都没被用到。
  换句话说：这次换型买不到质量，还倒贴一倍多的时间。
- **两侧都截断在 512**，所以这一轮比的是"同预算下谁打分更好"。候选的 8192 长窗口
  **没有**被量到（27% 的文档超过 512 token，p90=1097、最长 7651）—— 那是它的另一个
  卖点，要单独跑一轮才算数，见"已知未覆盖"。

候选模型的字节逐位核对过（`publisher_sha256` 那条铁规矩）：
`onnx/model.onnx` 1,255,502,649 B / `5b9f03fd…`，`tokenizer.json` 17,082,734 B /
`3a56def2…`，来源 `onnx-community/gte-multilingual-base`，落
`evals/.runtime/models/`（不进 git）。

**落点：按退路执行了收敛。** 注册表只剩 `bge-zh`，`zh-full` / `ms-marco` 与
`~/.dhole/config/reranker.json` 一并下线，`paths.rename_legacy_model_dirs`（那条把
`msmarco-minilm-l6-v2` 改名成 `ms-marco` 的迁移）随之删除 —— 它搬去的目的地名已不再是
模型键。候选模型**没有**进注册表：它只属于这次评测。老机器上遗留的 `reranker.json`
不再选任何东西，但首次取模型时会有一条日志说明「你选的名字不再起作用」，不静默换打分器。
（落点摘要见 `CHANGELOG.md` 17.0 变更。）

跑法：`python evals/model_swap_b8.py --limit 300 --max-len 512`；只重算配对检验用
`--compare A.json B.json`（不重跑推理）。**为什么没走 `ranking_baseline.py` 的集 B**：
那条路此前根本跑不通 —— 脚本顶部把 `DHOLE_HOME` 指到一次性目录，`active_model_dir()`
于是指向一个空目录，模型永远"没装"；而 `get_reranker()` 只是 peek，评测不预热就回
`None`。两处一起，它只会永远报 `no local model (lean install)` —— 一个看着很确定、
其实从没跑过的结论。已修。

### 已知未覆盖

- A/C 两档需要在当前版本上重跑，三档才可比；A 档还得先把 `A.schema.json` 真的导出
  （上一轮它指着一份没生成过的文件，量到的其实是"什么说明书都没有"）。
- 跨档重跑要固定 agent 版本，否则 `few_calls` 这种断言量到的是方差。
- `shell-is-not-an-answer` 这一格的前提可疑：站酷的「推荐专题」在本机两个层级的 DOM 里
  都没出现（浏览器层列出 106–113 条链接，零条专题链接）。它现在实际量的是**拿不到时
  会不会说实话**（这一条两档都过了），不是专题扇出。**靶站现在有确定性的壳页**
  （`/shell`：几百字符、组不成任何一组链接），这一格可以换成本地靶，不再靠外站碰运气。
- ~~扇出第二层的**内容质量**没有断言，只有 ok/empty/failed 计数~~ —— 已补：整列同值与
  某列全空都会在 `summary` 里点出字段名（B0-5）。它分不出"这些页面本来就共享这个值"与
  "选择器抓的是页眉"，所以那句话用的是"要么…要么…"的形状，而不是判一个罪。
- ~~「探针到底被省下几发」需要一格"选择器必然猜不到"的靶页~~ —— **靶页已就位**：靶站
  新增 `/collections`（`?items=N` 张 `.card`，各挂一个 `/work/<n>`）与 `/work/<n>`
  （一列每页不同 / 一列每页同值 / 一列永不命中），四端点由
  `tests/test_fixture_site_fanout_targets.py` 走真 socket 钉住。它把这一格从"外站碰运气"
  变成确定性的：同一格跑两遍（一遍让 agent 猜错选择器、走到 `nested_no_match` 的新字段，
  一遍让它一次命中），差值才是这颗字段的效果。**仍未做的是那一跑本身** —— 它需要一个
  接进 agent 的 eval 循环，记在这里不冒充已完成。
- B8 只量了 **512 同预算**那一轮。候选的 8192 长窗口没量：本轮 27% 的文档超过 512
  token（p90=1097、最长 7651），所以长窗口大概率只对这部分题有影响。要判它值不值，
  得挑出"文档超 512"的那些题、在 `--max-len 2048` 下再跑一轮 —— 那是另一个实验，
  不混进换型判据（判据比的是"同预算下谁打分更好"，答案已经是不明显）。

