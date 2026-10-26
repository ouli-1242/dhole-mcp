# P07 林一 · 教师 L1（保存与下载轮）

## 场景与读数
1. `web_read save_to=not-yet-r17/note.md`：`save_to parent directory … does not exist - create
   it first`，未抓取、未写。
2. 截图 `save_to=not-yet-r17b/shot.png`：**父目录自动创建**，文件落盘，动作行 ok。
3. `sys_guide topic="call"`：返回手册原文（非 JSON 文本）。

## 问题
- 无 B。
- E（R17-003）：同一 `save_to` 名下两种父目录语义（read 拒绝 / 截图创建）。对 L1 用户，
  第二发能成、第一发不能成，容易读成偶发；两句错误/回执都各自说清了。
- 量具注：sys_guide 非 JSON 原文被 driver 截 600 字（harness）。

## 评分
任务完成 4.5/5（语义不对称扣 0.5）。
