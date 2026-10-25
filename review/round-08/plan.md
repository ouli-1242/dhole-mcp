# round-08 计划（P0 封存）· 主题：组合与长链（web_act 深水轮）

> 分支 review/round-08 · 基线 ba4e008（round-07 收官提交）· 服务 127.0.0.1:9208 + 靶站 8399。
> 主攻**多动作组合与跨调用长链**：read as= 同链寄存器、pin 人在中途、session_id 跨调用、
> iframe/dropdown/upload/dialog/drag 的动作面组合——单轮各测过的能力，这轮攻击「叠在一起还成立吗」。
> 深水错误注入（链中失败语义、寄存器重名、frame 错名、pin 滥用）由 L3 猎手承包。
> 回归压力：round-07 必测回归点 + 各轮家产（iframe 提示/上传三跳/confirm 闸/challenge 同链/显式 timeout）。

## 十张画像卡（封存，执行前不改）

### P01 阮鸿飞2 · 手游运营 L1
- 目标：照手册做「同链领取奖励」：read（读挑战）as=token → fill {=token} → click 提交，一条链内完成。
- 验收：同链寄存器组合一次跑通；失败时错误句把出路教出来。
- 主攻：web_act 同链组合 + sys_guide(call)。

### P02 慕容雪2 · 直播运营 L3（猎手）
- 目标：弹窗与页签动作面：/dialog 的 alert/confirm/prompt 三弹窗逐个真触发（#status 结果对账）；页签动作按描述真用。
- 验收：每弹窗动作的结果与 #status 一致；页签动作语义与描述一一对应；出入即候选。
- 主攻：web_act(dialog/tab) + sys_guide(behavior)。

### P03 甘棠2 · 电商卖家 L1
- 目标：session_id 跨调用购物车：/login（session s1）→ /checkout 填单加购 → confirm 提交 → /account 复查登录态。
- 验收：跨调用会话保持真成立；confirm 闸语义不破（R01 家产）。
- 主攻：web_act(session) + web_read。

### P04 黎照2 · 移动端开发 L2
- 目标：iframe 表单组合：/frame 外层读 → frame= 定位内层 #frame/inner 元素填提交（R04-F1 指引照做）；再试外层选择器误打内层（提示句复现）。
- 验收：frame= 三种指法（name/URL 前缀/CSS）至少两种真通；误打时的超时提示带 iframe 出路。
- 主攻：web_act(frame) + sys_guide。

### P05 云苏2 · 网络工程师 L2
- 目标：上传三跳 + 下拉选择：/upload 文件上传（确认闸三跳、「received <文件名>」落点）+ /dropdown select 动作。
- 验收：三跳闸不破（R04 家产）；received 文件名如实；select 语义清楚。
- 主攻：web_act(upload/select) + sys_state。

### P06 屠维2 · DevRel L2
- 目标：6+ action 长链：一次调用里 read as=a → read as=b（双寄存器）→ 两个 fill 各引其名 → click → 断言读。placement/顺序语义照手册。
- 验收：多寄存器不串名；顺序错了的错误句点名该改哪一步。
- 主攻：web_act(长链/寄存器组)。

### P07 越千帆2 · 技术招聘 L2
- 目标：人在中途双挑战：/challenge/text pin=true（读 token → 下一发填）+ /captcha 图形挑战（pin 保页 → 看图转写 → 填）。
- 验收：pin 保页 + 跨调用同 token；图形案流程与手册一致（R07-001 补句照做真通）。
- 主攻：web_act(pin/challenge) + cred_defaults。

### P08 桑无咎2 · 医疗器械注册 L1
- 目标：长表单逐字段：/checkout 或 /login 多字段照手册逐个 fill（含非法值一栏）再提交闸。
- 验收：L1 照手册不迷路；非法值的拒绝句教出路。
- 主攻：web_act(表单) + web_read。

### P09 库尔班2 · DBA L3（猎手）
- 目标：深水错误注入：①链中一 action 失败 → 整链语义（后面还跑吗？回执怎么分账）；②寄存器重名（两次 read as=同名）；③frame 错名；④pin 与 {=} 混用越界。
- 验收：每个注入的错误句具名且教出路；不静默、不串账。
- 主攻：web_act(错误注入) + sys_guide。

### P10 越千帆2b · SDET L3（猎手）
- 目标：组合态攻击：dialog+iframe 叠加（在 /frame/inner 里触发 confirm 弹窗）；两 session_id 交替切换不串状态；同链 read as= 后 cross-frame fill 引寄存器；tab 动作边界（无第二页签时切页签）。
- 验收：组合态全部具名可解释；串状态/静默即候选。
- 主攻：web_act(组合态) + sys_state(sessions)。

## 覆盖核对

- 职业 10 全新（手游运营/直播运营/电商卖家/移动端开发/网络工程师/DevRel/技术招聘/医疗器械注册/DBA/SDET）；L3×3（P02/P09/P10）、L1×3（P01/P03/P08）✓。
- web_act 动作面全家：fill/click/read as=/confirm/upload/select/hover/drag/dialog/tab/frame/pin/session ✓。
- 13/13 目标：画像内主攻 web_act/web_read/sys_guide/sys_state/cred_defaults；旁支补 web_search/web_research/web_recall/web_crawl/web_discover/doc_parse/feed_read/web_request ✓。
- 回归绑定：round-07 必测 5 项 + R04 iframe 提示/R02 写闸显式 timeout/R01 提交闸家产。

## 执行参数

- 节流：画像间 ≥18s；场景间 5-8s。
- 端口：9208；env DHOLE_IGNORE_ROBOTS=1（注明不可观测）。
- 方法论：每场景先引手册/描述原句，再真机回放，再记「合/不合」——不合即候选（P3 裁决文档改还是行为改）。
- 靶点地图（P0 预检在册）：/dialog（alert/confirm/prompt→#status）、/frame+/frame/inner（iframe name=app）、/upload、/dropdown、/hoverdrag、/challenge/text、/captcha、/login→/account、/checkout。
