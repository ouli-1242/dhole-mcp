"""fix-R13-F2 · R13-002 缓存回放补回 css_selector 0 匹配注记。

- **现象**（round-13 p22 #02/#03）：同一 URL + 同一 `css_selector="#no-such"`
  读两遍。第一遍 live 的 summary 带 R06-001 的诚实句「matched 0 elements … the
  whole document was returned」；第二遍命中缓存（TTL 内）**这句话消失**，整页照回。
  p20 S1 / p21 A1 在旧缓存行上复现。
- **根因**：注记由 `trafilatura_extractor._note_selector_matched_nothing` 写进
  `_ARG_NOTES`，只在**抽取发生时**生成。缓存 envelope（server.py `_finalize_result`）
  存了质量判决与元数据，却没存参数注记；回放侧
  `quality._with_agent_hints` 组装 summary 时自然没有这句话。R01-021 修过同族漏项
  （headers_off_by），这一颗又漏了一次。
- **修法**：写入侧把**与缓存键同源**的 selector 注记存进 envelope（`arg_notes`，
  由 `_selector_notes_for_cache` 过滤 `css_selector ` 前缀）；回放侧逐条
  `_note_text()` 还原。`focus` / `max_content_chars` 不在缓存键里，它们的注记不搬，
  避免旧行把旧参数的话贴到新调用上。旧缓存行没有这一颗 → 一个字不说（缺席不是断言）。
- **测试（先红后绿）**：`tests/test_a_cache_replay_still_says_the_selector_matched_nothing.py`
  2 测试（live→replay 两发都带句；过滤只留同源注记）。修前 2 红，修后 2 绿。
- **影响面**：缓存 envelope 多一颗可选键；缓存键与 TTL 零变化；无 selector 的缓存行
  零变化（`arg_notes` 为空列表 / 旧行为同一句话）。
"""
