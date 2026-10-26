# P01 苏晓 · 新手产品经理（L1）—— 照教程抄错

session `df4b95bb03924a5e8b020bef5df89d83`

| 场景 | 误用 | 回执 | 判定 |
|---|---|---|---|
| S1 数字带引号 | `max_content_chars="500"` | 接受（数字字符串本就按 int 读，params 模块顶注写明），387B | A/设计 |
| S2 自愈整形 | 同上 int 500 | 缓存回放 387B | PASS |
| S3 camelCase | `maxContentChars=300` | `invalid_request` 点名未知字段 + 全量支持列表 | PASS |
| S4 参数名记成 q | `web_search {"q": ...}` | 点名 `q` 不支持 + `next_action` 教「改名或删掉」 | PASS |
| S5 自愈 query | `query="机械键盘 评测"` | 2 结果（2 high） | PASS |
| S6 actions 写成字符串 | `"click #note"` | 点名 `actions must be an array of objects, got ...` | PASS |
| S7 数组形状自愈（靶页记错） | `/dialog` 上点 `#note` | selector miss 具名 + 10s 超时；实际 `#note` 在 `/frame/inner` | 量具（R18-007） |

补条（session `68a05279753d4c1e87de86ee088bebf7`）：

- `max_content_chars="abc"` → 点名必须是 integer，未吞；
- `"50"` → 读成 50 后 **clamp 50→500 并在 summary 报出**；
- `/frame/inner` 上 `click #note` → 成功。

**结论**：一个 L1 画像最常犯的六种错全部具名拒绝或按文档宽松接受；无缺陷。
数字字符串与浮点的不同待遇见 R18-006（A）。
