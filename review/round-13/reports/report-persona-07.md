# P07 陆沉舟 · 隧道测量员 L2 —— 链条⑦search→crawl→discover 对账→read（+research 补条）

## 画像与卡面

隧道测量员，L2。情境：要摸清一个资料站的结构，用「搜索、爬、地图」三把不同的尺量同一片地，
再落到一页原文。目标：三把尺的读数能对上。验收：两把尺对账；中环 clamp 落 summary/clamped。

## 读数（主跑 7 发 + p19 补条 2 发）

| 发 | 工具 | 读数 |
|---|---|---|
| S1 | web_search playwright automation | 3 results（3 high），baidu/sogou/yandex，rerank=neural |
| S2 | web_crawl / depth≤2 | 2 页：1 content_ok，article 1 / fallback 1，`stopped: max_pages reached` |
| S3 | web_crawl max_pages=0 | `max_pages clamped 0->1 (supported range 1-40)`——落 summary |
| S4 | web_search max_results=0 | `max_results=0 is outside the supported 1-50 range; returning at most 1`——落 summary |
| S5 | web_discover / | mapped 5 URL；页面行 work=3，与 items=3 对齐 |
| S6 | web_read /work/1 | 200 · 39B · cache（37.3 min 前抓，replay 句在场） |
| p19-S1 | web_research「Model Context Protocol specification」 | 2/2 源读入，failed=0，more_hits=5；证据包逐源带 URL/标题/chars |

## 合 · 不合

- **合**：crawl 与 discover 两把尺对得上（5 行，work=3=items）；两个 clamp 都具名落 summary，
  没有静默改参；research 的失败源独立成 `failed`（本轮 0 失败），more_hits 与已读分账。
- **不合**：无实质缺陷。S6 命中缓存，回执自觉说「replayed from the cache」，不让用户误以为是新抓。

## 摩擦清单

1. 两个 clamp 都只在 summary 尾巴一句，参数错误没有独立字段（既有合同，r12 已判 REJECT）；
   对 L2 可读，但机器消费要抠 summary 文本。
2. research 只读 2 源（max_sources=2），more_hits=5——用户想全读得再来一轮。

## 判定

- 三把尺对账 PASS；中点 clamp 具名 PASS（r12 必测 3 的 2 发抽检全过）。
- research 覆盖补条 p19：证据包 2/2 + failed 独立数组 PASS。

## 主观评分

5/5 —— 数字能对上，缓存也主动交代，是这轮最可复算的一页。
