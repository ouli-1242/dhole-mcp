# dhole-mcp 真实模拟测试报告（2026-10-25 手动轮）

> 服务：`dhole --http --host 127.0.0.1 --port 9201`（v17.2，src 树 editable，
> 含今日未发布改动），真实 `~/.dhole` 状态、真实网络。驱动：原始 JSON-RPC
> over streamable HTTP（`trust_env=False`，协议 2025-11-25）。
> 证据：`sessions/calls.jsonl`（29 发全量台账）+ `results/*.json`（逐发完整
> wire 回执）+ `artifacts/dropdown.pdf`。驱动脚本 `sim.py`、补测
> `probe_cookie_jar.py`、`probe_go_back.py`。

## 结论

13 颗工具全部真实发出、10 个场景走完：**9 项行为符合设计与手册承诺**，抓到
**2 个真实缺口 / 毛边**（下 P2 / P3）与 1 组环境事实（非缺陷）。

## 场景 × 结果

| # | 场景 | 工具 | 结果 |
|---|---|---|---|
| S1 | 服务认知 | initialize/tools_list, sys_state, sys_guide | 13 颗 ✓；census 报缓存 28 条/会话 1 个；models 读数在 `reranker` 键 |
| S2 | 私网摩擦 | web_read, cred_defaults | 首发具名拒绝（127.0.0.0/8）；`allow_private` 豁免后 1.4s 通 ✓（摩擦路线完整） |
| S3 | 建图与爬取 | web_discover, web_crawl | sitemap 如实报「没有」；crawl `search='登录'` 过滤 0 行并如实计数 ✓ |
| S4 | 表单操作（新动作走真服务） | web_act | select value/label/列表三路 ✓、pdf 落盘 ✓、navigate back ✓、dialog 账本（未武装 auto-dismiss + 武装应答 + prompt 应答）✓、upload→提交闸暂停→confirm 重跑→`submission: POST`→页面回执「received sample.docx (63552 bytes)」✓ |
| S5 | 写请求纪律 | web_request, web_read | 无 confirm 具名拒绝 ✓；**带 session_id 被拒（P2，见下）** |
| S6 | 变更监控 | web_read(check) | 基线记录 ✓；复检 `changed: false` 如实 ✓ |
| S7 | 真实阅读 | web_read | wiki（focus best，见下「良好诚实」）；批量 2/2（键名是 `results` 不是 `pages`——我脚本打错）；arxiv PDF 直下 HTTP 档 2.8s 出 3,172 字、`timeout clamped 89→30s per attempt`（3.3 修复在线）✓ |
| S8 | 订阅 | feed_read | 2/2 真源（hnrss + 阮一峰）7 条 ✓ |
| S9 | 本地文档 | doc_parse | docx/xlsx/pdf 三发 ✓（pdf pages 钳制提示正确） |
| S10 | 搜索与研究 | web_search ×2, web_research | 中文查询 5 行 ✓；英文查询 4 行全中文站（天气，见下）；research 1/2 源成包、失败行说 why（P3，见下） |
| S11 | recall | web_recall | 从本机缓存召回 bilibili 行（新鲜）+ 旧行带 `is_stale: true` 标注 ✓ |

## 值得记下的良好诚实行为（模拟中真实发生）

1. **focus best 块的自我怀疑**：wiki Dhole 条目 `focus_mode="best"` 选中
   15 字的 `## Conservation` 标题块，信封直说「best 块只有 15 字——可能只是
   标题；去掉 mode 或改写查询」，同时报告「4 张表共 5,962 字与查询无共词被
   丢弃」。答案在表格里、BM25 够不着——工具把出路指给了调用方。
2. **提交闸的全链路**：upload → click `#send` → `pending_approval`（什么都没
   发）→ 同链带 `confirm: true` 重跑 → `submission: "POST"` → 站点收执可读。
3. **弹窗账本**：未武装的 confirm 被默认 dismiss **且记账**（`note: no
   {"dialog": "accept"} was armed`），武装后的 accept 与 prompt 应答都落在
   `#status` 上可验证（`NAMED:模拟员`）。
4. **降级池的如实报告**：`families_answered_of_asked: 3 of 9`、逐行
   `1 of 5 (degraded_pool)`、`engine_detail` 逐家给 why。

## 发现（按优先级）

> **两处均已于同日修复并复验**（见文末「修复回执」）。

### P2 · web_request 的登录 cookie 断点（已修复）

`web_request` 不收 `session_id`（拒辞列出全部受支持参数，其中无它）；POST
/login 成功、靶站确认发了 `Set-Cookie: sid=...`，但后续 `web_read /account`
（无 session 与有 session 各测）都拿到未登录壳——**写响应的 Set-Cookie 不落
任何后续可用的 jar**。17.0 的「不写密码登录流」编排（HTTP 层攒 cookie → 桥
进浏览器）里，凡登录要靠**写请求**（POST 表单）完成的站点，这条路就断。
补测证据：`probe_cookie_jar.py` 输出（cookie names 不上回执、/account 未登录）。
已修（同日）：web_request 收 `session_id`，响应 cookie 落该会话 jar——回执见文末。

### P3 · web_research 分摊预算可超过 web_read 的 timeout 上限（已修复）

research `timeout=150000` 分两源；第 2 源拿到 >120000ms 的预算，被 web_read
校验器整发拒掉（`why: Timeout exceeds maximum of 120000ms`）——失败如实进包
（`failed: [{n: 2, url: dict.cn, why: ...}]`，证据包没有被拖垮），但该源本该
被 **clamp 到 120000** 而不是拒绝。已修（同日）：分摊处 clamp 到
`security.TIMEOUT_MAX_MS`——回执见文末。

## 修复回执（同日）

| 项 | 修法 | 验证 |
|---|---|---|
| P2 | `web_request` 增 `session_id=`（白名单 + 签名 + wire schema，+244 全在该工具），递进 web_read；jar 存取本就在 fetch 层每跳每方法跑，无需新机制 | 真机复验 `probe_p2_fixed.py`：POST /login（302+Set-Cookie）回执 `session_id: sim-live`、`session_cookie_names: ['sid']`；同 id 读 /account 拿到「欢迎回来」已登录页 → **PASS**（修复前同一发拿未登录壳） |
| P3 | research 分摊处 `min(share_ms, security.TIMEOUT_MAX_MS)`，clamp 而非让下游拒 | 单元钉（假 read 遵守真校验合同）：超限份额被钳、两源照常成包 → 5/5 过 |

账目：连接期 36,412 → 36,656（+244 全在 web_request，单价 2,954 → 3,198；8 颗组 26,289 / 9 颗组 28,611）；棘轮与价目表（README / configuration.md）同步；锁由命令重写。钉 `tests/test_a_login_post_lands_in_the_jar.py`（5 条）。修复后全量 **7,692 passed / 0 failed**。

### 环境事实（非缺陷）

- 本机真实 `~/.dhole` 里积着此前实测的引擎冷却：brave（circuit open）、
  shenma 与 duckduckgo（blocked/captcha，CN 线路 ddg 需代理）。英文查询的
  4 行中文站是这个降级池（3 of 9）里 Bing 全球索引自己的排序——`degraded_pool`
  如实上脸，不是 2.4 语言车道回归。
- 批量读的行键是 `results`（我脚本最初按 `pages` 取，打错）。

## 台账

- 29 发调用、总墙钟 ≈2 分钟纯调用 + 节流间隔；0 起浏览器崩溃、0 协议层错误。
- 服务仍在 9201 运行（本报告发出时）。
