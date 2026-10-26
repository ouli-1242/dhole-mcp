# P07 闻溪 · 博物馆策展人 L2（并行与隔离轮）

## 任务
先画两张不同谓词的地图（只 /work、只 /article），再 crawl 三页，把地图里的详情页转 read，最后 recall。

## 场景与读数
1. discover `path_include="/work"`（seed `/`）：rows=1（只有起点页）；summary 明确「7 个同域候选链接全被 path_include/path_exclude 筛掉」。**筛空不是静默**。
2. discover `path_include="/article"`：rows=2（起点 + /article）。两张地图互不污染。
3. crawl `/` max_pages=3：3 页、1 content_ok（article:1, fallback:2）。
4. 详情页 read：ok=true，39B，cached=None（crawl 不播种 read 缓存）。
5. recall「工作 规格 展品」→ 0 行；note 说明磁盘上没有匹配。A 类：查询词与正文用词不一致。

## 问题
- 无 B 类。
- D/E（R14-004 复发）：谓词地图保留起点行（根行是「你从哪来」），文档未写根行例外。
- E（R14-005 仍在）：summary 仍说 `depth <= 2`，手册仍教 `max_depth`；文档由并发 agent 编辑中，本轮不碰。

## 评分
任务完成 4/5；地图谓词与筛空说明做得好，根行/文档漂移扣 1。
