# P03 阿吉 · 咖啡店主 L1（并行与隔离轮）

## 任务
两条订阅流（近三天 / 近两周）+ 一篇长文分两片读，交错进行。

## 场景与读数
1. 订阅 A `since=3d`：回执 `since=2026-10-23T03:32:43Z`（绝对 ISO）、older=3、items=5。
2. 订阅 B `since=2w`：`since=2026-10-12T03:32:47Z`、older=0、items=5。**相对窗口按当下回量正确**。
3. 长文第一片：`max_content_chars=400` 被具名钳到 500（supported 500-200000）；summary=`478B of 7.0KB`、truncated、next=478。
4. 续片 offset=478：summary=`473B of 7.0KB`、仍是切片；回放句说明这是缓存里的同一抓取。
5. 两条流同发 `since=3h`：各 older=5、items=3。

## 问题
- 无 B 类。
- E（在册）：L1 仍会遇到 max_content_chars 下限 500 的钳制（已具名，可自愈）。
- E：offset 窗口只给「本窗 of 总量」，不含起止字符坐标（r14 已裁决为契约形状）。

## 评分
任务完成 5/5；两窗口与两条流账目一致，没有串味。
