# P07 顾之遥 · 博物馆策展人 L2 —— discover/crawl · pin 会话 · recall

## 读数（6 发主跑 + p11 补条 3 发）

| 发 | 工具·形状 | 读数 |
|---|---|---|
| S1 | discover `max_depth=2` | **invalid_request**：supported 名单含 `max_pages` 等（我用了旧参数名，A 类） |
| S2 | crawl / max_pages=3 | 3 页：1 content_ok（article），2 fallback；stopped: max_pages |
| S3 | act pin+session：prompt 正形→click→read | `#status=NAMED:顾之遥`；pin 待命句在场 |
| S4 | 同 session+pin 回读 | **pin hit=true**，`#status` 仍是 NAMED:顾之遥 |
| S5 | recall「对话框 删除」 | 1 行：一篇《注意力机制研究综述》，snippet 里命中「删除线」 |
| p11-S1 | discover /collections?items=6 max_pages=8 | mapped 8 行（6 个 /work + 种子 + about 行） |
| p11-S2 | 同种子 path_include=`/work` | mapped 7 行 = 6 /work + **种子 /collections 本身**；summary 未解释根行为何保留 |

## 合 · 不合

- **合**：pin 合同再次实锤——改的值跨调用还在、`pin hit` 句在场；crawl 行账（article/fallback）
  分得清；discover 正形后地图行都能点。
- **不合**：
  1. **recall 相关性**：问「对话框 删除」返回一篇深度学习综述，只因页里出现「删除线」。
     词形命中不是错，但 top-1 完全无关，且没有分数让用户判断（E6 现场）。
  2. **path_include 保留种子行**：`/work` 过滤后仍带 `/collections?items=6`。若「根节点
     永远保留」是设计，summary 应说一句；否则就是过滤泄漏。P3 查文档/测试定性。
  3. discover 的参数从 `depth` 换成 `max_pages`（r13 的 `depth` 已不可用）；旧卡片/手册若
     仍写 depth 就是文档漂移。P3 查。

## 摩擦清单

1. recall top-1 词形巧合、无分数（E6）。
2. path_include 保留种子行且不解释（候选 C4，P3 定性）。
3. discover 参数更名（depth→max_pages）无迁移提示（A 类错路 + 文档漂移候选）。

## 判定

- pin 命中 PASS；crawl PASS；discover 正形 PASS（p11）。
- C4（path_include 种子行）与 discover 文档漂移转 P3。

## 主观评分

4/5 —— 会话留住与地图都成了；recall 和过滤语义各留一个问号。
