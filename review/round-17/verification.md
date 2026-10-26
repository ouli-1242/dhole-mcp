# round-17 P5 验证（保存与下载轮 · 结转 R16-001 已修，0 新 CONFIRM）

> 分支 review/round-17 · 基线 b60a90a · 端口 9217/8399/8398。
> **并发前提**：另一位 agent 仍在翻 src 注释；本轮只改 `fetcher.py`（单文件）并单补丁暂存。

## 1. 修复与真重启

- R16-001 修复（fetcher.py 三层）后，14:15 重启 9217（pid 7240→**15880**），真机确认
  GBK/Latin-1 read 与 crawl 全部正确。
- P5 再重启一次：pid 15880→**28600**；靶站 8399（22360）与多语言站 8398（26712）不动。
- 重启后 tools/list=13。

## 2. 复验 V1-V5（scripts/verify_r17.py · sessions/verify-r17.log）

| # | 主题 | 读数 | 判 |
|---|---|---|---|
| V1 | **R16-001** | GBK read=「国标编码页面…」；Latin-1=「français … é è ê à ç ù」无 U+FFFD；crawl GBK 页同样正确 | PASS |
| V2 | save_to 合同 | 落盘 sha256 与回执一致；同路径二次「already exists」；`max_save_bytes=50` 具名拒且未落盘 | PASS |
| V3 | 截图/PDF | shot.png 头 `89 50 4E 47 0D 0A 1A 0A`；verify17.pdf 头 `%PDF-`；doc_parse 读回 553 字 | PASS |
| V4 | r15/r14/r13 回归 | 空框 property 读可读说明；css 收窄 ok；selector 0 匹配注记 live+hit；feed -3 拒；相对 since 绝对 ISO；offset 窗口；check baseline→changed | PASS |
| V5 | 上传中文名 | `received verify17-上传.txt (12 bytes)` | PASS |

## 3. 全量 pytest

- `python -m pytest -q` → **7849 passed, 23 skipped, 3 warnings in 144.60s**（pytest-full.log）
- 对账：r16 收官 7845 + 本轮接线 4 枚（R16-001）= 7849，无失败/无 error。

## 4. ruff

- **Found 16 errors**，与 r10-r16 基线一致。

## 5. 并发纪律

- 只改 `fetcher.py`（另一位 agent 自 11:50 未再动）；其余 src/tests 未触碰。
- P6 用 **HEAD 基线生成单文件补丁** 再 `git apply --cached`：staged 版本只含本轮修复，
  对方在 fetcher.py 里的注释翻译留在工作树，不提交、不回滚。
- review/backlog.md 仍含对方在途状态更新：本轮 E 记录写进 common-issues.md。
