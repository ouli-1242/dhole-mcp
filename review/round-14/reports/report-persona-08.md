# P08 马小川 · 体育数据分析师 L3（猎手）—— 相对 since 6h · 混发 · clamp · 回访

## 读数（6 发：cred + feed×2 + search + read×2）

| 发 | 工具·形状 | 读数 |
|---|---|---|
| S1 | feed since=`6h` | `since=2026-10-25T21:14:26Z`（= 当下减 6h）；older=5、nodate=2、items=3（1 条近期 + 2 条无日期） |
| S2 | feed 好源+404 混发 | 2 行：好源 2 条；坏源 `http_error_404` 逐行具名 |
| S3 | search max_results=0 | `outside the supported 1-50 range; returning at most 1`（summary 内） |
| S4a | read /article?cb=p08-report | live、387B、fetched_at |
| S4b | 同 URL 再读 | cached=true、同一时间戳 |

## 合 · 不合

- **合**：`6h` 的相对窗口从当下起算，返回 1 条近期 + 2 条无日期（保留策略正确），
  `older=5` 把 6 条带日期里挡掉的 5 条数出来；混发 404 不连坐；search clamp 具名；
  回访缓存账诚实。
- **不合**：无实质缺陷。feed 的 `items_older_than_since` 字段名对 L3 可读，对 L1 不友好
  （P03 已记 E 类）。

## 摩擦清单

1. 无日期条目保留是正确行为，但 summary 不单列「2 条无日期已保留」，要读字段
   `items_without_date`（无感）。
2. 引擎池里 yandex/yahoo 时有时无，结果行数会漂（环境，非缺陷）。

## 判定

- 相对 since/混发/clamp/回访四项 PASS；未发现新 B 类。

## 主观评分

5/5 —— 增量、坏源、越界、缓存四本账全对。
