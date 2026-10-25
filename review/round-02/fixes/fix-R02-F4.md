# fix-R02-F4 · R02-004 learned ceiling 压显式预算 + 教学句逐发重复

- **现象**（两半）：①P04 显式 `timeout=90000` 的调用被 learned ceiling 压到 5s/attempt——`_adaptive_timeout` 对见过的域无视 default_ms，显式预算照样被压；而 R02-041 注释自称 "caller's budget is the contract"，与实现不符，用户侧无翻身杠杆。②同一句长解释每个回执都念一遍（P08 同主机 6 连发 6 遍）——披露变噪声。
- **根因**：①`_http_tier_budget` 无从知道 wire 上带没带 `timeout=`——它只看 `budget_ms`（显式 90000 和默认 30000 到它手里都是「一个数」）。②句子按「每回执都值得念」写，且把 override 杠杆写成 "not a cap you set"——在显式预算被压的现场是假话。
- **修法**：
  - context.py 新增 `_TIMEOUT_EXPLICIT` ContextVar；server.py 分发层置位（`bool(args) and "timeout" in args`）；`_http_tier_budget` 在 `not _TIMEOUT_EXPLICIT.get()` 时才应用 learned——**显式 timeout 是合同，learned 让位**；默认预算照旧让 learned 管（retry 经济学不破）。
  - learned 句收紧为一句带真杠杆的教学句：`one HTTP attempt is bound by the learned ceiling for this host: {given}, not the default {asked} - an explicit timeout= lifts it`（显式修复后这句才成真话；{asked} 在这一支恒为默认值，所以说 "the default"）。
- **测试**：`tests/test_an_explicit_timeout_is_not_undercut_by_the_learned_ceiling.py`——带 timeout= 的调用 learned 不生效（attempt 预算保持调用方值）、不带 timeout= 的调用 learned 照旧生效；autouse fixture 复位 ContextVar（防 xdist 串 worker 泄漏）。
- **线上证据**：sessions/verify/w3-learned.txt——三连发全部带新句（`...: 5000ms, not the default 30000ms - an explicit timeout= lifts it`）；probe_w3c 另一观察：冷 EMA 首发无此句（无样本时不念，正确），首发真实抓取后才念。缓存命中的回执没有此句也是正确行为（没走 HTTP 档，无 attempt 可约束）。
