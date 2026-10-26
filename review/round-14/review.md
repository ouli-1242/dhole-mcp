# round-14 对抗审查（审查员帽）

> 输入：10 份画像报告 + 轮报告 + 78 发原始回执。审查阶段允许读源码/测试。
> **并发现场**：工作树有另一位 agent 的未提交改动；本审查只读其文件与在途测试，不改不提交。

## 候选与逐条攻击

### R14-001 · offset 续片 summary 不点名切片
- **首报**（P02/P10）：`473B of 7.0KB` 被读成「只报本片，没说是哪一段」。
- **攻击**：重读在途测试 `tests/test_an_offset_window_summary_names_the_slice.py`：
  期望形状正是 `"<本窗> of <总量> markdown"`，并明确「尾巴那一窗 is_truncated=False 也要
  说是切片」「[No more content.] 不算窗口」。p02 S2 的 `473B of 7.0KB markdown` 逐字命中。
  我首报期待的是「起止字符坐标」，产品契约是「本窗 of 总量」——**首报读法错误**。
- **Verdict：REJECT（首报推翻）**。剩余「不含起止字符」属 E（措辞取舍）。

### R14-002 · recall 同文多变体 + 词形巧合
- **证据**：p01 前三行 `/article`、`?t=html`、`?cb=…`；p07 问「对话框 删除」返回注意力
  综述（snippet 含「删除线」）。
- **攻击**：E2 家产在册（R02-007 已修「同一 URL 合并」，query 变体是不同的 URL，属
  「search 侧仍开放」）；词形命中是 CJK 二元组的既定语义，snippet 让用户能识破。无分数
  是 E6 在册。不是新 B，也不是回归（没有改坏 R02-007 的合并键）。
- **Verdict：REJECT→E（backlog 已有 E2/E6，记复发）**。

### R14-003 · doc_parse summary 不点名 pages
- **证据**：p06 `pages_returned=2`、`pages_total=3`、TOC 落在 2/3 页，summary 只写
  `1.3KB markdown · parse`。
- **攻击**：字段层是准确的，页范围确实生效；只是总结没复述。属交付话术（E）。
- **Verdict：REJECT→E**。

### R14-004 · discover path_include 保留种子行
- **证据**：p11 S2 `path_include="/work"` → 7 行 = 6 个 `/work/*` + 种子 `/collections?items=6`。
- **攻击**：种子是地图根，工具把它作为「你从哪来」保留；既有测试
  `test_a_map_the_budget_cut_can_be_read_back_row_by_row.py` 的谓词用例测的是**续页**
  （游标页），没有对根行下断言；文档也没写根行例外。行为可能是有意的，但没说出来。
- **Verdict：REJECT→D/E（根行保留按设计，文档补一句即可；不改行为）**。

### R14-005 · discover 手册 `max_depth` 与公开参数面漂移
- **证据**：p07 按手册词 `max_depth` 调用 → `invalid_request`，supported 名单只有
  `max_pages` 等；手册 `references/tools.md:328` 仍写「max_depth (default 2, range 0–5)」，
  `troubleshooting.md:72` 也教 `max_depth`；summary 仍说 `depth <= 2`。
- **攻击**：代码注释（server.py 的 `_SC_PARAMS` 上方）明确说 web_discover 不再收这个
  参数、crawl 内部仍用；schema 与该名单一致。所以**代码是有意收窄，文档没跟上**。
  这是真实用户会踩的坑（我们踩了），但错误当场给全支持名单，可一发自愈；归 E（文档漂移），
  不是 B。且 `references/tools.md` 正被另一位 agent 编辑，本轮**不碰**。
- **Verdict：REJECT→E（backlog；并发编辑，待其落地后同步）**。

### R14-006 · discover summary 的 `depth <= 2`
- **攻击**：内部默认值，用户无法调；与分析 005 同根（stale depth 概念）。并入 005。
- **Verdict：REJECT→E（与 005 合并）**。

### R14-007 · L1 工程词（cached/fetched_at/truncated）
- **攻击**：事实都对，长期 E；不挡任何操作。
- **Verdict：REJECT→E（既有）**。

### R14-008 · sys_guide 原文被截断
- **攻击**：驱动 `driver.py` 对非 JSON 回执只存 600 字（`_raw`），属 **harness 量具**，
  不是 dhole。记 env-log。
- **Verdict：REJECT（harness，非产品）**。

### R14-009 · p07 `max_depth` 被拒
- **攻击**：A 类参数误用（我按旧手册），错误名单清楚；p11 正形一次通。
- **Verdict：REJECT（A）**。

### R14-010 · feed 相对 since / check / offset / 批量缓存
- **攻击**：四项都是本轮主题面，实测全部成立（p01/05/08/09 相对窗口、p05/p10/p12 check
  两相、p02/p10 offset 窗口、p04 逐行缓存）。无缺陷。
- **Verdict：PASS（观察项：E8 可升级为已可用）**。

## 最终确认队列

**空（0 CONFIRM）**。本轮未发现 B 类可修复缺陷；5 条 E 级进 backlog/观察，
1 条 D/E（path_include 根行）只补文档，不改行为。P4 无修复；P5 做真重启回归。

## 审查过程记录

- 本轮**推翻了自己的首报**（R14-001）：`473B of 7.0KB` 正是产品在途新特性的目标形状。
  这与 r08/r09/r12 的五次「量具截断」同族——教训是：判定前先读在途测试/契约，不凭
  自己脑中的形状下结论。
- 并发纪律：只读不写另一位 agent 的文件；发现的两条 E（docs drift、summary depth）都落在
  其正在编辑的路径上，明确记「不碰」。
- 反假干净：0 CONFIRM 不是靠降级得到的——5 条 E 全部留在报告里；A 类与 harness 问题也列了。
