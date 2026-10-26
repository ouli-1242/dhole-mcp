# P05 程野 · 数据记者 L3（并行与隔离轮）

## 任务
两个 research 问题并行；中途 crawl 靶站三页，再把 crawl 见过的详情页改用 read 读，确认两代缓存不互相冒充。

## 场景与读数
1. research 第一问：2/2 sources，6.3s，more_hits=5。
2. crawl `/` max_pages=3：3 页、1 content_ok（article:1, fallback:2），stopped=max_pages reached。
3. crawl 后的 /work/1 改用 read：`cached=None`、39B——**crawl 不向 read 缓存播种（r10 代际隔离仍成立）**。
4. 同页复读：`cached=True`，但回放 fetched_at=03:33:34Z，比 live 的 03:33:35Z **早 1 秒**（跨秒边界）。
5. research 第二问：1/1 source，13.3s。

## 问题
- **E（新样本）**：live 与缓存回放的 `fetched_at` 不总是逐位相同——live 在 `_with_agent_hints` 末尾盖 `datetime.now()`，缓存行存的是更早的 `time.time()`；跨秒边界即差 1 秒。字段描述承诺「For live fetches the two coincide」。方向安全（回放不更“新”），但严格合同不成立。
- D（在册）：crawl/read 两代缓存（设计如此，回执 cached 字段如实）。

## 评分
任务完成 4.5/5；research 与代际隔离全对，时间戳一致性扣 0.5。
