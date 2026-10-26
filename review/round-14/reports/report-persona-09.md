# P09 花婶 · 园艺师 L1 —— feed 一周 · 本地 xlsx · 回访一页

## 读数（6 发：cred + feed + parse + read×2 + state）

| 发 | 工具·形状 | 读数 |
|---|---|---|
| S1 | feed since=`1w` max=3 | `since=2026-10-19T03:15:43Z`（= 当下减 7 天）、older=1、items=3 |
| S2 | doc_parse care_table.xlsx | content_ok=true、78 字；「月季」「绣球」都在 |
| S3a | read /work/1?cb=p09-price | live、39B |
| S3b | 同 URL 再读 | cached=true、同一 fetched_at |
| S4 | sys_state | 1 session、0 cookie、Nothing erased |

## 合 · 不合

- **合**：相对 `1w` 的账对；xlsx 单表直接读出两行花卉；回访第二读来自缓存且不装新鲜。
- **不合**：L1 用户看到「fetched_at」「cached copy」还是需要人翻译；没有造成误解，只是
  阅读成本。已并入 P03 的 E 类记录。

## 摩擦清单

1. 缓存/时间的工程词对 L1 不友好（E，既有）。
2. feed 的 `older=1` 没有一句人话总结（E，既有）。

## 判定

- feed 相对 since / xlsx / 回访 PASS；无新 B。

## 主观评分

5/5 —— 一周新品、养护表、行情页，三件事都办成了。
