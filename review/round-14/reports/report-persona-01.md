# P01 方寸 · 图书馆采编员 L2 —— feed 相对窗口 → 回访缓存 → recall

## 读数（7 发：cred + feed×2 + read×3 + recall）

| 发 | 工具·形状 | 读数 |
|---|---|---|
| S1 | feed_read since=`3d` | 受理；行内 `since=2026-10-23T03:08:08Z`（= 当下减 3 天）；items=5、older=3、nodate=2 |
| S2 | feed_read since=`2026-10-24`（备用） | `since=2026-10-24T00:00:00Z`、older=3、items=5——相对窗口已够用，ISO 只是兜底 |
| S3a | web_read /article | live · 387B · `fetched_at=03:08:20Z` |
| S3b | 同 URL 再读 | **cached=true**，`fetched_at` 与首读逐位相同；summary 用过去时讲「replayed from the cache」 |
| S4 | 同 URL `cache_ttl=0` | cached 缺席、`fetched_at=03:08:29Z`——强制回源如实 |
| S5 | web_recall「缓存 抓取」 | 5 行；前三条是同一篇文章的三个 URL 变体（`/article`、`?t=html`、`?cb=…`） |

## 合 · 不合

- **合**：`3d` 不是被当成字符串忽略，行内 `since` 精确落在「当下减 3 天」；`older=3` 与
  5 条返回、2 条无日期保留对得上（3+5 条带日期 = 源里 8 条）。回访第二读的缓存账诚实：
  时间戳不重盖、过去时说明回放。`cache_ttl=0` 真回源。
- **不合**：recall 把同一篇文章的三个 URL 变体排在一起，占掉前三行——这是 E2 家产的再次
  出现（搜索/召回按 URL 变体不去重），对「找回上周那一页」的用户来说等于只找到一页。

## 摩擦清单

1. recall 三行同文不同 URL（E2 复发，皱眉）。
2. recall 命中没有相关度分数，L2 只能靠 snippet 猜哪条是真要的那页（既有形状）。

## 判定

- 相对 since PASS（E8 家产：本轮起可用 `3h/3d/2w`，日期仍兼容）。
- 缓存回放诚实 PASS；`cache_ttl=0` PASS。
- recall 变体重复：E2 记录（不记新 B，除非 P3 证明是回归）。

## 主观评分

4/5 —— 增量与回放两件事都说得清；扣分在 recall 把一页拆成三行。
