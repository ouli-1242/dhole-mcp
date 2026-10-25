# 画像报告 · P02 顾晚舟（测试开发 QA · L2）· round-04

> 主题：web_act 写路径深水。11 发（含求助与重试）。p02-/p22-/p23-/p24- 前缀。

## 场景实录

- **S1 upload**：首试败于脚本路径（file not found——拒得对）；修好后 `upload 1 file(s) into '#doc' ok` → click #send 被 `pending_approval` 拦（上传表的提交也在闸内——拦得对）→ confirm=true 重发 → **「received 报价说明.pdf (1590 bytes)」**。
- **S2 dialog**：alert/name 两个弹窗步 ok，metadata 多一行 `dialogs` 收据。
- **S3 iframe（候选 R04-001）**：裸 selector 打 iframe 内元素三连败——「the selector matched no element (or none became actionable) before the timeout」，**33 秒烧在三条 10s 超时上，错误从头到尾没提「这页有 iframe、元素在里面要用 frame: 寻址」**。求助 sys_guide → 文档里有（`{"selector": "#note", "frame": "#app"}`）→ 重试三步全 ok（fill/click/read 收据齐）。机制在、失败话术不指路。
- **S4 drag**：陌生键第一拒全形（R01-010 回归 PASS）→ 正形 `drag '#card-a' -> '#slot-b' ok`。
- **S5 keyboard 变卦**：press Enter in #password → 提交成功。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 根因 | 严重度 |
|---|---|---|---|---|
| S3 三连超时 | 「选择器明明对啊」 | 错误不提 iframe/frame 寻址（候选 R04-001） | 骂人边缘 |
| S1 首试 | 「路径错了是我的锅」 | file not found 拒得对 | 无感 |

## 放弃点
无（QA 不会放弃，会查文档）。

## 主观评分
- 第一印象 4 / 速度 3（33s 超时税）/ 可信 5 / 报错可读 3（iframe 那三条）/ 等待 4 / 总体 4.0。

## 改进清单
1. 【骂人边缘→希望】selector 超时且页面有 iframe 时，步级错误补一句 frame: 寻址指引（R04-001）。
2. 【保持】upload 的确认闸与「received <文件名> (bytes)」落点。
3. 【保持】dialogs 收据行。

## 附录
证据：sessions/persona-02/22/23/24/ + p02-run.log。
