# round-15 轮报告（并行与隔离）

> 主题：两个上下文同时在手上时，会话 / 缓存 / selector / 提取类型是否互相污染。
> 10 画像 · **68 发** · 13/13 工具 · 端口 9215 · 基线 ea25700。
> 工作树含另一位 agent 的未提交改动；本轮只读不写其文件。

## 结论

- **1 CONFIRM（B）**：R15-001 空表单框 `read property=value` 抛 UnboundLocalError（`empty_hint` 未绑定）。正常操作即可触发，且错误文案是 Python 内部错误。已 test-first 修复。
- 其余 8 条候选全 REJECT（转 E/A/D/量具）。
- 隔离面正向实锤 5 项：
  1. 同页双 selector 交错：内容互不串味，缓存键按 selector 隔离（P01/P08）。
  2. 双 session 填表：A/B 各持其值，pin 命中回读正确（P01/P09）。
  3. 登录 session vs 匿名：同页 23B「欢迎回来」vs 14B「需要登录」（P02/P04）。
  4. check 两相与 read 缓存互不搅乱（P02）。
  5. crawl 不播种 read 缓存，代际隔离仍成立（P05/P07）。
- 新鲜度回归全过：相对 since 3h/3d/2w、offset 窗口 `<本窗> of <总量>`、缓存回放同 fetched_at（P10）/ 差 1 秒（P05，E）。

## 问题清单（P3 后）

| # | 级别 | 现象 | 去向 |
|---|---|---|---|
| R15-001 | **B** | read property 空值 → UnboundLocalError | 已修（test-first） |
| R15-002 | E | live/replay `fetched_at` 跨秒可差 1s，与字段描述「coincide」不符 | backlog |
| R15-003 | E2 复发 | recall 里 `/` 与 `/` 两行并排、is_stale 打架 | backlog（E2） |
| R15-004 | D/E | discover 谓词地图保留起点行；手册 `max_depth` 漂移（文档并发编辑中） | backlog（R14-004/005） |
| R15-005 | A | sys_guide 用了不存在的 topic（原文已列全） | 用法 |
| R15-006 | A | recall 查询词脱离正文导致 0 行（note 已说明） | 用法 |
| R15-007 | 量具 | p04 首读 `cached=True`——跨轮共享磁盘缓存（cb 与 r14 重名） | 记录 |
| R15-008 | E | doc_parse summary 不点名 pages | backlog（R14-003） |

## 评分

P01 4.5 · P02 5 · P03 5 · P04 4.5 · P05 4.5 · P06 4.5 · P07 4 · P08 5 · P09 2.5→4.5（修后） · P10 4。
