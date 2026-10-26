# P04 罗盘 · 供应链分析师 L2（并行与隔离轮）

## 任务
三份供应规格页批量读，随后 recall 找回；再走一次写闸（先被拒、再确认），最后用自己的登录会话对照匿名读。

## 场景与读数
1. 批量读 /work/1..3：三行全部 `content_ok=true`（39/36/36B），但**首读三行 `cached=True`、fetched_at=03:10:51Z**——这是本进程之外（r14 服务/共享磁盘缓存）读过的同 URL（cb=p04-* 与 r14 脚本重名）。量具注，不是本轮冷读。
2. recall「规格 供应 工作」→ 0 行；回执 note 明确「nothing on this disk matches」。A 类：我猜的词不在这些页正文里（页正文是「作品 1 / … / 第 1 篇…」）。
3. 写闸缺 confirm：`invalid_request: method=POST is a write…`，next_action 教同一发加 `confirm=true`。
4. confirm=true：status=200，真写落 orders.log。
5. 自有 session 登录后 /account=23B（「欢迎回来…」），匿名 /account=14B（「需要登录…」）。**登录/匿名隔离成立**。

## 问题
- 无 B 类。
- 量具注：跨轮共享缓存使「首读」不冷——报告里不得把它读成缺陷。
- A：recall 查询词脱离正文。

## 评分
任务完成 4.5/5（批量/写闸/隔离全对；recall 未命中系查询词问题）。
