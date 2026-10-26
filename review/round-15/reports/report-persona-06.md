# P06 林澄 · 兽医 L2（并行与隔离轮）

## 任务
两份本地资料交错解析：药品 PDF 指定页、剂量 XLSX；再用网页对照读。

## 场景与读数
1. PDF `pages="2-3"`：ok=true，1304 字，summary `1.3KB markdown · parse`。
2. XLSX：ok=true，86B，正文含「犬」「猫」。
3. PDF `pages="1"`：1053 字，与 2-3 片不同=True。**页范围真生效且逐次独立**。
4. `/about` 网页读：ok=true，12B。

## 问题
- 无 B 类。
- E（在册 R14-003）：`doc_parse` summary 只说 `1.3KB markdown · parse`，不点名 `pages=2-3` 与总页数；字段层准确。
- E（r06 在册）：XLSX 无每 sheet 小计。

## 评分
任务完成 4.5/5；两份文件交错无串味。
