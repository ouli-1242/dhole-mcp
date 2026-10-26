# round-12 计划（P0 封存）· 主题：链条协奏（多工具真实链条，跨工具账目一致性）

> 分支 review/round-12 · 基线 120a5d7（round-11 收官）· 服务 127.0.0.1:9212 + 靶站 8399。
> 前十轮每轮深打单颗（act/检索/预算/冷面四颗），**链条从未当过主角**：真实用户不是
> 一发一发孤立地调工具，而是 discover 完接 read、feed 完接 parse、act 完 pin 住再回来。
> 本轮每张卡都是一条 2-4 颗工具的**真实任务链**，专盯接缝处：A 工具的产出喂 B 工具时
> 账目还对不对（URL 行可点？页码还活着？缓存代际一致？session 交叉认人？）。
> 回归压力：round-11 必测 6 项（feed 负数/max_items=0、confirm 形状、doc_parse 双码、
> 混发分账、第一拒 12 发、家产合并）+ r10/r09 各轮家产随链条自然回访。

## 十张画像卡（封存，执行前不改）

### P01 洛砚舟 · 建筑师 L2
- 目标：链条①discover→read→parse→recall：/collections 地图选行 → read 详情页 →
  构造图纸 CSV parse → recall 回查刚读过的词（索引是否已收）。
- 验收：discover 行点得进；parse 吃得下；recall 查得到刚读页（或如实说没索引）。
- 链：web_discover + web_read + doc_parse + web_recall。

### P02 岑万川3 · 证券交易员 L3（猎手）
- 目标：链条②search→research→read 三方对账：同一主题 search 结果行、research 来源行、
  read 正文互核（URL/标题/条数不串账）；顺带 r10 必测（timeout= 合同、attempt_cap 分层）。
- 验收：三方各行其账；越界 timeout 具名 clamp。
- 链：web_search + web_research + web_read。

### P03 白鹭洲 · 潜水教练 L1
- 目标：链条③guide→read→request→read：sys_guide(start) 引路 → 手册路线 read →
  POST /login（写闸语义 r11 必测2）→ /account 复查（session 交叉「欢迎回来」）。
- 验收：L1 沿教学句不迷路；写闸具名；会话认人。
- 链：sys_guide + web_read + web_request。

### P04 明溪 · 口腔医生 L1
- 目标：链条④feed→parse→read：feed since 增量（r11 必测1/4 回归：负数具名、0=all、
  混发分账）→ 新条目附件 parse → 原文 read 比对。
- 验收：feed 账目逐发可读；负数拒绝在链中依旧具名。
- 链：feed_read + doc_parse + web_read。

### P05 常镜秋 · 评估师 L2
- 目标：链条⑤act→pin→read 复核：snapshot → 表单填写 → register 一Call → pin=true
  同 session 挂页 → 第二发 read 同页复核（r08 家产：pin+session_id 合同）。
- 验收：跨调用页保持；register 值回读一致。
- 链：web_act + web_read。

### P06 屠万里 · 高尔夫教练 L2
- 目标：链条⑥search→recall→read：site=/引号短语过滤语义（r09 家产）→ recall limit
  clamp 回归（r11 修的 feed limit 同款思路，recall 越界注记还在）→ read 落点。
- 验收：site= 不越域；recall clamp 注记在场；read 打得开。
- 链：web_search + web_recall + web_read。

### P07 慕青梧 · 殡葬策划 L1
- 目标：链条⑦crawl→discover 对账：小站 crawl 3 页 → discover 地图行数对账 →
  断链页 read 如实报错；cred_defaults 打头。
- 验收：两把尺不串账；断链如实；L1 零迷路。
- 链：cred_defaults + web_crawl + web_discover + web_read。

### P08 闻岁酿 · 酒侍 L2
- 目标：链条⑧parse→feed→state：多 sheet/GBK parse（r11 必测3 双码+保真）→
  feed 混发（404/connection_refused 逐行）→ sys_state 盘点缓存与会话。
- 验收：坏行不拖垮好行；state 盘点与前面调用数对得上。
- 链：doc_parse + feed_read + sys_state。

### P09 聂怀古 · 文物修复师 L3（猎手）
- 目标：链条⑨第一拒链上抽样：错参数喂进链条**中环**（不是首环）——看断点处教学
  是否依旧颗颗带码前缀+出路（r11 必测5），链是否停在断点不硬闯。
- 验收：中环断点具名；无裸崩；错误后链可续。
- 链：13 颗参数面（链中穿插）。

### P10 岑晚舟 · 电网调度员 L2
- 目标：链条⑩request 写闸→state 复盘→recall 收尾：POST 写闸 confirm（r11 必测2）
  → sys_state 复盘本轮会话/缓存 → recall 收尾查历史。
- 验收：confirm 教在 next_action；state 复盘诚实；recall 有账。
- 链：web_request + sys_state + web_recall。

## 覆盖核对

- 职业 10 全新（建筑师/证券交易员/潜水教练/口腔医生/评估师/高尔夫教练/殡葬策划/酒侍/文物修复师/电网调度员——r01-r11 均未用）✓；L3×2（P02/P09）、L1×3（P03/P04/P07）✓。
- 13/13：read×6 链、search×2、research×1、recall×3、request×2、act×1、crawl×1、discover×2、feed×2、parse×3、state×2、guide×1、cred 打头 ✓。
- 回归绑定：round-11 必测 6 项 + r09 检索四件套 + r10 timeout 合同 + r08 pin 合同，全部嵌进链条而非单测复刻。

## 执行参数

- 节流：画像间 ≥18s；链环间 5-8s。
- 端口：9212；env DHOLE_IGNORE_ROBOTS=1（注明不可观测）。
- 方法论：先引手册/描述原句 → 真机回放 → 记「合/不合」；判定前读全四账面字段
  （summary/error/next_action/fetch_hint）——三轮 REJECT 教训（量具截断）为本轮铁律。
