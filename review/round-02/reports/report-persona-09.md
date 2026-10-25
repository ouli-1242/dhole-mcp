# 画像报告 · P09 韩青（财务会计 · L2）· round-02

> 报销季：xlsx/GBK csv、真 PDF、假后缀、批量静态页。5 发（p09-*）。

## 场景实录

- **S1 表格双发**：sample.xlsx → 69 字 markdown 表（Name/Score/Alice/Bob）「直接能贴表」；gbk_sample.csv → **encoding=gb18030 自动识别**、中文表头无乱码。「没问我要不要选编码。」
- **S2 真 PDF**：background_checks.pdf 7.7KB，**合并单元格如实披露**（「11 merged cell(s) across 1 irregular table(s)…rendered as text rows, not a markdown grid」）——会计最怕的就是表被静默摊平。
- **S3 假后缀**：文本假装 PDF → `not_a_pdf: body does not start with %PDF`，具名诊断零等待。「是文件不行，不是它不行。」
- **S4 批量 404**：夹具没有 /invoice 页 → 3 行 content_ok=false、顶层「0 answered without an HTTP error」——「successful 的账和能引用的账分开说」再次有效；行里也带 error。（夹具缺口如实记录。）

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因 | 严重度 |
|---|---|---|---|---|---|
| 00:59 | S4 三张 404 | 「哦这站没这些页」 | 1.7s | 夹具缺口（非工具） | 无感 |

## 放弃点

无。

## 主观评分

- 第一印象 5 / 找答案速度 5 / 答案可信度 5（合并单元格披露是杀手锏）/ 报错可读性 5 / 等待感受 5 / 总体 4.9。

## 改进清单

1. 【保持】encoding 自动识别 + 声明；merged cells 披露；not_a_pdf 具名诊断。
2. 【希望】404 行的 total_extracted_chars=3 这种「错误页也被抽取」的数字可以不展示（/error 行的数字没人在乎）。

## 附录

证据：sessions/persona-09/01-05；02（gb18030）、03（merged cells）、04（not_a_pdf）。
