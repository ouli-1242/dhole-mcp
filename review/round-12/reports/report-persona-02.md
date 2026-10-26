# P02 岑万川3 · 证券交易员 L3（猎手）—— 链条②search→research→read 三方对账

## 读数（5 发）

| 发 | 工具 | 读数 |
|---|---|---|
| S1 | web_search "browser automation agent" ×4 | 4 行真链（tinyfish.ai 等） |
| S2 | web_research max_sources=2, timeout=30000 | sources=1（n=2 博客园行, content_ok）+ **failed=1**（n=1 CSDN, why=`http_error_521: server returned error status`）——"1 of 2 read; 1 failed (each row says why)" 两本账都在 |
| S3 | web_read search 第一行 timeout=0 | 具名拒：`invalid_request: Timeout must be positive`，next_action 教 1-120000 |
| S4 | web_read timeout=999999 | 具名拒：`Timeout exceeds maximum of 120000ms (2 minutes)` |

## 合 · 不合

- **合**：search 行 / research 来源行（sources+failed 两个名单）/ read 三方各记各账，
  URL 不串；r10 timeout 合同回归两发都具名（正域 1-120000 双端教学）。
- research 的失败源行在独立的 `failed` 数组、带 why 字段——summary 说"each row says
  why"，行确实逐行说。首轮只打 sources 差点误判"失败行丢了"，读全回执后推翻。

## 判定

链条②全绿。三方对账成立，失败源不失踪。
