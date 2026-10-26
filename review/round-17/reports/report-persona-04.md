# P04 沈括 · 档案员 L2（保存与下载轮）

## 场景与读数
1. `max_save_bytes=50`：`Response body too large (1,265 bytes = 1.2KB, over the save_to limit of 50)
   - nothing was written`，磁盘无文件。
2. `max_save_bytes=10000`：落盘 1,265B。
3. `web_recall "归档 保存 笔记"`：terms=['保存','笔记']，1 行。

## 问题
- 无 B。小帽拒绝具名、给抬帽上限（1,073,741,824）与替代路线（web_request/curl）；不落半截文件。
- E：错误行语气混用英文/数字格式，但信息完整。

## 评分
任务完成 5/5。
