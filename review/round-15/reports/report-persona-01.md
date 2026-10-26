# P01 沈砚 · 医学院图书馆员 L2（并行与隔离轮）

## 任务
一边查论文库，一边查门诊指南；两件事在同一个 MCP 进程里交错。需要确认：同一页用两种 selector 取两个片段时互不串味；两个具名会话各自填表不互相覆盖。

## 场景与读数
1. `sys_guide {"topic":"web_read"}` → 非 JSON 文本（已知 topic 列表），`topic_known=False`。A 类：我用了不存在的 topic，原文教学列出全部 topic。
2. 同页双 selector 交错（/article?cb=p01sel）：h1=10B、p=387B、h1 再读=10B；同 selector 两次一致=True，异 selector 不同=True；第三次 `cached=True`。**缓存键按 selector 隔离成立**。
3. 双 session 填名（/login，pin=true）：A 读回「沈砚」、B 读回「访客」。
4. 回 A session：`pin hit` 命中，读回仍是「沈砚」。**会话隔离 + pin 命中成立**。
5. `sys_state`：3 sessions open，0 cookies。

## 问题
- 无 B 类。
- A：sys_guide 的 topic 名不在名单（原文有全部名单，可一发自愈）。
- E：`content_ok=false`（auth_wall 判词）与动作行全 ok 同时出现——判词与动作分家（r12 已裁决为设计）。

## 评分
任务完成 4.5/5（隔离与 pin 全部按预期）；扣分仅在 A 类自查失误。
