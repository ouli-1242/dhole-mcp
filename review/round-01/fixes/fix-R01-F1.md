# fix-R01-F1 · R01-008 feed_read 缺省预算 1 秒

- **现象**：不带 timeout 的 `feed_read` 首发只等 1 秒、重试 2 秒（P10 Helen 的 hnrss 两发全挂，manual 轮同源曾「2/2 成」）。回执自白：`the first try timed out at 1s, so this call retried once at 2s`。
- **根因**：wire 分发层 `timeout=args.get("timeout", 20)`（server.py:12978）在缺参时塞 **20**，而方法层把 wire 数字当**毫秒**换算（`_feed_timeout_s(20)` → `max(1, round(20/1000))` = 1 秒）。那个 20 是换算层出生前 wire 的旧默认「20 **秒**」；`_FEED_TIMEOUT_DEFAULT_MS = 20_000` 落地时这一处没跟上。
- **修法（根因）**：分发层缺参不塞数——`timeout=args.get("timeout")`，None 让方法层自己的 20s 默认生效。单点改动；显式毫秒（60000→60s）照旧。
- **测试**：`tests/test_a_feed_call_without_a_timeout_gets_its_whole_twenty_seconds.py`——spy `fetch_feeds` 收到的秒数：缺参=20、显式 60000=60。修前红（spy 实录 `timeout: 1`），修后绿。
- **影响面**：仅 feed_read 分发一处；web_read/crawl 等其他 timeout 参数未被触碰。
- **副作用**：不传 timeout 的 feed_read 从「必然 1s+2s」回到「20s+40s」——慢源（hnrss 这类 2–5s TTFB）从系统性死亡回到可用；显著慢的调用 wall time 变长是真预算而不是泄漏。
