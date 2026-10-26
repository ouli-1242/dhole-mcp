# round-15 对抗审查（审查员帽）

> 输入：10 份画像报告 + 轮报告 + 68 发原始回执。审查阶段读源码/测试。
> **并发现场**：工作树有另一位 agent 的未提交改动；只读其文件，P6 只暂存本轮 hunk。

## 候选与逐条攻击

### R15-001 · 空表单框 property 读抛出 UnboundLocalError（B）
- **证据**：p09 S2，同 session 不 pin 重导航后 `read {"selector":"input[name=username]","property":"value"}`
  → 动作行 error 逐字为 `cannot access local variable 'empty_hint' where it is not associated with a value`。
- **攻击**：读 `actions.py` 的 read 分支——`empty_hint` 只在 attribute 分支（约 2753 行）与文本分支
  （约 2769 行）赋值；property 分支设置 `text/what/read_via` 后直接落到 `if not text: raise
  ValueError(... empty_hint)`。空字符串是合法读数（未填的框），于是走未绑定路径。
- **复现**：单测用既有 `_Page/_El` 假 DOM，`props={"value":""}` + property 读 → 红，错误逐字同线上。
  既有的空值测试只覆盖 attribute 分支（`test_an_empty_form_field_says_that_is_its_current_value...`），
  property 分支是漏网。
- **危害**：正常操作（读一个还没填的框、重导航后的空框）即触发；用户拿到 Python 内部错误，
  无出路句。**B 类确认**。
- **修法**（test-first）：property 分支补 `empty_hint = "'value' is empty on this field - that is
  its current value, not a missing read"`；行为与 attribute 分支一致（ok=False + 说明）。
- **Verdict：CONFIRM(B)，已修**（新测试 1 枚，先红后绿）。

### R15-002 · live 与缓存回放 fetched_at 差 1 秒
- **证据**：p05 S3 live=`2026-10-26T03:33:35Z`，S4 回放=`03:33:34Z`；p10 同一对相同。
- **攻击**：live 的 `fetched_at` 由 `quality._with_agent_hints` 在**响应末尾**盖 `datetime.now()`；
  缓存行存的是更早的 `cache.set_cached` 的 `time.time()`。跨秒边界即差 1 秒，模型侧字段描述
  写「For live fetches the two coincide」。
- **定级**：方向安全（回放不会比 live 更“新”），差 ≤1s，不改变任何缓存/新鲜度决策；且修复需把
  单一时间戳贯穿 fetch→store→envelope 三条路（并发 agent 正在改这些文件），风险 > 收益。
- **Verdict：REJECT→E（R15-002；backlog，下一轮若再现再升）**。

### R15-003 · recall 中 `/` 与 `/` 两行并排
- **证据**：p10 S4 前两行同为靶站索引，`http://127.0.0.1:8399/`（variants=2）与
  `http://127.0.0.1:8399`（无斜杠），is_stale 一真一假。
- **攻击**：`recall._merge_url_variants` 按完全相同的 URL 合并（R02-007 合同），尾斜杠是不同的 URL
  字符串；搜索侧 E2 家产早已在册「精确 URL 契约，查询参数变体不合并」。
- **Verdict：REJECT→E2 复发**（不加权、不裁决行为）。

### R15-004 · discover 谓词地图保留起点行 / 手册 max_depth 漂移
- **证据**：p07 S1 `path_include=/work` rows=1（只有起点），summary 如实报「7 个候选全被筛掉」；
  S2 `/article` rows=2（起点 + article）。R14-004/005 已裁决。
- **攻击**：根行是「你从哪来」，测试只对续页谓词断言；summary 的 `depth <= 2` 与手册
  `max_depth` 仍是历史概念。文档正在并发 agent 手里（tools.md/troubleshooting.md），本轮不碰。
- **Verdict：REJECT→D/E（维持 r14 结论；文档待并发落地后同步）**。

### R15-005 · sys_guide「web_read」topic 不存在
- **证据**：p01 S1 非 JSON 文本「unknown topic 'web_read'」+ 全 topic 列表。
- **攻击**：r11 已裁决「内容教学列全部 topic——非错误门」。是我按工具名猜 topic。
- **Verdict：REJECT（A）**。

### R15-006 · recall 0 行（p04/p07）
- **证据**：p04 查「规格 供应 工作」、p07 查「工作 规格 展品」→ 0 行；回执 note
  「nothing on this disk matches」。
- **攻击**：读两页正文（/work/1..3）——用词是「作品 / 第 N 篇 / 模板」等，查询词不在正文；
  r10 同族已立「查错词」量具注。
- **Verdict：REJECT（A/量具）**。

### R15-007 · p04 首读 `cached=True`
- **证据**：/work/1..3?cb=p04-1..3 首读三行 cached=True，fetched_at=03:10:51Z（早于本轮）。
- **攻击**：URL 的 cb 与 r14 脚本完全同名，缓存是磁盘级、跨进程共享——那是 r14 的真实抓取。
  量具污染，不是产品缺陷；本轮此后改用本轮独有 cb 的页面均为 fresh。
- **Verdict：REJECT（量具；教训：每轮 cb 带轮号）**。

### R15-008 · doc_parse summary 不点名 pages
- **攻击**：字段层 `pages_returned/pages_total` 准确；summary 只写大小与 parse。R14-003 已在册。
- **Verdict：REJECT→E（复发）**。

### R15-009 · 隔离面正向复核（PASS）
- 双 selector 交错（10/387/10B，缓存键隔离）；双 session 填名互不覆盖 + pin 命中；
  登录 session 23B vs 匿名 14B；check 两相与 read 缓存互不搅乱；crawl 不播种 read 缓存。
- **Verdict：PASS**（本轮主题面成立）。

### R15-010 · crawl/read 代际
- crawl 抓过的 /work/1 用 read 读仍是 fresh——r10 已裁决「壳页闸 + 指纹键合」，回执 cached 如实。
- **Verdict：REJECT（D，设计）**。

## 最终确认队列

**1 CONFIRM（B）：R15-001**，已按 test-first 修复（1 新测试，先红后绿）。
其余 9 条：E×3（002/003/008）· D/E×1（004）· A×2（005/006）· 量具×1（007）· D×1（010）· PASS×1（009）。

## 审查过程记录

- 本轮没有重演「量具截断」家族：所有「没找到/没读到」都先读了 JSON 全文与源码分支再定级。
- R15-001 的发现路径值得在册：画像里的 Python 内部错误文案是强信号——那不是产品话术，
  直接指向未覆盖分支。
- 并发纪律：未编辑另一位 agent 正在改的 models.py/server.py/quality.py；修复只落在
  actions.py 的 property 分支 3 行，P6 用单 hunk 暂存，不吞其 336 行在途改动。
