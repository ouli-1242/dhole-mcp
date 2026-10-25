# round-11 复验记录（P5）· 2026-10-26

- 被验代码：branch `review/round-11` 工作树（P4 修复未提交，提交在 P6 落地）。
- **真重启**：taskkill 旧 9211（pid 34392）→ 全新进程 pid **18048**（dhole-9211.log 启动横幅）→ V1/V2 重放命中该进程；靶站 8399 持续。
- 全量 pytest：**7771 passed, 23 skipped ×2 连续**（7768 + 本轮 3 = 7771 对账平）。诚实记录：修复后首轮全量 2 failed（DNS-recheck xdist flake——round-06/07/08 在册那对），**隔离 2 passed + 两连全量绿**，flake 环境敏感来去如前。
- ruff：**16 条基线**（0 新增）。
- 复验方式：唯一 B 类修复一发真重启后的线上重放（sessions/verify-r11.log）。

## 逐项复验

| # | 项 | 证据 | 判定 |
|---|---|---|---|
| V1 | R11-001 feed max_items=-3 → 具名拒绝 | `invalid_request: max_items must be ≥ 0 (0 = all), got -3`（"no request went out"——参数门先拒） | **FIXED**（verify-r11.log V1） |
| V2 | 0 = all 在册语义保持 | max_items=0 → 8 items 全量返回 | **FIXED**（verify-r11.log V2） |

## 本轮「冷面深水」总账

四颗最少被打的工具深打 63 发：**1 处真缺口修复（feed max_items 负数静默 → 拒绝，R09-002 同族合同）+ 4 起假候选被回执/probe/正文推翻**（POST 修法句住在 next_action、since 教日期格式、sys_guide 内容教学、doc_parse 两码各司其职）。冷面四颗的账目形状（feed 逐行分账/discover 地图/request 写闸 confirm 形状/doc_parse 具名码）全部成立；第一拒全集抽样 12 发：11 颗带码前缀+出路，sys_guide 走内容教学（设计）。
