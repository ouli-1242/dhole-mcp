# fix-R04-F2 · R04-002 watch 私网出路句补「flag 得在 watch 进程的环境里」半个前题

- **现象**：R02-001 的出路（`set DHOLE_PERSIST_CREDENTIALS=1 and store allow_private once`）方向经 P03 三轮攻击**验证真通**（带 flag 的 watch 进程 baseline 200 exit 0），但话术缺半个前题：`dhole watch run` 是独立进程，flag 只设在 MCP 侧（或随便哪个 shell）时，watch 进程读不到，依旧 exit 2——读者照原句设了 flag 仍会撞墙一次。
- **根因**：R02-001 写句时只回答了「为什么 watch 够不着内存豁免」，没回答「flag 要跟着哪个进程走」。
- **修法（话术）**：ops.py `_WATCH_PRIVATE_HINT` 改为 `set DHOLE_PERSIST_CREDENTIALS=1 in the watch process's own environment too (export it where \`dhole watch run\` runs - the MCP side having it is not enough), store allow_private once (cred_defaults), or watch a publicly reachable URL`。
- **测试（先红后绿）**：`tests/test_the_watch_road_names_whose_environment_needs_the_flag.py`——断言 hint 点名 watch 进程自己的 environment/env。修前红，修后绿（旧句测试 test_a_watch_row_says_why_the_private_gate_is_permanent_there 的前缀断言同绿）。
- **线上复验**：真重启后 `dhole watch add+run`（无 flag）→ 错误行带新前题全文（sessions/verify-r4.log W2）；带 flag 的通路 P03 已验（baseline 200 exit 0）。
- **影响面**：watch run 的 CLI 错误行文本；wire 不受影响。
