# round-18 P5 复验

## 真重启

- 9218 旧进程停止 → 新进程 **PID 34796**（`dhole --http --port 9218`，env
  `DHOLE_IGNORE_ROBOTS=1` / `DHOLE_USAGE_LOG=1`）；重启事件写进
  `review/.runtime/server-restarts.log`。
- 8399 靶站与 8398 多语言站全程在列；`cred_defaults allow_private=true` 在新进程重置。

## V1-V6（`scripts/verify_r18.py`，日志 `sessions/verify-r18.log`）

| 组 | 内容 | 结果 |
|---|---|---|
| V1 R18-001 | `press.key` 传 list / int / dict → `action 0 'press.key' must be a key string (e.g. "Enter"), got <type>`，不再出现 selector miss；合法 `key="Enter"` 仍成功 | **PASS** |
| V2 R16-001 | meta-only GBK →「国标编码页面…中文与数字 2026」；Latin-1 →「français … é è ê à ç ù」；crawl GBK 不占位、不乱码 | **PASS** |
| V3 R15-001 | 空框 `property=value` → ok=False +「'value' is empty on this field - that is its current value, not a missing read」 | **PASS** |
| V4 save_to | 落盘 sha256 一致（1,265B）；同路径二次保存被拒；`max_save_bytes=8` 拒绝且不落半截 | **PASS** |
| V5 截图·PDF | 整页 PNG（65,143B，头 `89 50 4E 47`）；PDF（110,377B，头 `%PDF`）；PDF→doc_parse 553 字 | **PASS** |
| V6 r13/r14 | feed `max_items=-3` 拒绝；selector 0 匹配注记在 summary；缓存回放 `fetched_at` 同首读 | **PASS** |

**VERDICT: ALL_PASS**

## 全量 pytest

`python -m pytest -q` → **7854 passed, 2 failed, 23 skipped**（130.74s；
对比 round-17 的 7849 passed，新增 7 枚即本轮测试，全部通过）。

两个失败均**非本轮改动**：

1. `tests/test_server_8.py::test_the_cause_sentence_still_tells_which_action_needs_the_resources`
   —— 该测试断言 `actions_want_visual` 的 docstring 含英文句 `product IS the rendered page`；
   另一位 agent 正在把该 docstring 翻成中文（工作树 diff 显示该英文句被删），断言随之落空。
   我们只改了 `_validate_actions` 的 press 分支，未动该 docstring。
2. `tests/test_a_map_the_budget_cut_can_be_read_back_row_by_row.py::...::test_the_advice_names_the_door_that_works`
   —— 单跑通过；全量在 xdist 下失败，属 **round-08 起在册的 xdist 抖动**，与本轮改动无关。

## ruff

`ruff check src tests` → **16 errors**（与本轮前基线一致；无新增）。

## 并发纪律

- `src/dhole_mcp/actions.py` 工作树含对方 558+/328- 注释翻译（未提交）；
  本轮只暂存 HEAD 基线 + 我们的 6 行 press 校验，不夹带、不回滚对方改动。
- 新增测试文件 `tests/test_a_press_key_that_is_not_a_string_is_refused_by_name.py`
  为本轮新建，整文件暂存。
