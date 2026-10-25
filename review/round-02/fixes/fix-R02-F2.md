# fix-R02-F2 · R02-002 archive 档把 Wayback capture 索引页当内容盖章 content_ok=true

- **现象**：P02（法务，GDPR 检索）的 EUR-Lex PDF 调用——availability API 报有快照，抓回来的是 archive.org 自己的列表页（"Wayback Machine / 978 captures / 11 Mar 2017 - 17 Oct 2026"，482B），一路盖着 `content_ok=true` 出门。调用方引用回来的是**目录**，不是正文。判定强度：骂人级第一名。
- **根因**：`_fetch_from_archive` 接受任何 status<400 且非空的快照体——快照不存在/被重定向时，archive 送回的就是它自己的 capture 索引页。工具有「有快照→交快照」的合同，缺「这份快照是正文吗」的检查。
- **修法（形状守卫）**：server.py 新增 `_wayback_capture_index_body(body)`——形状三件套同时在场才算索引页：自家标题（"wayback machine"）+ 复数 captures 词 + 指向列表的链接（"every capture" 或 "/web/"）。命中则记 log、note 点名（「listed snapshots of this URL but the snapshot itself answered with the Wayback capture index, not archived content - the origin's failure above stands; try a dated web.archive.org/web/<timestamp>/<url> URL directly」）并 return None（archive 档交白卷，错误行保持来源层原判）。正文里碰巧提到 Wayback 的真快照不受影响。
- **测试**：`tests/test_an_archive_snapshot_that_is_really_the_capture_index_is_not_content.py`——索引页形状被拒、真快照正文（提到 Wayback 字样）放行、三件套缺一不触发。
- **线上证据**：sessions/verify/w1-archive.json——EUR-Lex 重放：archive 档被抓到索引页，note 全文在场，`content_ok: False`（修复前是 True + 目录正文）。
