# P05 沈青禾 · 数据记者 L3（猎手）—— 证据包 · recall · check 变化

## 读数（6 发：cred + research + search + recall + check×2）

| 发 | 工具·形状 | 读数 |
|---|---|---|
| S1 | research 2 源 | `1 of 2 source(s) read`；failed 独立数组：`http_error_402 … why`；more_hits=5 |
| S2 | search「data update frequency」 | 2 results（2 high），so360/sogou/baidu/yandex，neural |
| S3 | recall「缓存 过期 抓取」 | 4 行；前三条同文不同 URL 变体 |
| S4 | check 首问 /changing?cb=p05-check | `0 changed, 0 unchanged, 1 baseline recorded` |
| S5 | check 再问 | `1 changed`；行内 `first_divergence_at=111, baseline_chars=184, fresh_chars=203` + diff note |

## 合 · 不合

- **合**：research 的失败源不是拖垮整包，而是独立 `failed` 数组带 `why`（402 如实）；
  `more_hits=5` 与已读源分账。check 两相在**真的会变**的页上给出 changed + 分叉位置 +
  前后字数，可直接写进稿子。
- **不合**：recall 的 URL 变体重复（E2 第三次现场）；另外 query 的 CJK 二元组会命中
  「删除线」这类词——回执给 snippet，用户能看出是词形巧合，不算错，但低相关行没有分数。

## 摩擦清单

1. recall 同文三变体（E2，皱眉）。
2. recall 无相关度分数（E6，无感/皱眉之间）。
3. research 只有一个可用源，稿子要再补一条来源（正常现象，不是缺陷）。

## 判定

- research 失败分账 PASS；check changed/unchanged PASS；recall 变体重复记 E2 复发。
- 本轮未发现新 B 类。

## 主观评分

4.5/5 —— 证据包和变化检查都能直接引用；recall 的行去重还要用户自己合并。
