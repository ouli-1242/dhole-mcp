# P09 罗薇 · 图书管理员 L2（保存与下载轮）

## 场景与读数
1. `web_read /qr.png save_to=qr17.png`：5,311B，磁盘头 `89 50 4E 47 0D 0A 1A 0A`——真 PNG；
   content 是「saved 5,311 bytes to …」，不把二进制塞进正文。
2. crawl 多语言站 3 页：3/3 content_ok。
3. discover：9 URL、4 行本次出门，plan 持有其余行。
4. feed `since=2w`：since 绝对 ISO、older=0、items=3。

## 问题
- 无 B。二进制保存、crawl/discover/feed 都正常。
- 量具注：PNG 头断言最初误报 False（脚本转义），字节复核为真，已修脚本。

## 评分
任务完成 5/5。
