# fix-R01-F5 · R01-010 drag 首拒不教全形 + R01-002/R01-001 两处顺手修 + R01-005-harness

## R01-010 · drag 形状校验先于源端寻址

- **现象**：`{"drag": {"from": "#a", "to": "#b"}}` 第一拒报 `_act_target` 的「needs exactly one of 'selector' or 'index'」（不提 to）；第二拒才见 `must be {selector|index, to}` 全形。P08 三连拒 8s×3。
- **根因**：drag 分支先对源端跑 `_act_target`（它对 `{"from": …}` 报半句话），`to` 的全形检查排在后面。
- **修法**：drag 分支内建 `_drag_shape()` 全形异常；陌生键（selector/index/expect/to 之外的键）先于 `_act_target` 裁决，第一拒即全形 + 点名冒出来的键。
- **测试**：`tests/test_a_drag_rejection_teaches_the_whole_shape_on_the_first_try.py`：`{from,to}` → 全形 + "from" 被点名；缺 to 照旧全形；正形放行。

## R01-002 · crawl search 清空后 summary 不再借 max_pages 的措辞

- **现象**：search 过滤清空队列后 summary =「crawled 0 page(s) … stopped: max_pages reached」——没货读起来像爬满了（P02/P05 两轮白跑）。
- **根因**：search 过滤跑在 summary 组装之后，清空时过滤前的停因位原样出门。
- **修法**：过滤前记 `_pre_filter`；清空时 summary 重写为「search='…' matched 0 of N crawled page(s) - the word only lives in page bodies, and search matches URL path + title」；有命中照旧 filtered to。
- **测试**：`tests/test_a_crawl_search_that_matches_nothing_says_so_not_max_pages.py`（本地三页 fixture）：空查不含 "max_pages reached"、"matched 0 of 3"、命中路照旧 "filtered to"。

## R01-001 · feed 顶层 summary 点名出错行 URL

- **现象**：三发里一发 410，顶层只说「1 rows errored」，哪个 URL 要展开行才知道（P01）。
- **修法**：`_feed_batch_envelope` 的 errored 句带前 2 条出错行的 source_url，「each row names its own error」兜底照旧。
- **测试**：`tests/test_a_feed_summary_names_the_row_that_errored.py`：1 错点名、3 错点名前 2 且兜底句仍在、干净批次不付。

## R01-005-harness · ensure_up.ps1 env 失效（harness 修，不进产品队列）

- **现象**：ensure_up.ps1 拉起的进程里 `DHOLE_IGNORE_ROBOTS=1` 从未生效（P03 sohu / P07 /private/secret 的 robots_disallowed 与缓存自白「bypassed (process-wide)」矛盾）。
- **根因**：env 赋值写在本文件第 12 行的**双引号字符串**里——PowerShell 在外层求值，`$env:VAR` 未设时展开为空串，子进程收到 `='1'; dhole …`（实测复现：`"$env:FOO_TEST='1'; echo hi"` → `='1'; echo hi`）。产品 robots.check 对 env 短路的代码正确（robots.py:497）。
- **修法**：env 在脚本顶层真赋值，`Start-Process` 子进程继承；内串不再赋值。
- **验证**：P5 真重启后 `web_read /private/secret`（allow_private 豁免后）应成功取回而非 robots_disallowed——见 verification.md。
