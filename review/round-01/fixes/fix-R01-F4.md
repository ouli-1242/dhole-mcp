# fix-R01-F4 · R01-006 挑战页可发现性两处（fill 花括号提示 + confirm 层次）

- **现象**：P07 在靶站挑战页 5 连试失败——①把引用语法猜成 `{CODE}`，被**静默按字面**填进表单（evals/.runtime/challenge.log：answer=`{CODE}`，expected=`TTUUR`，ok=false），回执只说 fill ok；②顶层 `confirm: true` 不动提交闸（正形是动作内 `{"click": {..., "confirm": true}}`），pending_approval 报错里的 `{"confirm": true}` 不说层次。对抗审查补测用正形一发通关（「同一条链里读到的就是填进去的那一枚」）——**能力缺口不存在**，缺的是把形状教出去的两句话。
- **根因**：①「plain 花括号是文本」是设计（测试钉住），但恰在这个形状上没有任何提示；②`_CONFIRM_SNIPPET = '{"confirm": true}'` 天然歧义。
- **修法**：
  - actions.py 新增 `_hint_unresolved_braces`：fill 成功后，text 含 `{word}` 形状且该名字不在链的寄存器里 → 回执行带 `note`（教 `{=name}` 用法 + 点名现存寄存器）；合法 `{=name}` 与真字面量不付这句话。
  - `_CONFIRM_SNIPPET` 改为 `'confirm: true on the click/submit action itself ({"click": {"selector": "...", "confirm": true}}), not at the top level'`——两处提交闸报错共用。
- **测试**：`tests/test_a_fill_that_looks_like_a_reference_says_it_went_in_literally.py`（假页面 harness，与 test_actions_2 同款）：猜法有 note 且点名寄存器 / 合法引用与纯文本不付 note / snippet 含 confirm+click+action。
- **影响面**：actions.py 一帮助函数 + 一处调用 + 一常量。
- **副作用**：text 里出现 `{word}` 字面量且恰无同名寄存器的合法用例（如模板文本）会多一句 note——软提示，不改行为，可接受。
