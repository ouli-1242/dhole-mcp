# round-13 对抗审查（审查员帽）

> 输入：P2 十份画像报告 + 轮报告 + 95 发原始回执（sessions/）。
> 对每个候选先尝试推翻，再给 verdict；B 类且复现稳定才进修复队列。
> 审查阶段允许读源码/测试（P1 禁令解除）。

## 候选清单与逐条攻击

### R13-001 · 显式 css_selector 收窄被 `extraction_collapsed` 误判
- **首报**：p21 A3 / p22 C7：`web_read(url, css_selector="h1")` 只取回 10 字（用户点名的 h1），
  回执 `content_ok=false`，`error="extraction_collapsed: … holds 400 characters … the words
  ARE in the HTML, dhole's reader lost them"`，next_action 还教 `extraction_type='html'`。
- **真实性**：正常用户（p04/p01 都试过点名取块）会点名 h1/卡片；不是矫情场景。站得住。
- **复现**：p21 A3（/article 缓存键不同→live）与 p22 C7（?cb=r13d 全新 URL）两次独立复现，
  结果一致（10 字 / 400 可见字 / content_ok=false）。
- **归因**：B（dhole 判决错误）。源码核实：`quality._detect_content_issue` 的
  `extraction_collapsed` 分支（quality.py:1088-1102）用「整页可见字」作分母，**没有**
  css_selector 豁免；而同一页的 `_substance_verdict` 已在 R06-F2 拿到豁免
  （quality.py:890-897）。两把尺一个豁免一个不豁免，正是 R06-002 修过的那族的另一半。
  `extraction_lost_text`（恢复尺）豁免 selector 的注释（quality.py:686-694）在册，说明
  设计意图是「narrowed 的调用方要的是更少」——判决尺漏了同一条。
- **严重度**：用户点名要一块，工具说「抽取器把字丢了」，并建议换 extraction_type（会绕开或
  重跑抽取）。误导下一步；content_ok=false 还会让上层 agent 放弃正确结果。P1 级 B。
- **完整性**：p21/p22 两个现场、原始 wire 回执在盘；非打印截断（读的是 error/content_ok 字段）。
- **Verdict：CONFIRM（B）**。修法：判决尺在 `_CSS_SELECTOR` 在场时跳过 collapse 判（留空判
  + 已有 basis），恢复尺不动；先写红测。

### R13-002 · 缓存回放丢掉 css_selector 0 匹配注记
- **首报**：p22 C6a live 的 summary 含「matched 0 elements…the whole document was returned」；
  紧接着 p22 C6b 同 URL+selector 命中缓存，**同句消失**，内容仍是整页 387B。
  p20 S1 / p21 A1 在旧缓存行上同样重现。
- **真实性**：用户重复读同一页+同一选择器极常见（缓存 TTL 1h）；R06-001 的整个立义就是
  「0 匹配必须说出去」。站得住。
- **复现**：write→hit 同 URL 对照（p22 #02/#03）是干净因果；两次独立现场（p20/p21）复现。
- **归因**：B（dhole 账丢失）。源码核实：注记由 `trafilatura_extractor._note_selector_matched_nothing`
  写入 `_ARG_NOTES`（trafilatura_extractor.py:573-587），仅在**抽取发生时**生成；
  缓存写入侧 envelope（server.py:6268-6301）没有存它，回放侧
  `_cache_hit_response`（server.py:7398+）只还原质量判决，summary 组装
  （quality.py:2795-2803）自然没有这句话。
- **严重度**：缓存命中是常态；用户第二次读会相信选择器生效并引用整页。P1~P2 级 B。
  与 R13-001 同族（都是「selector 没生效」的账），但根不同：一个是判决误报，一个是账丢失。
- **完整性**：live/hit 对照 + envelope 源码路径双证。
- **Verdict：CONFIRM（B）**。修法：写入侧把 selector 相关注记存进 envelope；回放侧原样
  带出（只带与缓存键同源的 `css_selector` 注记，避免把旧 focus 注记搬到新参数上）；先写红测。

### R13-003 · submit 闸的 next_action 像顶层 confirm（C1）
- **首报**：p10 S2 被闸后 next_action 说「re-run the same actions with {"confirm": true} on
  that submit」；p10 S3 照字面放顶层，再次被闸。正形只在动作行 error 里。
- **攻击**：动作行 error 明确写 `not at the top level` 并给出嵌套示例
  （p10 #03、p05 #02）；p16/p17/p18 三发用正形一次通。R09-003 家产本来就要求「读全四账面」。
  顶层 confirm 是 A 类（模型没读动作行），不是 dhole 挡了合法路径。
- **归因**：E（措辞可更好：next_action 直接给嵌套示例）。**Verdict：REJECT（转 E，backlog）**。

### R13-004 · dialog 错误文案的第二种形状两可（C2）
- **首报**：p03 S3 `{"answer":"商无咎"}` 被拒；文案写「a bare "accept"/"dismiss", or
  {"answer": ..., "prompt_text"?: ...}」。
- **攻击**：p21 C1 实测 `{"answer": "accept"}` 可被 prompt 收下（答空串，status=NAMED:）；
  p15 的 `answer=accept + prompt_text=名字` 一次通；错误文案给出了 `prompt_text` 这个关键键名。
  字面两可，但两条正确路径都在回执里，用户一回合内可修正。
- **归因**：E（把 answer 的枚举说死）。**Verdict：REJECT（转 E，backlog）**。

### R13-005 · 页签跨调用消失（C3）
- **首报**：p09 S1 开 /article 页签，S2（下一调用）`tab list` 只剩 0 号 `/`；回执无寿命句。
- **攻击**：p21 B1 同调用内两页签都在（0 /、1 /article）；p21 B2 不带 pin 下一调用回到单页；
  p21 B3 带 pin 但无 session_id → **产品主动教学**「pin=true could not be honored: an anonymous
  auto session is shared… Name a session_id= and pass pin=true again」。页面实例模型在册
  （upload lifetime 同族，pin 合同 r12 必测 1）。不是漏了功能，是模型没有在 tab 回执里
  重复讲寿命。归 D（预期行为）+ E（可在 tab 回执补一句）。
  **Verdict：REJECT（转 E，backlog）**。

### R13-006 · 表单/菜单页 read 无正文（C4）
- **首报**：p01 /dropdown 63B、p03 /dialog 30B、p10 /checkout 15B，均 content_ok=false
  「no prose (menu fragments only)」。
- **攻击**：这是靶站页面本身没有散文；诚实标签在，web_act 的 snapshot/read 是正路
  （p02/p06/p10 全程用它）。README/描述把 web_read 定位为「读正文」，不承诺表单结构。
  归 D + E（next_action 可直接给 snapshot 形状）。**Verdict：REJECT（转 E，backlog）**。

### R13-007 · 上传回执 bytes 与磁盘差 2（C5）
- **首报**：p16 receipt `received qinpu2.txt (63 bytes)`，磁盘 65 字节。
- **攻击**：数值由**靶站**计算（evals/fixture_site.py:847-850 `size = len(payload) - 2`），
  dhole 只转述页面上的 `#receipt` 文本。归 C（环境/靶站量具），与产品无关。
  **Verdict：REJECT（环境侧，env-log 记录）**。

### R13-008 · iframe 保存不跨调用（p14 T2）
- **攻击**：与 R13-005 同根（页面实例寿命）。T2 读到的 `not saved` 是页面真实值；
  pin 可留住（p06 合同）。归 D + E（iframe 回执可补寿命句）。**Verdict：REJECT（转 E）**。

### R13-009 · captcha 后 read #status 超时（p06 S3）
- **攻击**：靶站提交后是整页 h1，没有 #status；是我脚本假想的选择器。产品报
  `selector matched no element + Timeout 10000ms`，具名。归 A。**Verdict：REJECT**。

### R13-010 · 顶层 frame 三连拒（p04 主跑）
- **攻击**：`Unsupported argument(s) for web_act: ['frame']` + 全支持名单；p14 正形一次通。
  归 A（参数层级），无产品责任。**Verdict：REJECT**。

## 最终确认队列（只含 B 类、复现稳定）

| id | 现象 | 根因层 | 修法摘要 |
|---|---|---|---|
| R13-001 | 显式 selector 收窄被判 extraction_collapsed | quality._detect_content_issue 判决尺缺 selector 豁免 | `_CSS_SELECTOR` 在场时跳过 collapse 判决；恢复尺不动 |
| R13-002 | 缓存回放丢 selector 0 匹配注记 | envelope 未持久化参数注记 | 写入侧存 selector 注记，回放侧带出 |

两项同属「selector 没生效」的账，但不合并：一个是**误报**（live），一个是**漏报**（cache）。

## 审查过程记录

- 判定前读全四账面（summary/error/next_action/fetch_hint）；R13-001/002 均以字段原文
  与源码路径双证，不依赖打印截断。
- 本轮确实推翻了 6 条首报（C1/C2/C3/C4/C5/C8），其中 3 条转 E backlog、2 条归 A/C。
- **反假干净检查**：p04/p10 的 A 类错路、p06 的 10s 超时、靶站 bytes 差都留在报告里；
  两个 B 类都是 P3 探针新发现，没有被 P2 的「十条链全通」叙事掩盖。
