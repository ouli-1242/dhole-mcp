# round-16 环境日志（P0/P1）

> 服务 127.0.0.1:9216（dhole，pid 23108）+ 8399（靶站，pid 22360）+ **8398（多语言页面站，pid 26904）**。
> env DHOLE_IGNORE_ROBOTS=1 · DHOLE_USAGE_LOG=1。
> 分支 review/round-16 · 基线 6f262fa。**工作树含另一位 agent 的未提交改动，被测对象即当前工作树**。

## 启动与自测

- 2026-10-26T11:45:4x site-8398 STARTED（pid 26904，`python -m http.server --directory review/round-16/site`）。
- 2026-10-26T11:45:4x dhole-9216 STARTED（pid 23108）。
- 自测：server Dhole v17.2 · tools/list=**13**。
- 多语言页：`ar.html` 保真（阿拉伯文正确）；**`gbk.html` 被判二进制**（content 是
  `[Binary content detected …]` 占位句，content_ok=true）；`latin1.html` 重音变 U+FFFD。
- 本地 CSV：GBK（缺省与 encoding=gbk）、UTF-16、含 emoji/accent 的 UTF-8 —— **全部保真**。

## 多语言页面站（8398）

| 文件 | 编码/内容 | 用途 |
|---|---|---|
| ar.html | UTF-8 / 阿拉伯文 | RTL 保真 |
| ja.html | UTF-8 / 日文 + emoji | 日文与多字节窗口 |
| ko.html | UTF-8 / 韩文 | 韩文保真 |
| ru.html | UTF-8 / 西里尔 | 俄文保真 |
| emoji.html | UTF-8 / emoji + ZWJ + 组合字符 | emoji 链路 |
| gbk.html | **GBK 字节，服务端无 charset，meta 声明 gbk** | 编码探测 |
| latin1.html | **Latin-1 字节，服务端无 charset，meta 声明 iso-8859-1** | 编码探测 |
| mixed.html | UTF-8 / 多语言混合 | crawl/discover |

> `python -m http.server` 对 .html 只发 `Content-Type: text/html`（不带 charset）——
> 与常见静态服务器一致；meta 是页面自己声明的唯一线索。

## 靶站日志偏移

| 日志 | P0 时点 |
|---|---|
| evals/.runtime/orders.log | 7 行 |
| evals/.runtime/challenge.log | 2 行 |

## 备注

- 主题「编码与多语言」；P1 画像优先打非 ASCII URL / 正文 / 文件三面。
- 9215（r15 服务）仍在监听，互不干扰；本轮所有调用只打 9216。
