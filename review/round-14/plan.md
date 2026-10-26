# round-14 计划（P0 封存）· 主题：回访与新鲜度（缓存/check/since/offset/recall/会话连续性）

> 分支 review/round-14 · 基线 f5f9340（round-13 收官）· 服务 127.0.0.1:9214 + 靶站 8399。
> **现场特别说明**：工作树有另一位 agent 的未提交改动（src/tests/docs 多文件，含新测试与
> 新工具动作）。按用户指示不打扰、不提交、不回滚其改动；本轮被测对象 = **当前工作树**
> （editable）。P6 只暂存本轮自己的文件与本轮修复 hunk，绝不整文件吞掉他人的改动。
>
> 主题来源：r13 刚修完「缓存回放丢 selector 注记」，而真实用户最常做的正是**回访**——
> 同一页读两遍、盯订阅源增量、看长文续片、用 recall 找上周读过的页、隔天查有没有更新。
> 本轮 60% 打这些高发区，40% 投向 schema 里新露出的面：`extraction_type=check`、
> feed 相对 since（'3h'/'3d'/'2w'）、offset 续片 summary、cache_ttl 语义。

## 十张画像卡（封存，执行前不改）

### P01 方寸 · 图书馆采编员 L2
- 常用：带工具的 AI 助手。情境：周一早上要交本周新书清单，先看出版社订阅源 3 天内的新条目，
  再回查上周读过的书目页有没有改版。目标：用相对 since 筛增量；同一页读两遍确认缓存回放诚实；
  recall 找回上周的书评页。验收：`3d` 从当下起算、`items_older_than_since` 对账；第二次读
  `cached=true` 且 `fetched_at` 不伪装成现在；recall 命中上周页。变卦：若 `3d` 不认就退 ISO 日期
  （记摩擦，不记缺陷，除非报错不教格式）。主攻：feed_read + web_read + web_recall。

### P02 陆知微 · 专利审查员 L3（猎手）
- 情境：审查意见要反复引用同一篇专利族页面，必须确认「缓存里这份是不是我上次读的那版」。
  目标：先读一遍、立刻带 `cache_ttl=0` 再读（应真回源）、再用 `extraction_type=check` 问
  「与我上次读的比变了没有」；长文用 offset 续片；最后 sys_state 复盘缓存账。
  验收：check 的 changed/unchanged 与时间戳有账；cache_ttl=0 的回执不能是 cache；offset 续片
  summary 点名切片；state 与调用对得上。刁难：同一 URL 连打三种读法，看会不会互相污染缓存。
  主攻：web_read(cache_ttl/check/offset) + sys_state + sys_guide。

### P03 周小满 · 咖啡馆店主 L1
- 情境：每天早上看店里菜单/供货价目页有没有变，不懂技术，只会说「帮我看看今天变了没」。
  目标：让 AI 读同一页两遍并解释为什么第二次更快；再看订阅源的新品。
  验收：L1 能听懂 cached/fetched_at 的解释；没有被一句现在时的「刚抓的」骗到；feed 拿到 1-2 条。
  脾气：最多容忍两次「稍等」；看不懂英文术语会追问。主攻：sys_guide + web_read + feed_read。

### P04 韩冬 · 供应链计划员 L2
- 情境：盯 3 个供应商规格页，想知道哪些改过；一次批量读三页；把变更记到本地交接站（写操作）。
  目标：`urls=` 批量读逐行账；对本地靶站发一次 POST 记变更（无 confirm → 闸；带 confirm 真写）。
  验收：批量行数=3 且逐行有账；写闸教学句；confirm 后服务端日志 +1。主攻：web_read(urls=) +
  web_request(POST 本地)。

### P05 沈青禾 · 数据记者 L3（猎手）
- 情境：赶稿要写「某数据集的更新频率」，需要一份可引用的证据包，再找回自己上周读过的页面，
  并对同一 URL 做变化检查。目标：research 证据包（失败源独立成账）；recall 命中；check 有账。
  验收：research 的 sources/failed/more_hits 分账；recall 结果带 url+title+snippet；check 的
  changed 结论与上一读有关。主攻：web_research + web_recall + web_search + web_read(check)。

### P06 唐栗 · 兽医 L2
- 情境：查一份药典 PDF 的指定页（本地文件），同时盯药品公告订阅源；同一剂量页读两遍确认版本。
  目标：doc_parse 本地 PDF 指定页；feed since；web_read 缓存回放。
  验收：PDF 页范围生效、内容含目标词；feed 的 since 计数自洽；第二读 cached 且说明来历。
  主攻：doc_parse + feed_read + web_read。

### P07 顾之遥 · 博物馆策展人 L2
- 情境：给馆藏站点做一份地图，挑 3 页抓下来，再把一个详情页 pin 住、改标签后回读；最后 recall。
  目标：discover 行可点；crawl 行账；pin+session_id 跨调用留住页；recall 找到展品页。
  验收：discover 行数=可读页；crawl 行 content_ok 有账；pin hit 句 + 改值回读；recall 命中。
  主攻：web_discover + web_crawl + web_act(pin) + web_recall。

### P08 马小川 · 体育数据分析师 L3（猎手）
- 情境：盯联赛数据源（相对 since '6h'），用 search 找最新战报，再读同一页两遍看缓存。
  目标：相对 since；feed 混发（好源+404）逐行分账；search 越界参数具名；第二读 cached。
  验收：6h 从当下起算；404 行具名；max_results=0 clamp 落 summary；cached 账诚实。
  主攻：feed_read + web_search + web_read。

### P09 花婶 · 园艺师 L1
- 情境：看花卉市场订阅源一周新品，把一份本地 Excel 养护表读成文字，再读一页行情。
  目标：feed since '1w'；doc_parse xlsx；web_read；state 收尾。
  验收：L1 零迷路；since 计数对；xlsx 单表读出点名行；state 与调用对上。
  主攻：feed_read + doc_parse + web_read + sys_state。

### P10 罗青 · 编剧 L1
- 情境：查一段历史资料，长文被截断后要续读；隔天再看同一页有没有更新。
  目标：`max_content_chars=500` 截断 → `offset=next_offset` 续读；再用 check 问有没有变；
  搜一条背景资料。验收：续片 summary 点名切片、无重复行；check 结论与时间戳有账。
  主攻：web_read(offset/check) + web_search + sys_state。

## 覆盖核对

- 职业 10 全新：图书馆采编/专利审查/咖啡馆主/供应链计划/数据记者/兽医/策展人/
  体育数据分析/园艺师/编剧——r01-r13 均未用 ✓
- 成熟度：L3×3（P02/P05/P08）、L1×3（P03/P09/P10）、L2×4 ✓
- 工具 13/13：cred 全员；read 全员；feed P01/03/06/08/09；recall P01/05/07；
  research P05；search P05/08/10；crawl+discover+act P07；request P04；parse P06/09；
  state P02/09/10；guide P02/03 ✓
- 任务类型 ≥4：信息获取（P01/P08）、干活产出（P05/P09）、页面交互（P04/P07）、
  边界刁难（P02/P10）、长任务跨会话（P02/P07）✓
- 失败面 ≥2：P04 无 confirm 写闸、P08 feed 404 混发、P10 截断续读边界 ✓
- 新写场景 ≥30%：check / 相对 since / offset 续片 / cache_ttl=0 / 回访对照 ✓

## 回归绑定（round-13 必测 6 项）

1. 显式 css_selector 收窄不报 collapse（R13-001）→ P10 旁支加一发点名取块。
2. 缓存回放带 selector 注记（R13-002）→ P02 同 URL+selector 两读（live/hit 都应有句）。
3. 提交闸 confirm（action 级）→ P04 写闸 + confirm。
4. dialog prompt 正形/误序 → 本轮不主打，P07 旁支抽 1 发（answer+prompt_text）。
5. tab/pin 寿命 → P07 pin+session_id 命中；tab 不做主链（r13 已深打）。
6. pin / auth_wall / feed 三连+混发 / recall clamp / frame 错名 家族抽检 → P07 pin、
   P08 feed 三连+混发、P01/P05 recall、P10 css 收窄。

## 执行参数

- 节流：画像间 ≥18s；链环间 5-8s；search ≥10s；不并发。
- 端口 9214；env DHOLE_IGNORE_ROBOTS=1（robots 门不可观测）。
- 判定前读全四账面字段（summary/error/next_action/fetch_hint）且 summary 全文。
- 补条脚本用独立 persona 号（p11+），不复用主跑目录。
- **并发纪律**：模拟阶段不读 src；审查阶段可读；P4 若需改 src，只改当前缺陷相关 hunk，
  避开另一位 agent 正在改的注释区域；不 `git add` 整文件。
