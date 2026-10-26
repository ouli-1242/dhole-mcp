# round-17 轮报告（保存与下载往返）

> 主题：save_to / max_save_bytes / 截图·PDF 落盘 / 二进制保存 / 上传 / 本地文件读回。
> 10 画像 + 3 补条 · **77 发** · 13/13 工具 · 端口 9217/8399/8398 · 基线 b60a90a。
> **本轮首位工作是接线 round-16 的 R16-001 修复**（test-first，4 枚测试；真机已验证）。

## 结论

- **1 fixed（结转）：R16-001** —— meta-only 编码页按 `<meta>` 解码。修复在 `fetcher.py`：
  `sniff_html_charset` + `Response.encoding` + `_decode_html_bytes` 三层；GBK/Latin-1 的
  read 与 crawl 全部恢复，4 枚新测试绿。
- **0 个新 CONFIRM**：6 条候选全为 E/D/A/量具。
- 保存面的正向实锤：
  1. 覆盖门：`web_read save_to` 对已存在文件**先拒后发**，不浪费下载（P01）。
  2. 小帽：`max_save_bytes=50` 具名拒绝、不落半截文件；抬帽上限与替代路线都在句子里（P04）。
  3. 系统目录：`C:/Windows/...` 写前拒绝，未产生请求（P02）。
  4. 字节账：saved_to/bytes/sha256 与磁盘一致；中文/emoji 文件名都可落盘（P01/P08）。
  5. 截图/PDF：PNG 头、`%PDF-` 头正确；PDF 可被 `doc_parse` 读回（P03/P06）。
  6. 二进制：`/qr.png` 按字节保存，正文不放二进制（P09）。
  7. 上传：中文名文件 26B 无损，`#receipt` 回执点名文件名与字节数（P05/P12）。

## 问题清单（P3 后）

| # | 级别 | 现象 | 去向 |
|---|---|---|---|
| R16-001 | **B（结转）** | meta-only GBK/Latin-1 页被当 UTF-8 | **已修**（4 测试 + 真机复验） |
| R17-001 | E | 空 `save_to` 的拒绝句点名 `max_save_bytes`（调用方没给这个参数） | backlog |
| R17-002 | E | save_to 的 summary 把保存回执句长度当 `NB text`；`extracted_type=text` | backlog |
| R17-003 | E | `save_to` 两套路径语义：read 拒覆盖/拒缺父目录，截图·PDF 覆盖/建父目录 | backlog |
| R17-004 | D | 相对 `../` 遍历允许；只拦系统目录（设计：调用方自己的路径） | 记录 |
| R17-005 | D/E | save_to 永不吃阅读缓存（原始字节 ≠ 抽取正文），回执未点明 | backlog |
| R17-006 | A | 上传回执读了 `#status`（靶站真实 id 是 `#receipt`） | 用法 |
| R17-007 | 量具 | PNG 头断言双层转义误报 False（文件本身正确） | 脚本已修 |

## 评分

P01 5 · P02 4.5 · P03 4.5 · P04 5 · P05 4.5 · P06 4.5 · P07 4.5 · P08 4.5 · P09 5 · P10 4。
