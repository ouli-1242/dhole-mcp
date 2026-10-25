# round-07 计划（P0 封存）· 主题：手册与实况（文档诚实轮）

> 分支 review/round-07 · 基线 0702edf（round-06 收官提交）· 服务 127.0.0.1:9207 + 靶站 8399。
> 主攻**文档与实况的对账**：sys_guide 四篇（start/call/behavior/configuration）里每一条可执行承诺、
> 工具描述里的每个参数断言——画像按手册照做，逐条记「手册说的」vs「实况给的」。六轮修复后的
> 文档面是否还与行为同步，是本轮的全部问题。回归压力：前六轮家产合并回访。

## 十张画像卡（封存，执行前不改）

### P01 阮鸿飞 · 新入职后端 L2
- 目标：把 sys_guide(start+call) 当 onboarding 手册逐条照做（路由三分支、参数禁地、placement 陷阱）。
- 验收：手册说的每个「照做就成」真成；说的每个「别做」真拦。
- 不告诉 AI：会故意按过时的句子做（若有过时句）。
- 主攻：sys_guide(start/call) + web_read/web_crawl 照做对账。

### P02 屠维 · 技术作家 L3（猎手）
- 目标：behavior 篇的交互章节逐条真机回放（read as=/fill {=}/confirm 层次/pin=true/session_id）。
- 验收：每条示例代码照抄可跑；误差即候选。
- 不告诉 AI：会先跑再说。
- 主攻：sys_guide(behavior) + web_act 全链照抄。

### P03 库尔班 · 运维 L1
- 目标：start 篇的三条路线从 L1 视角照走一遍（URL 有→read；URL 无→search/crawl；盯站→feed/watch）。
- 验收：L1 照手册不迷路。
- 主攻：sys_guide(start) + web_search/web_read/feed_read。

### P04 黎照 · 合规审计 L2
- 目标：configuration 篇的环境变量清单逐条对照 sys_state 实况（名字/默认/生效）。
- 验收：env 名与 sys_state 显示一致；默认值与文档一致。
- 主攻：sys_guide(configuration) + sys_state。

### P05 云苏 · 全栈工程师 L2
- 目标：web_read 描述里的每个参数断言（max_content_chars 范围/offset/extraction_type 全谱/urls 上限 10）真机点验。
- 验收：断言与行为一一对应。
- 主攻：web_read(参数面) + sys_state。

### P06 慕容雪 · 自媒体 L1
- 目标：skill 文案的「12 颗日常 + 全部 13 颗」清单对照实测（哪颗在哪个场景该用）。
- 验收：清单数对、路由对。
- 主攻：sys_guide(start) + 多工具浅用。

### P07 越千帆 · QA L3（猎手）
- 目标：错误消息里的「合法值名单」逐个验证（force_fetcher/focus_mode/extraction_type/plan/resume 等）——名单外真拒、名单内真收。
- 验收：名单与门一一对应；多一个少一个都是候选。
- 主攻：web_read/feed_read/web_act 的参数边界。

### P08 戚顾 · 平台工程师 L2
- 目标：sys_state 的 show= 各键（文档列了哪些）逐个真查；clear= 的成本句对账。
- 验收：show 键全存在且各说了真话。
- 主攻：sys_state(show=/clear= 语义对账)。

### P09 甘棠 · 客户成功 L1
- 目标：报错→按报错自救全流程（手册说「错误会教你」——逐条验）。
- 验收：三类错误（私网门/写闸/robots）的出路句真走得通。
- 主攻：web_read/web_act + cred_defaults；绑定 R02-001/R04-002 家产。

### P10 桑无咎 · 架构师 L3（猎手）
- 目标：工具描述里「No parameter disables it」「never opens cloud metadata」一类**绝对句**逐条攻击（找反例）。
- 验收：绝对句守得住；守不住即候选。
- 主攻：web_read/archive/私网谱全攻击面。

## 覆盖核对

- 职业 10 全新；L3×3（P02/P07/P10）、L1×3（P03/P06/P09）✓。
- 工具 13/13 目标：web_read（多数）/sys_guide（全员）/sys_state（P04/P05/P08）/web_act（P02/P09）/web_search（P03/P06）/feed_read（P03）/watch（P03 旁支）/web_crawl（P01）/web_request（P05 旁支）/cred_defaults（P09）/web_recall（P06）/web_discover（P10 旁支）/web_research（P10 旁支）/doc_parse（P02 旁支）✓。
- 回归绑定：合并前六轮家产（common-issues 各轮必测清单）。

## 执行参数

- 节流：画像间 ≥18s；场景间 5-8s。
- 端口：9207；env DHOLE_IGNORE_ROBOTS=1（注明不可观测）。
- 方法论：每场景先引手册原句，再真机回放，再记「合/不合」——不合即候选（P3 裁决文档改还是行为改）。
