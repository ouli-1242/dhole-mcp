# 模拟测试（计划第十章 eval 基线）

> 2026-10-01 执行　·　被测版本 **17.0**（`24b3aed`）　·　零真实站点流量
> 2026-10-01 追加：四项缺陷已修复并复验（见文末「修复与复验」）

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

# 4. 修复复验（需要靶站在跑）：走完整 smart_fetch 路径确认 D1/D2 已修好
python evals/verify_fixes.py

# 5. feed_fetch 的 since 增量端到端（需要靶站在跑，D3 之后才走得通）
python evals/feed_since.py
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
| 4 | `feed_fetch` since / loopback | **暴露限制** | 见 D3：无 `allow_private`，loopback 被 SSRF 拒 |
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

> 另注：91 块与 140 块的耗时几乎相同（12.5s vs 12.2s），提示耗时由**固定的分批开销**主导而非块数线性——值得后续单独看，本轮未深挖。

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

---

## 覆盖边界（如实说明）

**已覆盖**：submit 闸的零副作用与放行、cookie 桥双向、`check_login` 三态、`expect` 守卫、`focus_mode=best`/`filter`、robots 标注诚实度、写审计四类事件、M-1 基线、元素截图线上行为。

**未覆盖**：
- `DHOLE_MULTIMODAL=1` / `DHOLE_NEURAL_FOCUS=1` 的**关闭分支**（本会话四个 flag 全开）；关闭分支的拒绝文案由单测覆盖（`tests/test_multimodal_visual_g34.py:105/281`）。
- 场景 7 的线上触发（模型已下载）。
- 视觉验证码试读、2FA / 行为墙转交——需要能生成扭曲文字验证码的 fixture，本轮未搭。
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
3. **视觉验证码试读 / 2FA 转交**的 fixture 仍待搭（需要能生成扭曲文字验证码的靶站）。
4. ~~`feed_fetch` 的 loopback 端到端~~ **已补**：`evals/feed_since.py` 走完整工具路径验 5 条判据（无过滤时计数保持 0、`since` 保留更新的并如实报出被丢掉的条数、被过滤时无日期条目仍在返回内容里、`since` 在未来、`since` 不可读时报错而非当成"全是新的"）。5/5 通过。
