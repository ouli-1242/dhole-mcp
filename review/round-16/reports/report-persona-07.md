# P07 金敏 · 韩语用户 L2（编码与多语言轮）

## 任务
韩文页读两遍；韩文搜索；韩文 URL 参数的靶站页。

## 场景与读数
1. `ko.html`：96B，韩文（U+AC00–U+D7A3）完整，无乱码。
2. `web_search "한국어 인코딩 테스트"`：2 条全 high（sogou,so360,yandex,baidu），标题韩文正确。
3. 靶站 `/article?cb=한국어`：ok=true、387B；复读 `cached=True`，字符数一致。
4. 无错误、无截断。

## 问题
- 无 B 类。韩文正文、搜索、URL 参数三面成立。
- 量具注：脚本的 has_hangul 断言双层转义误报 False，回执码点复核为 True，脚本已修。

## 评分
任务完成 5/5。
