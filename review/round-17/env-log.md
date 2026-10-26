# round-17 环境日志（P0/P1）

> 服务 127.0.0.1:9217（dhole，pid 7240 → 修复后重启 15880）+ 8399（靶站，pid 22360）
> + 8398（多语言站，pid 26712，目录 round-17/site）。
> 分支 review/round-17 · 基线 b60a90a。工作树含另一位 agent 的未提交改动。

## 开工与 R16-001 接线

- 2026-10-26T14:11 现状：另一位 agent 仍在改 src 注释（security/pdf_extractor/models/feed/
  search_engines/reranker 的 mtime 13:55-14:11）；**fetcher.py 自 11:50 未再动**。
- 14:12 把 `round-16/fixes/test-R16-F1-draft.py` 接进 `tests/test_a_page_declaring_gbk_in_meta_decodes_as_gbk.py`，
  先红（ImportError：sniff_html_charset 不存在）。
- 修复（仅 fetcher.py）：
  1. `sniff_html_charset(body)`：前 16KB 的 `<meta>` 里读 `charset=`（含 http-equiv 形状）。
  2. `Response.encoding`：HTTP 头**明确**声明 charset 时听头；否则 sniff `<meta>`，再退回原值。
  3. `_decode_html_bytes`：UTF-8 声明先严格解；失败时按 meta 解；字节合法 UTF-8 时仍听字节。
- 单测 4/4 绿；14:15 真重启 9217（pid 7240→**15880**）后真机复验：
  - `gbk.html` → 「国标编码页面 / 这是一个 GBK 编码的页面…」；
  - `latin1.html` → `français … é è ê à ç ù`；
  - crawl 4 页里 gbk.html 也不再是乱码。

## 自测

- server Dhole v17.2 · tools/list=13 · 真实 search ok。
- 8398 站点目录 = review/round-17/site（round-16 的 8 个多语言页原样复制）。

## 备注

- save_to 用例的落盘目录：`review/round-17/sessions/files/`；遍历用例落在 sessions/ 内并由脚本清理。
- 9216（r16 服务）仍在监听，互不干扰；本轮只打 9217。
