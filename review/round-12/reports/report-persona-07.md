# P07 慕青梧 · 殡葬策划 L1 —— 链条⑦crawl→discover 两把尺对账→断链如实

## 读数（4 发）

| 发 | 工具 | 读数 |
|---|---|---|
| S1 | web_crawl `/` max_pages=3 | 3 页（/, /article, /qr）status 全 200 ✓ |
| S2 | web_discover `/collections?items=4` | mapped 6 URL（collections + work/1..4 + …），work 行=4 与 items=4 对上 ✓ |
| S3 | web_read `/work/999` | **200 · 40B**——不是 404！ |

## 合 · 不合

- **合**：两把尺各记各账（crawl 抓到的页 vs discover 地图行），行数互不串。
- **S3 的 200 是真话，我的前提是错的**：靶站 `/work/N` 对任意整数都渲染真页
  （`_work_page(n)` 无 404 分支，curl 实证 `/work/999` → 200）。工具如实回了 200，
  是画像卡把「断链」押错了行。真 404 由 p05b S8 补上：`/no/such/page` →
  `http_error_404: server returned error status`，status=404 如实 ✓。

## 判定

链条⑦全绿（404 判定移到补条）。靶站认知错误记录在案——不是工具缺陷。
