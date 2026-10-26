# P03 周小满 · 咖啡馆店主 L1 —— 「为什么第二次快」

## 读数（6 发：cred + guide + read×2 + feed + state）

| 发 | 工具 | 读数 |
|---|---|---|
| S1 | sys_guide topic=fields | 拿到字段手册（驱动只留 600 字原文，量具限制，见下） |
| S2a | read /article?cb=p03-menu | live、387B、fetched_at=03:10:22Z |
| S2b | 同 URL 再读 | cached=true、同一 fetched_at；summary「replayed from the cache…」 |
| S3 | feed_read max_items=2 | 2 条，newest=靶站条目 1（0 天前） |
| S4 | sys_state | 1 session、0 cookie、Nothing erased |

## 合 · 不合

- **合**：同一页第二读确实快（0.0s vs 0.1s）且回执不装「刚抓的」——`fetched_at` 留着
  首读时刻，summary 用过去时解释回放。`sys_state` 的「Nothing erased」对 L1 是句人话。
- **不合（L1 视角）**：
  1. 「replayed from the cache」「fetched_at」这类词对咖啡馆主是天书。字段手册本可以救，
     但驱动把非 JSON 的 guide 原文截在 600 字（**harness 量具问题，不是 dhole**），
     所以这条摩擦只能记「产品文案偏工程」。
  2. feed 的 summary「1 URLs asked · 1 rows answered」也是工程腔；L1 只想知道「有没有新的」。

## 摩擦清单

1. 缓存回放句是工程英文，L1 读不懂（E 类，措辞）。
2. feed 行话对 L1 不友好（E 类，既有形状）。
3. guide 原文被 harness 截断——量具问题，记在 env-log，不记产品缺陷。

## 判定

- 回访缓存诚实 PASS；feed PASS；state PASS。
- L1 摩擦 1/2 转 E backlog（不新开 B）。

## 主观评分

4/5 —— 事实都对，话不像对店主说的。
