# P05 小满 · 社媒运营 L1（编码与多语言轮）

## 任务
emoji 页面两读、emoji recall、近三天订阅、中文+emoji 标签页。

## 场景与读数
1. `emoji.html?cb=😀🚀`：两读都 ok，emoji 保留，第二次 `cached=True` 且时间戳相同。
   （首读也 `cached=True`——P03 刚读过同一 URL；跨画像共享缓存，量具注。）
2. `web_recall "emoji 😀 表情"`：terms=['emoji','表情']，命中 emoji 页——emoji 查询不崩，词元按 ASCII+CJK 切分。
3. `feed_read since=3d`：since 绝对 ISO、older=3、items=4，标题中文正常。
4. `/article?cb=标签😀`：387B，emoji 参数不影响抓取。

## 问题
- 无 B 类。
- 量具注：跨画像同 URL 导致「首读 cached=True」；报告不据此立案。

## 评分
任务完成 4.5/5（缓存可见性扣 0.5，属量具）。
