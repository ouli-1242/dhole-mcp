# round-04 对抗性审查（P3）· 2026-10-26

> 4 候选逐条攻代码；两处 CONFIRM（B 小修），一处 E7 活体，一处 E 邻接登记。

## 裁决总表

| # | 候选 | verdict | 一句话理由 |
|---|---|---|---|
| R04-001 | web_act selector 超时不提 iframe | **CONFIRM（B）** | 机制在（actions.py:897 `frame:` 三种拼法——name/URL 前缀/iframe CSS，文档在 sys_guide）；失败话术不在：`_action_failure`（actions.py:1966）对 "no element" 只回超时事实，而失败现场**拿得到 page**——页面有子 frame 是当场可知的一般事实，不是域名特判。修：selector 超时且页面有子 frame 时，步级错误追加 frame 寻址指引（P02 的 33s 税本可免） |
| R04-002 | watch 出路句缺「flag 在 watch 进程 env」半个前题 | **CONFIRM（B，话术）** | 出路本身**已验证真通**（P03：带 flag 的 watch 进程 baseline 200 exit 0——R02-001 的修复方向是对的）；但 `dhole watch run` 是独立进程，flag 不在它 env 里就永远读不到（实测 exit 2 重演）。修：`_WATCH_PRIVATE_HINT`（ops.py）补「in the watch process's environment too (export it where `dhole watch run` runs)」 |
| R04-003 | E7 活体：跨进程缓存回放缺「存入时 env 语境」 | **CONFIRM（E，记录）** | 旁证撞主进程 41 分钟前的缓存行，回执自白 replayed 但没说存入时的 robots/env；E7 已建档（fetched-under 标注跨进程语义），活体样本在册，不另立新案 |
| R04-004 | 查询串变体不合并 + E10 再现 | **CONFIRM（E，记录）** | 合并键=精确 URL 是在册契约（R02-007 修的是同 URL 变体）；查询串差异属 E2 家族观察；E10 404 数字噪声三现——均 backlog 在册 |

## 最终确认队列（B 类）

1. **R04-001** selector 超时 + 页面有子 frame → 步级错误追加 frame: 指引（actions.py `_action_failure` + 链内调用点传 page）。
2. **R04-002** watch 私网出路句补 watch 进程 env 前题（ops.py `_WATCH_PRIVATE_HINT`）。

## 记录并接受

- R04-003/R04-004：backlog E7/E2/E10 的活体样本登记，无行为改动。

## 附：P3 侧良好行为确认

- 持久化路径经三轮攻击（库级双进程 / 旁证 set→kill→重启 / watch 带 flag）全部成立——「重启后报 robots_disallowed」恰是私网门放行的证明（拦它的是另一扇门，正话）。
- 混 set fail-closed（auth 无 host 整单拒、零落盘）被 P03 误当缺陷，攻击后确认是设计内不变式。
