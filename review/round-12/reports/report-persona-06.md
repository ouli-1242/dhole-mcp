# P06 屠万里 · 高尔夫教练 L2 —— 链条⑥search→recall clamp 回归→read 落点

## 读数（5 发）

| 发 | 工具 | 读数 |
|---|---|---|
| S1 | web_search "golf swing basics" ×4 | 4 results（4 high）from baidu,yandex,seznam ✓ |
| S2 | web_recall limit=99 | `clamped: ['limit -> 20 (1-20)']`——r09 fix 回归 PASS |
| S3 | web_recall limit=5 | `clamped: None`（未越界安静）——r09 fix 的另一半 PASS |
| S4 | web_read search 第一行 | 200 · 6.6KB · http（真源新抓，2.5s）✓ |

## 合 · 不合

- **合**：clamp-and-name 合同在 recall 上双向成立（越界出声、未越界闭嘴）；search 结果
  行点得进（S4 落点 6.6KB 真文）。
- 无不合。

## 判定

链条⑥全绿。r09 必测回归点 1 原样在场。
