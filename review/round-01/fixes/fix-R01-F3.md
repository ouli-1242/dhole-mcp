# fix-R01-F3 · R01-009 skill 文案计数漂移（eleven/ten → 13）

- **现象**：start 路标页开头「dhole is eleven tools」、SKILL.md「ten everyday tools … All eleven are registered」——tools/list 实际 **13** 颗（web_research/web_recall 上线时没跟）。P03 的 L1 反应：「说好的 11 颗是几个意思」。
- **根因**：skill 首页文案写死了计数与清单；深卷（tools.md / configuration.md）早就写了 13，漂移的恰是每个读者都读的两处。
- **修法**：
  - start.md：计数句「is 13 tools」；Route 2 补 `web_research` / `web_recall` 各一句；补 `sys_state` / `sys_guide`（此前 13 颗里这两颗在路标页无名）。
  - SKILL.md：frontmatter description 的工具列表补 web_research/web_recall；正文「twelve everyday tools」清单补两颗 +「All 13 are registered by default」；Choosing a tool 表补两行（evidence pack / recall）。
- **测试**：`tests/test_the_skill_corpus_counts_the_tools_that_are_registered.py`——用 `MasterFetchServer._TOOL_DEFS` 反查：两份首页必须点名每一颗注册工具、不许再出现 "eleven tools"/"ten everyday tools"、写死的计数句必须与注册表 count 相等。文案再漂一格即红。
- **影响面**：两份 markdown；不动 references/ 深卷（本来就对）。
- **副作用**：无。
