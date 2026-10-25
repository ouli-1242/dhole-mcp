# round-10 计划（P0 封存）· 主题：预算与时间（timeout/ceiling/cache-TTL 全工具对账）

> 分支 review/round-10 · 基线 5ad954f（round-09 收官提交）· 服务 127.0.0.1:9210 + 靶站 8399。
> 主攻**时间预算的合同**：显式 timeout= 是合同（R02-004 家产）、learned ceiling 的让位与申报、
> 全工具的预算分摊（research 跨源）、cache TTL 的代际账（fetched_at/stale）、慢页与死页的
> 等待账——「说好多久就是多久」逐工具对账。八轮来时间语义只在 R02/R05 被顺带碰过。
> 回归压力：round-09 必测回归点 + 各轮家产。

## 十张画像卡（封存，执行前不改）

### P01 阮鸿飞4 · 无人机飞手 L2
- 目标：显式 timeout= 的合同：同一 URL timeout=5000 与缺省各一发，attempt 账与 learned ceiling 让位语义对账（R02-F4 照做）。
- 验收：显式预算不被压；attempt_cap_ms 如实。
- 主攻：web_read(timeout) + sys_guide。

### P02 白景舟2 · 外汇交易员 L3（猎手）
- 目标：ceiling 攻击：极小 timeout（1ms/5ms）与超长 timeout（300000 边界外）的拒绝/钳制形状；重访同一 host 时 learned ceiling 的学习与申报。
- 验收：边界值第一拒具名；learned 句如实（冷 EMA 无样本不念——round-02 教训在册）。
- 主攻：web_read/web_request 边界 + cache。

### P03 阮鸿飞4b · 咖啡师 L1
- 目标：照手册走读路径不设 timeout（缺省语义），慢页（/longaside）照读成功。
- 验收：缺省预算够用且申报（attempt_cap_ms/learned 句）。
- 主攻：web_read + feed_read。

### P04 越千帆4 · 电梯检验师 L3（猎手）
- 目标：cache TTL 代际账：同一 URL cache_ttl=3600/0/缺省三态，fetched_at 与 cache 行的年岁申报逐发对账；过期行（ttl=1 后 sleep）的 replay 账。
- 验收：cached copy fetched N ago 如实；ttl=0 真回源；代际不串。
- 主攻：web_read(cache) + web_recall(stale)。

### P05 云苏4 · 同声传译 L3（猎手）
- 目标：research 预算分摊：timeout=30000 两个源（一慢一快）——总预算怎么分、慢源被掐时账目怎么写（R01 家产「大 timeout 分摊」回归）。
- 验收：逐源 duration/失败行具名；总预算不被单源独吞。
- 主攻：web_research(timeout)。

### P06 屠维4 · 婚礼策划 L1
- 目标：批量 urls= 的每页时间（一次性 6 页无 timeout），落页逐行 status/fetched_at 对账。
- 验收：批量无显式预算时每页都有账；无页被无声丢弃。
- 主攻：web_read(urls=) + sys_state(cache)。

### P07 甘棠4 · 验光师 L2
- 目标：重复测量一致性：同 URL 同参数三连发（全 cache 命中）——三张回执的行账是否一致（cache replay 的诚实形状，round-02 W3 教训：无 attempt 句=正确）。
- 验收：replay 行不伪造 attempt；consistent。
- 主攻：web_read + sys_state(cache)。

### P08 桑无咎4 · 船长 L2
- 目标：crawl 深预算：max_pages/max_depth/depth≤2 的 stopped 原因账（max_pages vs max_depth vs 空池）三种现场。
- 验收：stopped 原因逐次如实。
- 主攻：web_crawl(budget)。

### P09 库尔班4 · 兽医师 L1
- 目标：时间参数的第一拒自救：timeout=0/负数、cache_ttl=-1、page 类越界——按拒绝句改正重试。
- 验收：每个拒绝句教合法范围；自救真通。
- 主攻：web_read/web_search 边界。

### P10 越千帆4b · 数据标注员 L3（猎手）
- 目标：组合态注入：research timeout=30000 配慢源矩阵、act 步级 deadline（wait/wait_selector 的 30s 帽申报）、crawl+read 混跑的 cache 代际交叉。
- 验收：组合态预算账逐发可读；钳制申报不缺。
- 主攻：组合 + 旁支。

## 覆盖核对

- 职业 10 全新（无人机飞手/外汇交易员/咖啡师/电梯检验师/同声传译/婚礼策划/验光师/船长/兽医师/数据标注员）；L3×3（P02/P04/P05）、L1×3（P03/P06/P09）✓。
- 13/13 目标：read/search/recall/research/crawl 主攻 + act/discover/feed/request/doc_parse/state/guide/cred 旁支编织 ✓。
- 回归绑定：round-09 必测 6 项 + R02-004 显式 timeout 家产 + round-02 W3 缓存回执教训 + 各轮家产。

## 执行参数

- 节流：画像间 ≥18s；场景间 5-8s。
- 端口：9210；env DHOLE_IGNORE_ROBOTS=1（注明不可观测）。
- 方法论：先引手册/描述原句 → 真机回放 → 记「合/不合」；**判定前读全四账面字段**（summary/error/next_action/fetch_hint——三轮假候选的教训）。
