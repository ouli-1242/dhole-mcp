# round-10 复验记录（P5）· 2026-10-26

- 被验代码：branch `review/round-10` 工作树（零修复——无新提交内含变更）。
- **真重启**：taskkill 旧 9210（pid 21872）→ 全新进程 pid **14648**（dhole-9210.log 启动横幅）→ tools/list 13 颗 PASS（sessions/verify-r10.log）。
- 全量 pytest：**7768 passed, 23 skipped**（与 round-09 收官点对账平——零改动零新测试）。
- ruff：**16 条基线**（0 新增）。
- 复验方式：零修复轮（round-05 先例）——真重启 13 tools + 全量绿即为复验本体；三条线索的否定性证据（两道在册闸/字段合同/probe 实证）见 review.md。

## 本轮「预算与时间」总账

时间语义逐工具对账 57 发：**0 CONFIRM**——显式 timeout= 合同、learned 让位、五处具名 clamp、TTL 四态代际、replay 诚实形状、crawl stopped 三现场、批量逐页账全部成立。三条候选线索（crawl 代际、attempt_cap 差值、recall 0 行）全部被在册合同与 probe 实证推翻——预算与时间的合同面经过两轮深水后仍然咬合。
