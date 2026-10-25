# 画像报告 · P01 温旖（内容创作者 · L2）· round-03

> 主题：选题库跨会话。8 发（含按报错指引的自救 4 发）+ 跨会话二访 2 发。p01- 前缀。
> 注：9203 是新进程，round-02 里进程级有效的 allow_private 豁免已被重启清掉——P01 第一发就撞上私网门。

## 场景实录

- **S1 找选题**：web_search 5 结果（engine 列表在场）。
- **S2 存两篇（第一撞）**：web_read 127.0.0.1:8399 三连 `invalid_request: URL targets internal/private IP`。**next_action 把门和钥匙一起给了**：「If this internal target is genuinely yours to fetch, the door is cred_defaults(set={'allow_private': true}) for loopback…」——L2 用户照做就能过。
- **S2b 按指引开门（真人自救）**：cred_defaults(set allow_private) → 再读三发全过（/collections 首发真抓 stealthy、二发缓存回放、/article http 真抓）。
- **S3 跨会话二访（明天）**：新 initialize（新 session id）→ web_recall「推荐专题 选题」→ **/collections 单行 `variants: 2`、`is_stale: false`**（R02-007 回归 PASS——两个抓取变体合并成一行，不再 stale 打架）+ /longaside 同行;模糊词第二查「文章」返回的是库里陈年外网新闻（stale: true 各归各行）。跨进程（9202 时代存的行 + 9203 新抓）召回照常工作。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因 | 严重度 |
|---|---|---|---|---|---|
| S2 首发三连拒 | 「这站怎么还锁」 | 3 发 | 私网门默认关（新进程豁免清零）；**报错教学好**所以一跳自愈 | 皱眉→无感 |
| S3 模糊词召回 | 「怎么是这些旧新闻」 | 1 发 | 神经召回对模糊词返回无关旧闻占前排（好在我要的那页排第一） | 无感 |

## 放弃点

无（门有钥匙、库有记忆）。

## 主观评分

- 第一印象 4 / 速度 5 / 可信 5 / 报错可读 5（私网门 next_action 教学现场）/ 等待 5 / 总体 4.8。

## 改进清单

1. 【保持】私网门的「门+钥匙」next_action 形状。
2. 【保持】recall 变体合并（本轮回归验证通过）。
3. 【希望】模糊词召回时把「最近抓的」排前一点（陈年外网新闻不该压过今天存的）。

## 附录

证据：sessions/persona-01/（run1 备份在 persona-01-run1/）+ p01-run.log。
