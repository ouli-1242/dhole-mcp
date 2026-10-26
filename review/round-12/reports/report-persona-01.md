# P01 洛砚舟 · 建筑师 L2 —— 链条①discover→read→parse→recall

## 读数（5 发：cred + discover + read + parse + recall）

| 发 | 工具 | 读数 |
|---|---|---|
| S1 | web_discover `/collections?items=6` | mapped 8 URL，页行=8（collections + work/1..6 + about），行 URL 全部真链 |
| S2 | web_read `/work/2` | 200 · 36B markdown · cache replay（58.1min 前代，账目自带 replay 诚实句） |
| S3 | doc_parse 材料单 CSV | 200 · 96B · parse ✓ |
| S4 | web_recall "作品二" | results=6，含 `/work/2`、`/work/2?t=1`——刚读的页已被 recall 索引 ✓ |

## 合 · 不合

- **合**：discover 地图行点得进（S2 直接 read 成功）；parse 吃得下；recall 查得到刚读页
  （链条①的四环全部咬合）。discover 行=8 与 items=6 对得上（6 work + collections + about）。
- **脚本侧误读（非缺陷）**：首轮打印用 `pg.get("links")` 取行得 0——discover 的行就是
  `pages` 本身（每行 url/status/title/summary），没有 links 键。回执无错，量具错。

## 判定

链条①全绿。跨工具行可点性成立。
