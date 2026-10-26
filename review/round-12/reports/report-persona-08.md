# P08 闻岁酿 · 酒侍 L2 —— 链条⑧parse 双 sheet/GBK→feed 混发分账→sys_state 盘点

## 读数（5 发）

| 发 | 工具 | 读数 |
|---|---|---|
| S1 | doc_parse wine_list.xlsx（红酒/白酒双 sheet） | 200 · 150B；content=`## 红酒` 表 + `## 白酒` 表两段齐全 ✓ |
| S2 | doc_parse GBK 酒庄 CSV | content=`\| 酒庄 \| 产区 \|…张裕…沙城`——**GBK 保真**（张裕/沙城逐字在）✓ |
| S3 | feed 混发（好源+/feed-404.xml+拒连端口） | "3 URLs asked · 3 rows answered · 1 rows carried 3 items · **2 rows errored**"——逐行：items=3 / `http_error_404` / `network_error: connection_refused`，坏行不拖好行 ✓ |
| S4 | sys_state | message="2 session(s) open, holding 2 cookie(s)…"，cache entries/hits/misses 明细 ✓ |

## 合 · 不合

- **合**：r11 必测回归点 3/4 双 PASS（GBK 保真 + 混发逐行分账）；xlsx 双 sheet 各成
  `##` 段（r11 在册观察「多 sheet 小计」本轮实锤：两段内容齐全，sheet 名即标题）。
- **脚本侧打印 bug（非缺陷）**：首轮 `张裕=False`——doc_parse 的 content 是**字符串列表**
  （web_read 才是 dict.text 列表），我的取文代码按 dict 取。回执真值保真。sys_state 的
  账在 `message` 键不在 `summary`，同上。

## 判定

链条⑧全绿。坏行隔离与编码保真双回归 PASS。
