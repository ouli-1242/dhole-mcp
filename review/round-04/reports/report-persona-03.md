# 画像报告 · P03 秦守拙（档案管理员 · L2）· round-04

> 主题：cred_defaults 落盘跨重启 + watch 用落盘豁免真查私网。3 段旁证生命周期 + 2 次 CLI（p03-run.log + 补测 probe 输出）。

## 场景实录

- **1 存**：旁证 9206（DHOLE_PERSIST_CREDENTIALS=1）`cred_defaults set allow_private` → 「stored allow_private. 1 key(s) in force.」→ 硬杀进程。
- **2 重启读**：新旁证同 env → 私网读 → **不再报私网门**，报的是 `robots_disallowed`（旁证 env 无 IGNORE_ROBOTS，robots 门拦——这正证明私网门凭落盘的豁免**通过了**）。identity.db 12KB 在盘（C:\Users\ouli\.dhole\）。
- **3 watch 出路兑现（R02-001 的另一半）**：主 shell 不带 flag → `dhole watch run` exit 2（错误行如 R02-001 所教）；**带 flag 重跑 → baseline HTTP 200、exit 0**——落盘豁免在 watch 进程里被读到，私网 watch 真的能跑。
- **首试败因（脚本之过，记录在案）**：第一版把 allow_private 和 auth 混在一次 set 里——auth 属凭据键必须带 host，整单被拒（fail-closed，一个字都没落盘）；脚本没看返回值就重启，误判成「持久化坏了」。复测单键 set 全通。
- **memory 对照**：主进程 9204（memory 模式）的豁免重启即清零——round-03 P01 已真撞过，本轮不重复造。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 根因 | 严重度 |
|---|---|---|---|---|
| 首试 | 「怎么重启就没了——哦，是我 set 被整单拒了」 | 混 set fail-closed；脚本没读返回值 | 无感（拒得对） |
| watch 无 flag | 「还是要 watch 进程自己也带 flag」 | R02-001 出路句没说 flag 得在 **watch 进程**的 env 里（候选 R04-002） | 皱眉（轻微） |

## 放弃点

无。

## 主观评分

- 第一印象 4 / 速度 4 / 可信 5 / 报错可读 4（出路句差半个前题）/ 等待 4 / 总体 4.4。

## 改进清单

1. 【皱眉→希望】watch 出路句补「flag 要在 watch 进程的环境里」（R04-002）。
2. 【保持】混 set 的 fail-closed 整单拒（一个字不落盘）。

## 附录

证据：p03-run.log + 补测三段（set 全回执 / 重启读 robots_disallowed / watch 200）。
