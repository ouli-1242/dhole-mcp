# fix-R02-F3 · R02-003 401/403 杠杆空（recover_with 与 next_action 打架）+ 工具描述超承诺

- **现象**：WSJ 401 的回执里，`diagnosis.recover_with=''`（机器可读杠杆白卷）而同封回执的 next_action 教着 `cred_defaults`——两只手打架。另：工具描述写「on a hard block, content_ok can be true of a dated Wayback snapshot」，401 实际**不触发** archive 第三档——P02 的「说好的硬墙走 archive 呢」。
- **根因**：两处。
  1. quality.py `status_verdict` 对 401/403 没有杠杆行——而同一张表里 `auth_required` 那行明明有。修的是**一致性**，不是改归档策略。
  2. 「401 不走 archive」是**设计**（quality.py:1297 docstring 在册：archive won't have login-gated content；`_ARCHIVE_FALLBACK_STATUSES=(404,410,451)` 同一决策）——错的是工具描述的「hard block」超承诺，没点名状态族。
- **修法**：
  - quality.py：401 → `store credentials for this host with cred_defaults (set={auth: {type:'basic',username,password} or {type:'bearer',token}}), or switch source / the Internet Archive copy`；403 → `retry with force_fetcher='stealthy', store credentials with cred_defaults, or switch source / the Internet Archive copy`。措辞与 `auth_required` 行及 next_action 同源。
  - server.py web_read 描述：`on a gone/removed page (HTTP 404/410/451 — not on 401/403, which are walls: archive won't hold login-gated content either)`——承诺与策略一致。
- **测试**：`tests/test_an_http_401_names_the_same_lever_both_hands_agree_on.py`——401 的 recover_with 点名 cred_defaults 且与 next_action 同源；403 给三路杠杆；描述含 404/410/451 与 401/403 排除句。
- **线上证据**：sessions/verify/w2-401.json——WSJ 重放：`recover_with: "store credentials for this host with cred_defaults (set={auth: {type:'basic'..."`。
- **wire 价格**：描述变了——`dhole wire lock --write` 重锁（36,763），README/configuration.md/三处测试常量同步（test_wire_moves_land_in_the_manual CONNECT_CEILING、test_server_3、test_fetcher_1）。
- **不修的部分（REJECT 在册）**：401 触发 archive 档——archive 不会有登录墙内容，去了也是白付一跳。
