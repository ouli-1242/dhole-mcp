# P10 江照 · 编剧 L1（并行与隔离轮）

## 任务
一篇长文分片读，同时看近三小时的订阅，再用两个上下文的关键词 recall。

## 场景与读数
1. 长文第一片：`max_content_chars=450` 钳到 500；summary `478B of 7.0KB`、truncated。
2. 续片 offset=478：summary `473B of 7.0KB`、仍是切片；回放句在场。
3. feed `since=3h`：since 绝对 ISO、older=5、items=3。
4. recall「长文 脚本 素材」→ 5 行；前两行都是靶站索引页，一条 URL 为 `/`（variants=2）、另一条为 `/`（无斜杠），is_stale 一真一假。
5. 长文再读：`cached=True`，与第一片 fetched_at 相同。

## 问题
- 无 B 类。
- E2 复发：尾斜杠 `/` 与 `/` 被当成不同 URL，同一页两行并排、is_stale 打架（R02-007 只合并完全相同的 URL）。
- E6：recall 命中里混入索引页，语义排序按既定语料。

## 评分
任务完成 4/5；分片与订阅全对，召回页身份去重扣 1。
