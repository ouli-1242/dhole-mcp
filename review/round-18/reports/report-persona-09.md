# P09 罗薇 · 图书管理员（L2）—— 本地文件与订阅源

session `8b3aaba82287415994fa6dcf732f942c`

| 场景 | 误用 | 回执 | 判定 |
|---|---|---|---|
| S1 不存在的文件 | `nope.md` | `File not found: <server cwd>\...`（绝对路径前缀被脱敏）+ cwd 说明 | PASS |
| S2 目录 | sessions/files | `Path is a directory, not a file` + 列出该层文件名 | PASS |
| S3 传 URL | `http://127.0.0.1:8399/article` | 当相对路径处理 → `File not found: <server cwd>\http:\...`；next 只说补绝对路径，未指 web_read | E（R18-004） |
| S4 二进制后缀 | not-a-doc.png | `Unsupported file type '.png'` + 支持列表 | PASS |
| S5 feed 非 feed 页 | /article | 顶层 summary 说 1 行 errored，行内自带 error/diagnosis | PASS |
| S6 urls 是字符串 | `urls: "..."` | `urls must be an array of strings, got a single string` | PASS |
| S7 recall 空白查询 | `"   "` | 按缺参点名 `query` | PASS |

**结论**：文件/订阅源形状逐颗具名；唯一缺口是把 URL 当路径时没把人领向 web_read（R18-004，E）。
