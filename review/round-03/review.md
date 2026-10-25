# round-03 对抗性审查（P3）· 2026-10-26

> 6 候选逐条攻代码；两处审查推翻自己的首报（R03-003 的顶层信号其实在、R03-001 的豁免是进程级在册设计）。

## 裁决总表

| # | 候选 | verdict | 一句话理由 |
|---|---|---|---|
| R03-001 | 私网缓存回放不过门 | **REJECT（前提不成立）** | 「没开过门的会话」不成立：cred_defaults 的 allow_private 存在**进程级** identity store（security.py:122 `stored_allow_private` → identity.get），同端口所有会话共享——9203 启动警告明说 "Every client on this port shares one credential store, one session state and one cache"。豁免是 P01 在同进程里开的；门（validate_url 在各工具方法第一行）与缓存都在同一份豁免之下，语义一致。无豁免的**进程**读私网 URL 在 validate_url 处直接拒，到不了缓存 |
| R03-002 | recall 无「我存的」显著性 | **REJECT（B）/ CONFIRM（E 记录）** | 排序契约是语义相关；recency 已经在预筛里（recall.py:233 `ORDER BY fetched_at DESC LIMIT 400`）。「我存的该排前」是产品设计诉求（scope=mine / 显著性加权），不是坏行为——P04 那发 /article 的正文讲的是抓取缓存，与「文章」语义本来就远。记 backlog E12 |
| R03-003 | 步级拒因不上升顶层 | **REJECT（首报看漏）** | 顶层**在**：summary 尾巴「· 1 action(s) waiting for user approval (click '#place-order')」+ next_action 整句暂停指引（quality.py `_report_action_outcomes` pending 分支）。P08 报告凭 driver 的 100 字截断打印误判——报告已更正 |
| R03-004 | page_type=article 给了视频频道页 | **CONFIRM（B，小）** | 抽取本身没错（trafilatura 找到 ≥200 字散文、link_density<0.5 → article，crawl.py `_classify`）；错在**字段描述**没说这是「抽取形状」不是「页面体裁」——crawl.py:192 只写 "How the page was extracted… Tells the agent what kind of content it got"，P10 现场把它读成「这页是文章」。修：描述点名 article = 散文抽取成功，不代表页面本身是文章 |
| R03-005 | E8 四现（since 档位词） | **CONFIRM（E，记录）** | manual/R01/R02/R03 四轮；错误句已带示例，升级项留 backlog 排期（与 R02-006 同待遇），活体样本在册 |
| R03-006 | E3/E5/E10 活体再现 | **CONFIRM（E，记录）** | E3（offset 整页口径）本轮两现、E5（钳制句报剩余）一现、E10（404 行数字噪声）两现——均为已建档 E 类的活体样本，不另立新案 |

## 最终确认队列（B 类）

1. **R03-004** page_type 字段描述点名「抽取形状 ≠ 页面体裁」（crawl.py:192 模型描述；wire 侧核对 tools/list 是否受影响）。

## 记录并接受

- R03-001：豁免/缓存/会话三者的进程级共享是**在册设计**（security.py 头注 + 启动警告）；无新修。报告更正框架。
- R03-002：backlog 新增 E12（recall 本机抓过显著性/scope 语义）。
- R03-003：报告更正（顶层信号在场）；driver 的 100 字截断打印是**观测量具**问题，记 journal 防复发。
- R03-005/R03-006：backlog 活体样本登记。

## 附：良好行为确认（P3 侧）

- `_report_action_outcomes` 的 pending 分支把暂停抬成头号信号（summary 后缀 + next_action 全句）——R03-003 攻击它时反而确认了它的完整。
- 私网门 next_action 的「门+钥匙」句在 P01 一跳自愈——与前两轮的修复链无回归。
