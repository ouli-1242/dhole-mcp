"""fix-R09-F1 · R09-002 recall limit 越界 clamp-and-name。

- **现象**：P10 实测 limit=21 与 limit=-5 均被 `recall.py` 的
  `max(1, min(20, int(limit or 8)))` 静默改写，回执无任何注记。行为指南的在册
  绝对句「An out-of-range number is either clamped-and-named, or refused — never
  silently rewritten」被自家工具违反；同轮对照：web_search max_results=51 具名
  clamp、web_research max_sources=0 带 `clamped` 字段。
- **修法**：clamp 结果带注记——越界时回执带 `clamped: ["limit -> N (1-20)"]`
  （形状与 web_research 的 clamped 一致）；未越界（含缺省 8）一个字不加。
- **测试（先红后绿）**：`tests/test_a_recall_limit_out_of_range_is_clamped_and_named.py`
  3 测试（21 具名 / -5 具名 / 8 与缺省安静）。修前 2 红，修后绿。
- **线上复验**：真重启（pid 24828）后 V1/V2 重放（sessions/verify-r9.log）。
- **影响面**：web_recall 回执多一个可选键；行为（返回行数）零变化。
"""
