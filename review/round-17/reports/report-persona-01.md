# P01 周岚 · 研究员 L2（保存与下载轮）

## 场景与读数
1. 中文搜索「网页 归档 保存 工具」：2 条全 high（bing,baidu,so360,sogou，rerank=neural）。
2. `web_read save_to=研究笔记.md`：content=「saved 1,265 bytes to …」，metadata
   `saved_to/bytes/sha256` 齐全；磁盘 1265B，**sha256 与回执一致**。
3. 同路径二次保存：`invalid_request: … already exists - refusing to overwrite it`，
   next_action「Nothing was fetched and nothing was written」——**先拒后发，未浪费下载**。
4. 换名再存：成功，1265B。

## 问题
- 无 B。覆盖门、字节账、中文路径三面都对。
- E（R17-002）：summary 写 `82B text`，82 是**保存回执句**的长度，不是正文；`extracted_type=text`
  也像是「抽取过」。内容与 metadata 已说清，属措辞。

## 评分
任务完成 5/5。
