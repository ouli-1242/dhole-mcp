# P04 老纪 · 运维工程师（L2）—— URL 形状与动词

session `eab3e3ef70e04ec1bd56ba602e406b8a` / `31781cc7f2764a5b87aeb80147106353` / `881acbb5d48e40e8a6c7ef61e40af68e`

| 场景 | 误用 | 回执 | 判定 |
|---|---|---|---|
| S1-S3 GET 探 ftp/file | `web_request GET` | 先被 `method=GET is a read` 拒；next_action 指 web_read（GET 归读，设计） | 量具（R18-007 邻） |
| S4 javascript: | web_read | `URL scheme 'javascript' is not allowed` | PASS |
| S5 method 拼错 | GETT | `unsupported method 'GETT'` + next 列出 DELETE/GET/HEAD/PATCH/POST/PUT | PASS |
| S6 method 小写 | get | 归一为 GET 后按读拒绝 | PASS |
| S7 空格+中文 URL | `q=机械 键盘` | 200，387B | PASS |
| S8 data: | web_read | `URL scheme 'data' is not allowed` | PASS |
| 补条 POST 无协议/ftp/file | p04b | 分别点名 no scheme / ftp / file | PASS |
| 补条 POST 未确认 | /checkout | 写入闸要求 confirm=true，next_action 教补同一发 | PASS |
| 补条 POST confirm=true | /checkout | 200，31B「订单已提交」 | PASS |

**结论**：URL 形状与动词拼写逐颗具名；写闸先拒后放，回执教自愈。首版用 GET 探 HTTP URL 属画像本身的误用（web_request 只收写动词），补条以 POST 重测通过。
