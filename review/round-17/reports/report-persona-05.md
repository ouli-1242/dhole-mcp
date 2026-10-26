# P05 何清 · 审计员 L2（保存与下载轮）

## 场景与读数
1. 上传中文名 `审计清单-中文.txt` + 点击提交（未带 confirm）：上传行 ok，click 行
   `pending_approval: submitting is an irreversible step`——写闸按预期拦下，POST 未发生。
2. 补充（p12）：click 动作内 `confirm: true` → 真提交，读 `#receipt` =
   `received 审计清单-中文.txt (26 bytes)`。**中文文件名与字节数无损**。
3. 上传不存在的文件：`invalid_request: action 0 'upload': file not found`——起浏览器前拒绝。

## 问题
- 无 B。
- A：我最初读 `#status`（靶站实际 id 是 `#receipt`），两发 selector 落空；读页面真实 id 后全通。
- 写闸教学正确：confirm 必须在动作内（r13 已立此规）。

## 评分
任务完成 4.5/5（A 类自查失误扣 0.5）。
