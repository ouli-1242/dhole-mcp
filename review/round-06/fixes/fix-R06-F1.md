# fix-R06-F1 · R06-001 css_selector 0 匹配说出去（不再静默回全页）

- **现象**：`.no-such-class-xyz` → 整页 269 字符、content_ok=true 出门，summary 无一字提选择器没匹配——缩取参数被静默丢弃且**看起来像成功**。P08 的伪类第一拒是最锋利的旁证：同一引擎在伪类处自己说了 "it would have been ignored and the whole page returned"，0 匹配分支不说。
- **根因**：`_narrow_html_to_selector`（trafilatura_extractor.py）`if not matches: return None`——0 匹配与编译失败一样静默回落整页。回落是**设计**（缩取是可选收窄，从不掏空答案；docstring 自己写明就是为修静默 no-op 而生），缺的是把后果说出去。
- **修法（话术层）**：新增 `_note_selector_matched_nothing(selector)`——与 `_note_selector_not_applied` 同一句式：`css_selector '<sel>' was NOT applied: it matched 0 elements on this document - the whole document was returned (narrowing never empties the answer)`。0 匹配分支调用；一次调用只说一遍（同款去重）。行为零变化。
- **测试（先红后绿）**：`tests/test_a_selector_that_matches_nothing_says_so.py`——0 匹配 note 在场（含 matched 0 elements / whole document was returned）；命中的选择器不加 note。修前红，修后绿。
- **线上复验**：真重启后重放（sessions/verify-r6.log W1）：note 全文在 summary。
- **影响面**：summary 文本；wire 价格不受影响（tools/list 未动，全量测试绿）。
