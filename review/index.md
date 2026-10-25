# dhole-mcp 真实使用模拟 · 总索引

> 宪章：../loop/PROMPT.md（原 review/PROMPT.md，材料现居 loop/）· 静态参考：../loop/ref/
> 仓库：D:\Dev\Tools\dhole-mcp · 被测：dhole-mcp（editable，v17.2 + 在途改动）

## 阻塞级置顶

（无）

## 轮次一览

| 轮 | 分支 | 基线 commit | 画像数 | 真实调用 | 确认缺陷 | 状态 |
|---|---|---|---|---|---|---|
| manual-2026-10-25 | （main 工作树） | e917220+WIP | 手动脚本代画像 | 29 | 2（当日已修，见该目录 report.md） | 完成（循环开始前的手动摸底轮） |
| round-01 | review/round-01 | 3cf035e | 10 | 101 | 8 fixed（R01-001/002/003/005h/006/008/009/010）+ 1 wontfix（007 设计取舍）+ 5 backlog（004/011/012/013/014）| 完成（P5 复验全过，全量 pytest 7728 绿）|
| round-02 | review/round-02 | aeb7059 | 10 | 64（画像）+ 25（P5 复验/探针） | 5 fixed（R02-001/002/003/004/007）+ 1 wontfix（005 设计取舍）+ 1 backlog 升级（006：E1/E3/E8 三现）| 完成（P5 复验 W1–W5 全过，全量 pytest 7746 绿）|
| round-03 | review/round-03 | 1f0d06b | 10 | 60（画像）+ 4（P5 复验/探针） | 1 fixed（R03-004 描述点名）+ 2 REJECT（001 豁免进程级在册 / 003 顶层信号在——审查推翻首报）+ 1 E 新档（002→E12）+ 2 E 记录（005/006）| 完成（P5 复验 W1 + 回归 11/11 PASS，全量 pytest 全绿）|
| round-04 | review/round-04 | 7cd2bc0 | 10 | 55（画像）+ 3（P5 复验/补条） | 2 fixed（R04-001 iframe 提示 / R04-002 watch 出路句前题）+ 2 E 记录（003=E7 活体 / 004=E2 邻接+E10）| 完成（P5 复验 W1/W2 + 回归 PASS，全量 pytest 7751 绿）|

## 跨轮资产

- common-issues.md — 缺陷 taxonomy 与高发区（round-01 以 manual-2026-10-25 为种子；round-02 起含行为澄清条目）
- backlog.md — E 类 UX 缺口（round-02 起带三现线标注与 wontfix 后记观察）
- decisions.md — 决策日志
- manual-2026-10-25/ — 手动摸底轮全部产物（报告 + 29 发原始回执）
