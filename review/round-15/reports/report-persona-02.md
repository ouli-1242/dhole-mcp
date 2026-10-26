# P02 陆知微 · 专利审查员 L3（并行与隔离轮）

## 任务
同一份「会变的页面」既要读正文，又要做变化核对；同时用登录会话读账户页，再匿名读同一页做对照。

## 场景与读数
1. 冷读 /changing?cb=p02mix：`cached=None`，`fetched_at=2026-10-26T03:32:27Z`。
2. 复读：`cached=True`，两次 `fetched_at` 相同。
3. check 第一发：`1 baseline recorded`，行 note 说明无基线、已记录。
4. check 第二发：`1 changed`，`changed=True`，note 给 diff 语义。**两相成立，且与 read 缓存互不搅乱**。
5. 登录：POST /login（session_id=p02login, confirm=true）→ status=200；带 session 读 /account=23B「欢迎回来…您已登录…」，匿名读同页=14B「需要登录…您没登录」。**会话隔离成立**。

## 问题
- 无 B 类。
- E（跨画像共用）：live 回执 `fetched_at` 与随后缓存回放的 `fetched_at` 在秒边界上可差 1 秒（p05 实测 03:33:35Z vs 03:33:34Z），字段描述写「live 时两者重合」。不影响语义方向（回放不更“新”），但严格契约不成立。

## 评分
任务完成 5/5；时间戳 1 秒级不一致记 E，不挡任何决策。
