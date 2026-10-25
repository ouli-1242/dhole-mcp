# 画像报告 · P01 陈曦（数据分析师 · L2）· round-01

> 场景：季度复盘前夜。目标：读本地两张表 + 盯竞品博客增量并交叉看正文。
> transcript：sessions/persona-01/*.json（10 发）· 台账 sessions/calls.jsonl（tag p01-*）

## 用户剧本（封存卡要点）

「帮我把这两个表读一下说重点」→「再把我们盯的那家博客最近 3 天更新拉出来」→
「头一条讲什么的，展开看看正文结论」→「算了把窗口拉到 10 天，但我只看最新 3 条」。

## 场景实录（原话 → 调用 → 结果）

### S1 本地报表（doc_parse ×2）
- 【调用】01 doc_parse tests/sample.xlsx → 0.5s，content_ok=true，69 字；02 tests/gbk_sample.csv → 0.0s，117 字，metadata.encoding=**gb18030**（自动识别，未问一句）。
- 【反应】「行数和列名都点到了，行。」xlsx 只有 69 字是文件本身就小（样例表），非工具问题。

### S2 盯站增量（feed_read ×2 + cred_defaults ×1 + 重试 ×2）
- 【调用】03 feed_read [127.0.0.1:8399]/feed.xml since='3d' → **具名拒绝**：URL targets internal/private IP，next_action 点名 cred_defaults 路线。
- 【反应】「哦本地站点也要报备啊。」按提示 04 cred_defaults(set={'allow_private': true}) → changed:[allow_private]；05 feed_read since='3d' → 行级拒绝 since='3d' is not a date - use ISO-8601 (2026-09-20…) **顺带把两种合法格式都写出来了**；06 ruanyifeng atom since=2026-10-18 → 1 行 1 条（items_older_than_since=2 如实计数）。
- 【反应】「3d 不行，提示里连示例日期都给了，照抄就行。」
- **环境注**：本轮 dhole 进程 21:31 重启过一次，重启后私网门恢复默认 → 摩擦路线在**本进程**真实走完（allow_private 是进程内存态，不落盘——见发现 F2）。

### S3 读正文（web_read ×1）
- 【调用】07 web_read /article focus=结论 → 0.0s，429 字，fetcher_used=**cache**（回执自己声明 cached copy / replayed from the cache——3.5min 前另一次会话的抓取，跨进程磁盘缓存命中且不装新抓）。
- 【反应】「缓存回放说清楚了，不是新抓的，行。」

### S4+S5 既要又要 → ISO 重试（feed_read ×2）
- 【调用】08 feed_read since='10d' + max_items=3 → 同样行级拒绝；09/10 追加发（真人自然重试）：since=2026-10-22 → **6 条 = 4 条带日期(0–3 天) + 2 条无日期**，items_without_date=2 / items_older_than_since=2 按**全部 8 条**计数；since=2026-10-15 + max_items=3 → 回 3 条，note 明说「counts 是对全部 8 条算的，不是对发你的 3 条」。
- 【反应】「窗口和条数两件事互不干扰，无日期的默认当新处理还标了计数——这个放心。」

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因（猜测标） | 严重度 |
|---|---|---|---|---|---|
| 21:33 | S2 首发 | 「本地站点还要报备？」 | 8s（读懂+放行） | 私网门默认拒绝（D，设计；报错点名出路所以能自救） | 皱眉 |
| 21:33 | S2 since='3d' | 「又不认 3d」 | 10s | since 只收日期不收档位（D；报错给了两种格式示例，自救零成本） | 皱眉 |
| 21:34 | S2 行级错误 | 「顶层只说 1 rows errored，哪个 URL 错了得点进去看」 | 5s | feed 行级错误在 rows[].error，顶层摘要不带 URL（猜测：批量行通用形状） | 骂人边缘 |
| 21:35 | S3 缓存 | 「429 字是不是太少」 | 0 | focus 生效后的正文（非缺陷） | 无感 |
| 21:35 | S4 重试 | 「我照它给的示例抄就过了」 | 0 | 同上 since 格式 | 无感 |

## 放弃点

无（4 个卡点全部按报错提示自愈）。

## 主观评分（1–5，每分一句理由）

- 第一印象 4：报错都是「人话+出路」，第一分钟就自己解开了两个门。
- 找答案速度 4：feed→正文一条链 3 步走完。
- 答案可信度 5：计数（older/without_date/max_items 裁剪）全部对得上账。
- 报错可读性 4：ISO 示例日期直接给；扣一分在批量行错误的顶层可见性。
- 等待感受 5：全程单发 ≤1s（除真实网络源 0.6s）。
- 总体满意 4.3。

## 能力边界遭遇

私网门（设计内，出路清楚）；since 格式（设计内，示例清楚）；无。

## 与「工具视角」的差异

工具视角看「行级错误+计数诚实」是正确实现；用户视角看「顶层不点名哪个 URL 错了」要展开才知道。

## 改进清单（按感知强度）

1. 【我想骂人→希望】feed 批量某行出错时，顶层 summary 直接把出错的 URL/原因带一句，而不是只给「1 rows errored」。
2. 【希望】since 认「3d/7d」这类档位词（或报错里说明只收日期并链接到字段词典）。
3. 【希望】allow_private 这类豁免能点名主机（当前只想放行 127.0.0.1:8399 一家，却全开了回环）——P07 会走点名路线验证。

## 附录

关键调用：03（私网拒绝）、05（since 拒绝）、09（6 条含无日期）、10（max_items note）。靶站旁证：fixture-8399.log 本阶段仅 feed.xml/article 的 GET。
