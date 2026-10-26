# round-16 P5 验证（编码与多语言轮 · 1 CONFIRM，修复推迟）

> 分支 review/round-16 · 基线 6f262fa · 端口 9216/8399/8398。
> **并发前提**：另一位 agent 正在把 src 注释翻成中文（fetcher/crawl/trafilatura_extractor
> 11:38-11:50 在途）。本轮**零产品代码改动**；R16-001 的测试草稿在 fixes/ 未接线。

## 1. 真重启

- 2026-10-26T12:03 杀掉 dhole-9216 旧进程 **pid 23108**，`ensure_up.ps1` 起新进程
  **pid 28668**；靶站 8399（pid 22360）与多语言站 8398（pid 26904）不动。
- 重启后 V1 第一发即打在新进程上；tools/list 仍 13。

## 2. 复验 V1-V5（scripts/verify_r16.py · sessions/verify-r16.log）

| # | 主题 | 读数 | 判 |
|---|---|---|---|
| V1 | R15-001 回归：空框 property 读 | 动作行 `ok=False`，error=`read found no property 'value': 'value' is empty on this field - that is its current value, not a missing read` | PASS |
| V2 | UTF-8 四文字 + 中文 URL 缓存键 | 阿拉伯/日文/韩文/西里尔码点全在；`/article?cb=复验中文` 两读 `cached=True` 且 fetched_at 相同 | PASS |
| V3 | R16-001 已知缺陷 | gbk read=`[Binary content detected …]`、content_ok=true；latin1=`fran�ais` 且 U+FFFD、content_ok=true | **KNOWN（未修，按预期复现）** |
| V4 | r13/r14 四件 | css 收窄 ok；selector 0 匹配注记 live+hit；feed -3 具名拒绝；相对 since 绝对 ISO；offset 窗口 `473B of 7.0KB` | PASS |
| V5 | web_act 多语言 | 混合页 `read p` 回「中文段落…العربية…😀」；阿语填表读回「ليلى」 | PASS |

**已知缺陷说明**：V3 不计 PASS/FAIL——它是本轮唯一 CONFIRM，修复路径落在对方正在编辑的三个
文件上，按用户指示推迟。其阴性/阳性由 `fixes/test-R16-F1-draft.py` 的 4 枚测试待接线。

## 3. 全量 pytest

- `python -m pytest -q` → **7845 passed, 23 skipped, 3 warnings in 157.77s**（pytest-full.log）
- 与 r15 相同（本轮无新接线测试；草稿在 review/round-16/fixes/，不参与收集）。
- 归因：当前工作树含对方在途注释翻译；全绿说明其改动未破坏行为。

## 4. ruff

- **Found 16 errors**，与 r10-r15 基线一致。

## 5. 并发纪律

- 未编辑 `fetcher.py` / `crawl.py` / `trafilatura_extractor.py`（对方正在改）；
  `git show HEAD` 确认 R16-001 在 HEAD 就存在，非其 in-flight 引入。
- 未接线测试：避免在对方的分支上留下红测试；草稿与具体修法写入 review.md。
- P6 只暂存 review/ 本轮资产；src/tests 一概不暂存。
