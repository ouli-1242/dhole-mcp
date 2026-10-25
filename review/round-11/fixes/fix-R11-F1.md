# fix-R11-F1 · R11-001 feed_read max_items 负数拒绝

- **现象**：P10 实测 max_items=-3 静默返回全量 8 条——feed.py 的 `if max_items <= 0: return` 把负数同 0 一起放行，无 clamped、无注记、无拒绝。工具描述只许「default 20; 0 = all」：负数不在声明域。
- **修法**：`_cap_applied` 对 `max_items < 0` 具名拒绝（`invalid_request: max_items must be ≥ 0 (0 = all), got -3`）——负数没有有意义的正域钳制目标（clamp 到几都是猜意图），按行为指南绝对句「clamped-and-named or refused」选**拒绝**。0 的在册语义（= all）与正数路径零变化。
- **测试（先红后绿）**：`tests/test_a_feed_max_items_below_zero_is_refused.py` 3 测试（负数拒 / 0=all 保持 / 正数切片+note）。修前 1 红，修后绿。
- **线上复验**：真重启（pid 18048）后 V1/V2 重放（sessions/verify-r11.log）。
- **影响面**：web_feed 参数门多一颗拒绝；0 与正数行为零变化。
