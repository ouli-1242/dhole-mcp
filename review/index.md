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
| round-05 | review/round-05 | 959ed79 | 10 | 53（画像+补条） | **0 fixed（首个零确认轮）**+ 1 REJECT（001 preflight 错误族=环境所致，观察项）+ 1 E 记录（002=E10 样本）| 完成（故障路径轮：回归 4/4 真测 PASS，pytest 7751 绿）|
| round-06 | review/round-06 | 5820f11 | 10 | 50（画像+补条） | 2 fixed（R06-001 0匹配 selector 说出去 / R06-002 碎片免整页尺）+ 2 E 记录（003=E2 家族五轮样本 / 004=focus 引擎名+sheet 小计）| 完成（P5 复验 W1/W2，pytest 7755 绿×2）|
| round-07 | review/round-07 | 0702edf | 10 | 57（画像+旁支）+ 5（P3 probe/P5 复验） | 1 fixed（R07-001 寄存器拒绝补 pin 出路）+ 手册对账 PASS（19=19 双向零差/urls=11 如实/绝对句×2 实证）| 完成（手册与实况轮：P5 复验 W1+W2，pytest 7757 绿×2）|
| round-08 | review/round-08 | ba4e008 | 10 | 93（画像 57+补全 14+旁支 22）| 3 fixed（R08-001/005 frame 落空具名 · R08-003 重铸出声 · R08-002 上传寿命句）+ **2 REJECT（R08-004/006——审查推翻首报，同根=量具截断）**| 完成（web_act 深水轮：P5 复验 V1-V3，7765 测试，xdist flake 在册）|

## 跨轮资产

- common-issues.md — 缺陷 taxonomy 与高发区（round-01 以 manual-2026-10-25 为种子；round-02 起含行为澄清条目）
- backlog.md — E 类 UX 缺口（round-02 起带三现线标注与 wontfix 后记观察）
- decisions.md — 决策日志
- manual-2026-10-25/ — 手动摸底轮全部产物（报告 + 29 发原始回执）
