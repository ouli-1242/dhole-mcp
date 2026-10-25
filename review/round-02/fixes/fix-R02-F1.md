# fix-R02-F1 · R02-001 watch 私网错误行不指路（CLI 进程够不着 MCP 内存豁免）

- **现象**：`dhole watch add http://127.0.0.1:8399/changing` 被收下，`dhole watch run` 永远 exit 2，错误行只复述安全门原话（`invalid_request: URL targets internal/private IP`）。P01 在 MCP 会话里明明设过 `cred_defaults(set allow_private=True)`——但 watch 是**另一个进程**，豁免够不着它（P01 判语：「这不怪我，可也没人告诉我路在哪」）。
- **根因**：`watch run` 在自己的 CLI 进程里建 MasterFetchServer；MCP 服务的 cred_defaults 默认 memory 模式（凭据不落盘）天然不可达。这是**结构性永久拒**——错误行却当作一次性失败复述。add 时收下私网 URL 是设计（watch.py `_clean_url` 注释在册），所以断点在错误行的呈现层。
- **修法（呈现层）**：ops.py 新增 `_watch_row_error_hint` / `_watch_error_with_hint`——私网族错误行追加 watch 语境的结构事实与真出路：`set DHOLE_PERSIST_CREDENTIALS=1 and store allow_private once (cred_defaults), or watch a publicly reachable URL`。其余错误原样（不加话）。
- **测试**：`tests/test_a_watch_row_says_why_the_private_gate_is_permanent_there.py`——私网前缀行带 hint、非私网行不加话、hint 提到持久化开关。
- **线上证据**：sessions/verify/w5-watch.txt——真实 `dhole watch add + run` 的错误行带着完整 hint；复验后 watchlist 已清。
- **不修的部分**：add 时拒绝私网 URL（审查 REJECT：run 错误行呈现是设计；加第二道规则反而拦掉「先 add 后持久化」的合法路径）。
