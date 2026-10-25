# fix-R02-F5 · R02-007 recall 同 URL 变体多行出门（stale 标注打架）

- **现象**：P10 recall 结果里同一 URL 两行，`is_stale` 一真一假并排——「到底信哪行」。跨轮同形：R01 P04 首报（E2），R02 P10 第二现且升级（可信度伤害，不只是重复）。
- **根因**：存储层按**抓取变体**存（同一 URL 的 markdown/html、不同时刻各一行），recall 结果组装按行直出，没有 URL 级合并。诚实的是每一行自己，撒谎的是并排的它们。
- **修法**：recall.py 新增 `_merge_url_variants(rows)`——按 url 分组，`fetched_at` 最新的胜出；合并数 ≥2 时行带 `variants=N`；顺序按每个 URL **首次出现位**稳定输出；合并**在 limit 截断之前**做（被合并的变体不占名额）；胜者行原样保留（额外键如 `i` 穿透）。
- **测试**：`tests/test_recall_merges_the_variants_of_one_url.py`——两变体合一行带 variants=2、fetched_at 最新胜出、is_stale 按胜者算、单变体不带 variants 键、合并先于截断。
- **线上证据**：sessions/verify/w4-recall.json——`web_crawl /collections` 两轮（max_pages=3 与 1）后 `web_recall query='推荐专题'`：/collections **单行**，`variants: 2`，`stale: False`（其余 45 分钟前的旧页 stale: True 各归各行，正确）。
