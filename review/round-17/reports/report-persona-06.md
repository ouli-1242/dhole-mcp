# P06 杜衡 · 数据员 L2（保存与下载轮）

## 场景与读数
1. `pdf` 动作落盘：`saved_bytes=110,377`，文件头 `%PDF-`。
2. `doc_parse` 读回：ok=true、553 字，头部是本 PDF 的 Created/Modified 元数据。
3. 只读 `pages="1"`：553 字，与整份相同——这份 PDF 只有一页，合理。

## 问题
- 无 B。PDF 落盘与本地解析往返成立。
- E（R14-003 复发）：doc_parse summary 只说 `553B markdown · parse`，不点名页范围；字段层准确。

## 评分
任务完成 4.5/5（页范围措辞扣 0.5）。
