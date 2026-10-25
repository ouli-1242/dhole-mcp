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

## 跨轮资产

- common-issues.md — 缺陷 taxonomy 与高发区（round-01 以 manual-2026-10-25 为种子）
- backlog.md — E 类 UX 缺口
- decisions.md — 决策日志
- manual-2026-10-25/ — 手动摸底轮全部产物（报告 + 29 发原始回执）
