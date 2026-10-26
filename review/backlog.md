# E 类 UX 缺口（不进修复队列，按用户感知强度排序）

> 种子：manual-2026-10-25。轮次列记录「已观察轮次」。**三现线**：同一现象跨三
> 轮复现 → 升级为待排期修复项（宪章 §8），在下表显式标注，不悄悄混进当轮修复。

| # | 现象 | 场景 | 感知强度 | 已观察轮次 | 状态 |
|---|---|---|---|---|---|
| E1 | 批量读行键 results 与直觉 pages 不一致，调用方脚本首写即错 | 批量读 | 皱眉 | manual + round-01 + round-02（P04） | **裁决 + 已修（文档面）**（2026-10-26：不改线上键名 `results`，以免打断老脚本；在 `web_read.urls` 参数说明、回执 summary、fields/tools 手册三处点名，并与 crawl 的 `pages` 对照） |
| E2 | search/recall 同 URL 多变体不去重；recall 两行 stale 标注打架 | 搜索/召回 | 皱眉 | round-01（R01-004）+ round-02（P10） | **已修**（R02-007 recall 按 URL 合并取最新；2026-10-26 补 search 侧：`utm_*` 按前缀从 URL 身份剔除，同页变体合并一行，真实查询参数仍分开） |
| E3 | offset 模式 summary/total_extracted_chars 说整页不说本片 | 长文续取 | 骂人边缘 | manual + round-01（R01-011）+ round-02（P05） | **已修**（2026-10-26 直改：窗口短于总量时 summary 说「窗口 of 总量」；`total_extracted_chars` 仍为整条路线长度） |
| E4 | crawl 升级 stealthy 浏览器时回执不点名（sys_state 里才发现） | 爬取 | 皱眉 | round-01（R01-012） | 观察中 |
| E5 | timeout 钳制消息展示剩余秒数（89→30）而非请求值 | PDF/大页 | 皱眉 | round-01（R01-013） | 观察中（R02-004 的 attempt 句已改为报 learned 值+默认值两个真数） |
| E6 | recall 低分命中（0.06 / 1/3 词）出榜，不如说「没找到」 | 召回 | 皱眉 | round-01（R01-014） | 观察中 |
| E7 | robots 缓存 metadata 的 fetched-under 标注来自铸造时刻，跨进程读易误判当前进程行为 | robots | 皱眉 | round-01（R01-005 旁证）+ round-02（P10 缓存 provenance 括号绕口令）+ round-04（P01 旁证撞主进程缓存，env 语境缺失活体） | 观察中（三轮三现） |
| E8 | since 只收 ISO 日期不收「3d」档位词（报错有示例，自愈成本低） | feed 增量 | 无感 | manual + round-01 + round-02（P05）+ round-03（P07） | **已修**（2026-10-26：`since` 收 `3h`/`3d`/`2w`，从本发时刻回量；回执 `since` 仍是绝对 ISO；`m` 不收因月/分歧义） |
| E9 | 微信签名链/视频类页面无类型徽标（返回正文但不说是嵌入流/外链跳转，L1 用户无法预判） | 特殊页面 | 皱眉 | round-02（P03） | 新增 |
| E10 | 404 错误行仍带 total_extracted_chars=3 数字噪声（错误行无正文可言） | 错误行 | 无感 | round-02（P09）+ round-03（P03/P06）+ round-04（P06） | 观察中（三现，临近升级线） |
| E11 | 硬墙主机烧满整个预算（http 被墙 → stealthy 30s/attempt 烧满 90s），全程无「此站常驻墙，换源」早停提示 | 反爬硬墙 | 骂人边缘 | round-02（P07 NEJM） | **wontfix 后记观察**（R02-005：早停需 per-host 失败记忆，误拒可恢复站点的风险 > 90s 等待代价；R02-004 显式预算已是真杠杆。若后续轮次再现，重开裁决） |
| E12 | recall 无「我存的」显著性：刚存的页会被语义更近的旧闻压出前排（排序契约是语义相关，recency 已在预筛）——产品演进方向：scope=mine 或本机抓过加权 | 召回 | 皱眉 | round-03（P04；P3 降级：机制无过，设计诉求） | 新增 |
| E13 | submit 闸的 next_action 写作 `{"confirm": true}`（像顶层），正形要嵌在 click/submit 动作里——模型照字面重试会二次被闸 | 页面交互 | 皱眉 | round-13（p10 S3；R13-003） | **已修**（2026-10-26 直改：next_action 改用 `_CONFIRM_SNIPPET` 的嵌套正形；手册三处同步点名「inside that submit action」） |
| E14 | dialog 错误文案第二种形状 `{"answer": ..., "prompt_text"?: ...}` 未说死 answer 只收 accept/dismiss，prompt 文本要走 prompt_text | 页面交互 | 皱眉 | round-13（p03 S3 / p21 C1；R13-004） | **已修**（2026-10-26 直改：拒绝句点名 answer 只收 accept/dismiss、prompt 文本走 prompt_text） |
| E15 | tab / iframe 没有像 upload 那样的 lifetime 句；跨调用状态消失要靠 pin（且需具名 session_id）才留住 | 页面交互 | 皱眉 | round-13（p09 S1→S2 / p21 B2 / p14 T2） | 新增 |
| E16 | 表单/菜单页 read 无正文（诚实标签「no prose」），next_action 可直接给 `{"snapshot": true}` 的 act 形状 | 读取形态 | 无感 | round-13（p01/p03/p10） | 新增 |
