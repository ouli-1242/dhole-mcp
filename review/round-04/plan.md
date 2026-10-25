# round-04 计划（P0 封存）· 主题：状态与写路径

> 分支 review/round-04 · 基线 7cd2bc0（round-03 收官提交）· 服务 127.0.0.1:9204 + 靶站 8399。
> 本轮主攻前几轮没真用过的**状态面**：写闸 confirm 全链、cred_defaults 持久化（DHOLE_PERSIST_CREDENTIALS=1）
> 与重启、watch 持久化跨重启、robots 受控三态（旁证进程 env 关，只指本地）、缓存 provenance 对账、
> check 增量语义、sys_state 对账。回归压力：common-issues 必测 9 项绑定进场景。
> 宪章 §8：≥6 职业、≥2 L3 猎手、≥2 L1、≥4 任务类、跨轮三元组去重（30 职业已用，本轮全换新）。

## 十张画像卡（封存，执行前不改）

### P01 沈砚青 · 站点管理员/SEO · L3（缺陷猎手）
- 情境：给公司站做收录体检，不急，较真。
- 真实目标：robots 受控三态（旁证 9205 env off，只指 8399）+ crawl path_include 语义 + sys_state census 对账。
- 验收：三态各自成立且互不污染；census 与实际调用对得上。
- 不告诉 AI：靶站 robots.txt 里 /private/secret 是 Disallow。
- 变卦点：中途改 path_include。
- 主攻：robots 三态（受控）+ web_crawl + sys_state；绑定回归 P10b 三态。

### P02 顾晚舟 · 测试开发 QA · L2
- 情境：给团队选型自动化工具，会刁难。
- 真实目标：web_act 写路径深水——upload、dialog、iframe（/frame）、hoverdrag、keyboard 提交。
- 验收：每步有收据；该拦的拦（无 confirm 的 submit）。
- 不告诉 AI：/frame/inner 才是目标。
- 变卦点：从 click 改 press Enter。
- 主攻：web_act 全动作面 + web_request；绑定回归 R01-010 drag 全形、R01-006 同链取值。

### P03 秦守拙 · 档案管理员 · L2
- 情境：年度归档，慢工。
- 真实目标：cred_defaults 落盘（DHOLE_PERSIST_CREDENTIALS=1）→ 重启后凭据与 allow_private 还在；watch 清单跨重启还在。
- 验收：重启前存、重启后读，零重配；watch run 用落盘豁免真的能查私网（对照 R02-001 出路的另一半）。
- 不告诉 AI：第一遍故意用 memory 模式让它撞一次重启清零。
- 变卦点：中途 add 第二个 watch 目标。
- 主攻：cred_defaults + watch(CLI) + sys_state；绑定回归 R02-001 的出路句（本轮验证它指向的路真的通）。

### P04 贺兰珠 · HR 招聘 · L1
- 情境：筛候选人作品，不急，AI 用得浅。
- 真实目标：登录一次（fixture /login）之后一直能读 /account；存候选页后跨会话找回。
- 验收：登录态在会话里保持；凭据不进参数（cred_defaults 路）；找回有结果。
- 不告诉 AI：密码。
- 变卦点：换第二个候选人页。
- 主攻：web_act(login) + cred_defaults + web_recall(跨会话) + sys_guide；绑定回归 R01-009。

### P05 佟既明 · 游戏策划 · L2
- 情境：配表要和线上数据对账。
- 真实目标：doc_parse xlsx/csv + web 抓数据源交叉核对（数字级）。
- 验收：表格数字能对上页面数字；merged cells 披露。
- 不告诉 AI：两处数字故意差一格。
- 变卦点：只对第二张表。
- 主攻：doc_parse + web_read + web_research；绑定 R02 良好行为组。

### P06 卫朝雨 · 保险理赔 · L2
- 情境：核赔流程要走完表单链。
- 真实目标：checkout 全链（该问就问、confirm 后真提交）+ 404 死链对账 + check 增量（改了才报）。
- 验收：确认闸三跳闭合；check 第二次调用只说变化。
- 不告诉 AI：理赔页其实已下线（404）。
- 变卦点：改数量后再 check。
- 主攻：web_act + web_read(check) + sys_guide。

### P07 华青野 · 健身教练 · L1
- 情境：给学员找训练计划，随性。
- 真实目标：搜→读→存→（跨会话）找回；视频页如实告知。
- 验收：三步内拿到能用的计划文本；视频页/索引页说人话。
- 不告诉 AI：学员只练家中无器械。
- 变卦点：改搜居家版。
- 主攻：web_search + web_read + web_recall(跨会话)；绑定 E9/E12 观察。

### P08 商陆 · 审计 · L3（缺陷猎手）
- 情境：对账缓存与真实源，怀疑一切。
- 真实目标：同一 URL cache_ttl=0 vs 缓存回放的 provenance 对账；/changing 连读两次看 check 语义；sys_state 的 cache census 与实际一致。
- 验收：每发回执说得清「这是哪一刻的哪一层」；check 的 diff 真指变化。
- 不告诉 AI：/changing 每请求都变（fixture 设计）。
- 变卦点：要求 include_headers 对账。
- 主攻：web_read(cache_ttl/check/include_headers) + sys_state；绑定 R02 缓存澄清条目。

### P09 阮青梧 · 心理咨询师 · L2（隐私敏感）
- 情境：把个案资料放内网 wiki，非常在意边界。
- 真实目标：私网豁免的**边界**——allow_private 点名主机 vs 全开；ignore_robots 的语义与诚实标注；错误行话术是否吓人或含糊。
- 验收：门说什么就是什么；点名清单生效（名单外仍拒）；robots 语义三句以内说清。
- 不告诉 AI：内网还有第二台主机。
- 变卦点：撤掉豁免（unset）再试。
- 主攻：cred_defaults(allow_private 主机清单) + web_read + sys_state；绑定 R03-001 澄清条目。

### P10 尉迟雪 · 旅游博主 · L1
- 情境：写游记找素材。
- 真实目标：读带图页、QR 页（/qr）、需要浏览器的页；跨会话找回来。
- 验收：图片/QR 页不炸、说清拿到了什么；找回有一句能用的摘录。
- 不告诉 AI：目的站是 QR 码页。
- 变卦点：要求「只要文字不要图」。
- 主攻：web_read(特殊页) + web_search + web_recall(跨会话)；绑定 E9。

## 覆盖核对（封存自检）

- 职业 10 种全新（站点管理/QA/档案/HR/游戏策划/保险/健身/审计/心理咨询/旅游博主）；L3×2（P01/P08）、L1×3（P04/P07/P10）✓。
- 任务类：页面交互（P02/P06）/ 边界刁难（P01/P08/P09）/ 长任务跨会话（P03/P04/P07/P10）/ 信息获取（P05/P07/P10）≥4 ✓。
- 多工具链 ≥3：P01/P02/P03/P05/P06/P08/P09 ✓。
- 工具 13/13 目标：web_read（多数）/web_act（P02/P04/P06）/web_request（P02）/web_crawl（P01）/web_discover（P01 旁支）/web_search（P07/P10）/web_research（P05）/web_recall（P04/P07/P10）/cred_defaults（P03/P04/P09）/sys_state（P01/P03/P08/P09）/doc_parse（P05）/feed_read（P06 旁支 RSS）/sys_guide（P04/P07）✓。
- 刻意失败 ≥2：P09 名单外主机仍拒、P06 404 死链、P08 /changing 每请求必变、P03 memory 模式撞重启清零 ✓。
- 回归绑定 ≥60%：P01(P10b 三态)/P02(R01-010/R01-006)/P03(R02-001 出路句)/P04(R01-009)/P05(良好行为组)/P08(R02 缓存澄清)/P09(R03-001 澄清) ✓。

## 执行参数

- 节流：画像间 ≥18s；场景间 5-8s。
- 端口：9204；旁证进程 9205（P01 robots 三态专用，env 关，只指 8399）。
- env：DHOLE_IGNORE_ROBOTS=1（主进程 robots 不可观测——报告注明）；P03 的持久化档案需要一次**真重启**（P1 内做：杀 9204 → ensure_up 再起），重启事件记 report。
- P03 的持久化模式：cred_defaults 落盘依赖 DHOLE_PERSIST_CREDENTIALS=1——旁证或重启 env 注入（不污染主进程）。
