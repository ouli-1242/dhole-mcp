# P06 小满 · 内容编辑（L1）—— emoji / 零宽 / 换行 / 尾随空格

session `de56fe46dc414ed885c9bcbe35a2955b`

| 场景 | 输入 | 回执 | 判定 |
|---|---|---|---|
| S1 查询带零宽+emoji | `机械键盘\u200b 👩‍💻` | 2 结果，summary 原样保留 | PASS |
| S2 selector 尾随空格 | `click "#note "` | 成功（CSS 忽略尾空白） | PASS |
| S3 selector 含换行 | `click ".card\n"` | selector miss 具名，但句尾带 Playwright 原文 `Locator.wait_for: Timeout 10000ms exceeded` | E（R18-003） |
| S4 URL 带中文+emoji | `?q=机械👩‍💻` | 200，387B | PASS |
| S5 自愈填表读回 | fill `笔记👩‍💻\u200b` → save → read | `SAVED:笔记👩‍💻`（零宽保留） | PASS |
| S6 不存在的 emoji selector | `#不存在👩‍💻` | 整页返回，summary 明说 `matched 0 elements ... the whole document was returned` | PASS（r13 家产） |

**结论**：Unicode 保真与 0 匹配注记都成立；动作失败句混入驱动原文属 E（R18-003），不影响成败判断。
