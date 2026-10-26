# P06 唐栗 · 兽医 L2 —— 本地 PDF 指定页 · feed 一周 · 回访缓存

## 读数（6 发：cred + parse + feed + read×2 + state）

| 发 | 工具·形状 | 读数 |
|---|---|---|
| S1 | doc_parse PDF pages=2-3 | content_ok=true、1304 字；`pages_returned=2`、`pages_total=3`；TOC 两节落在 2/3 页 |
| S2 | feed_read since=1w max=3 | `since=2026-10-19T03:12:54Z`（= 当下减 7 天）、older=1、items=3 |
| S3a | read /article?cb=p06-dose | live、387B、fetched_at=03:12:54Z |
| S3b | 同 URL 再读 | cached=true、同一时间戳 |
| S4 | sys_state | Nothing is open / Nothing erased |

## 合 · 不合

- **合**：PDF 页范围真的生效（2/3 页），TOC 与内容能对上；feed 的相对 `1w` 从当下起算，
  计数与日期自洽；回访缓存账诚实。
- **不合（小）**：doc_parse 的 summary 只说「1.3KB markdown · parse」，**不点名 pages=2-3**；
  用户要翻到回执字段 `pages_returned/pages_total` 才知道页范围被尊重。字段在、总结没说，
  属 E 类（交付话术）。

## 摩擦清单

1. PDF 页范围的确认要读字段，summary 不点名（E，无感）。
2. sys_state 的「Nothing is open」对 L2 还算清楚，但没有「缓存里有多少页」的数字（message
   说 counts ride along，没展开；可接受）。

## 判定

- PDF 指定页 PASS（字段旁证）；feed 相对 since PASS；缓存回放 PASS。
- E 记录：doc_parse summary 可点名页范围。

## 主观评分

4.5/5 —— 药典页和公告都拿到了；页范围这句没说出口。
