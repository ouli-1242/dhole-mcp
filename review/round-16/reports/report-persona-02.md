# P02 莱拉 · 阿拉伯语译者 L2（编码与多语言轮）

## 任务
读一份阿拉伯语 RTL 页面，用阿拉伯语参数复读；再用阿拉伯语用户名走登录 + 会话读。

## 场景与读数
1. `ar.html?cb=عربي`：ok=true、150B，正文从右到左的阿拉伯文完整（逐字校验含 U+0600–U+06FF），无乱码。
2. 同页复读：`cached=True`，字符数一致——阿语 URL 参数作为缓存键工作正常。
3. POST /login，`username=ليلى`：status=200，非 ASCII 表单体无损；随后带 session 读 /account = 23B「欢迎回来…」。
4. sys_state：2 sessions、1 cookie。

## 问题
- 无 B 类。
- 量具注：脚本里的 `has_arabic` 断言误把 `\u0600` 写成双层转义，打印 False；已用回执码点复核为 True（scripts/p02.py 已修）。

## 评分
任务完成 5/5。
