# 画像报告 · P09 迟桉（自由翻译 · L2）· round-03

> 主题：doc_parse 多 sheet/GBK/假 PDF + 外网 401 杠杆。4 发。p09- 前缀。

## 场景实录

- **S1 三区报表（多 sheet + 合并单元格）**：三个 sheet 全出（华东/华北/备注），合并单元格格如实交底：「the grid is this sheet's used range, not one table's columns: 3 column(s) wide - 1 carry a title, 2 carry no header cell: B holds data in every row - the title is missing, the column is not…」。
- **S2 GBK 老表格**：tests/gbk_sample.csv → 117 字符，编码自识（R02 良好行为保持）。
- **S3 假 PDF**：改扩展名的文本 → `PDF extraction failed: not_a_pdf: body does not start with %PDF`——具名（回归 PASS）。
- **S4 原文站 401（R02-003 回归）**：WSJ → status 401、recover_with「store credentials for this host with cred_defaults (set={auth: {type:'basic',username,password} or {type:'bearer',token}}), or switch source / the Internet Archive copy」——杠杆与 next_action 同源在线上活着。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因 | 严重度 |
|---|---|---|---|---|---|
| S1 | 「备注页那格为什么跨三列——它解释了」 | 0 | merged cells 披露 | 无感 |
| S4 | 「墙就是墙，出路给了」 | 0 | 401 杠杆 | 无感 |

## 放弃点

无（假 PDF 换真件是正常工作流）。

## 主观评分

- 第一印象 5 / 速度 5 / 可信 5 / 报错可读 5 / 等待 5 / 总体 4.9。

## 改进清单

1. 【保持】merged cells / not_a_pdf / 401 杠杆的具名披露组。
2. 【希望】多 sheet 各自的字数小计（现在只有总数 541，要分配额得自己拆）。

## 附录

证据：sessions/persona-09/ + sessions/files/（自造假 PDF 与三区 xlsx）+ p09-run.log。
