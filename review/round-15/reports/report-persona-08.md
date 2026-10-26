# P08 顾准 · 体育数据分析师 L3（并行与隔离轮）

## 任务
广谱搜索 + 订阅流，再批量读三页；同一页换 selector 取不同层，最后整批复读。

## 场景与读数
1. search「football data analysis」：3 条全 high，来自 sogou,so360,baidu,seznam，rerank=neural。
2. feed `since=2w`：since 绝对 ISO、older=0、items=4。
3. 批量读 /work/1..3：三行 fresh（`cached=None`），39/36/36B。
4. 同页换 selector：h1=4B、p=25B、整页=39B；三者不同；前两发 fresh，第三发 `cached=True`。**selector 维度的缓存隔离成立**。
5. 整批复读：三行全 `cached=True`，fetched_at 同为 `03:34:55Z`。

## 问题
- 无 B、无 E 新样本。搜索与缓存两条账都干净。

## 评分
任务完成 5/5。
