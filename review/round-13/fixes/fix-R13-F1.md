"""fix-R13-F1 · R13-001 显式 css_selector 收窄不再被判 extraction_collapsed。

- **现象**（round-13 p21 A3 / p22 C7）：`web_read(/article, css_selector="h1")`
  取回 10 字（正是用户点名的 h1），整页可见 400 字 → `content_ok=false`，
  `error="extraction_collapsed: … the words ARE in the HTML, dhole's reader lost
  them"`，next_action 教换 extraction_type。用户点名要一块，抽取器一个字没丢。
- **根因**：`quality._detect_content_issue` 的 collapse 判决用「整页可见字」作分母，
  没有 css_selector 豁免；同页的 `_substance_verdict` 已在 R06-002 拿到同一枚豁免
  （quality.py:890-897）。R06-F2 只修了实质尺，判决尺漏了同一条原则
  （narrowed 的调用方要的是更少）。
- **修法**：判决尺在 `_CSS_SELECTOR` 在场时跳过 collapse 分支（留空判，后续
  js_shell/混淆/geo 等判据照常）。恢复尺 `extraction_lost_text`
  （server._translate_response_body 的「值不值得再抽一次」）**不动** —— 它的
  docstring 在册说明了为什么这一问不许被答案形状豁免。
- **测试（先红后绿）**：`tests/test_a_narrowed_fragment_is_not_an_extraction_collapse.py`
  2 测试（selector 在场 → ""；无 selector 的真塌陷照旧 extraction_collapsed）。
  修前 1 红，修后 2 绿；相关族 520 测试绿。
- **影响面**：只有 `_CSS_SELECTOR` 在场的 web_read/相关入口的 collapse 判决；
  无 selector 的调用零变化。恢复/重试路径零变化。
"""
