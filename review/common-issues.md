# 跨轮缺陷 taxonomy 与高发区

> 每轮更新；是下一轮画像与场景设计的输入。种子 = manual-2026-10-25 摸底轮 + loop/ref/env-facts.md。

## 高发区（按观察频次）

| 区 | 现象 | 状态 | 来源 |
|---|---|---|---|
| web_request × 会话 | POST /login 的 Set-Cookie 不落任何后续可用的 jar；web_request 不收 session_id | **已修**（3cf035e 内，manual 轮 P2）| manual report / probe_cookie_jar.py |
| web_research 预算 | timeout 分摊可超 web_read 120s 上游校验 → 整源被拒而非钳制 | **已修**（3cf035e 内，manual 轮 P3）| manual report |
| 协议版本 | mcp 2.2.0 客户端（要 2026-07-28）连不上 dhole（最高 2025-11-25）；原生 MCP SDK 宿主全灭 | **待裁决**（B/D/上游）| env-facts §7.1 |
| 代理档位 | httpx 系读注册表系统代理、primp 系不读 → 同进程不同出口 | **待验证** | env-facts §7.2 |
| 引擎环境 | brave circuit open、shenma/ddg blocked（CN 线）→ 降级池如实上脸（families_answered 3 of 9） | 环境（C），非缺陷 | manual report |
| 批量读行键 | 键名是 results 不是 pages——调用方脚本易写错 | D（schema 如实），观察 | manual report |

## round-01 新增/复发（P3 裁决后）

| 区 | 现象 | 状态 | 来源 |
|---|---|---|---|
| feed_read 缺省预算 | wire 分发缺参塞 20 被当毫秒 → 全部不带 timeout 的调用顶着 1s 预算 | **已修**（R01-008，fix-R01-F1）| P10 hnrss 两发全挂 |
| resume 自荐形状 | next_action 教的 resume_token 调用缺 url，照抄撞门 | **已修**（R01-003，fix-R01-F2）| P02 |
| skill 文案计数 | start/SKILL 写死 eleven/ten，实际 13 颗 | **已修**（R01-009，fix-R01-F3）| P03 |
| 挑战页可发现性 | fill 猜错引用语法静默字面入站；confirm 层次不明 | **已修**（R01-006，fix-R01-F4；机制 read as=+fill{=} 本就可用）| P07 |
| drag 形状门 | 陌生键先被 _act_target 半句拦截，第一拒不教全形 | **已修**（R01-010，fix-R01-F5）| P08 |
| crawl search 空手 | 清空后 summary 借 max_pages 措辞 | **已修**（R01-002，fix-R01-F5）| P02/P05 |
| feed 顶层错误可见性 | rows errored 不点名 URL | **已修**（R01-001，fix-R01-F5）| P01 |
| harness env 传递 | PowerShell 双引号串内 $env: 赋值被外层展开成空 | **已修**（ensure_up.ps1 顶层赋值；产品无罪）| R01-005 |
| 超时预算非硬杀 | 溢出过 grace 时自白「did not stop when told」 | wontfix(evidence)：设计取舍，自白+budget_advice 是设计内出路 | P09（R01-007）|
| 同 URL 变体 / offset 语义 / 静默浏览器 / 钳制精度 / 低分召回 | E 类 | backlog E2–E6 | 多画像 |

## round-01 必测回归点（新画像真实回访）

1. web_request POST /login + session_id → web_read /account 登录态（manual P2 的修）。
2. web_research 大 timeout 分摊不再整源被拒（manual P3 的修）。
3. 降级池如实报告（families_answered_of_asked / engine_detail）。
4. 私网门具名拒绝 + cred_defaults 豁免路线（env-facts §3）。
5. 提交闸全链路（web_act submit 无 confirm 不发）。

## 新探查点（round-01 首攻）

- web_recall 跨会话召回的 stale 标注准确性
- web_discover→web_crawl 的地图/断点续爬（resume_token）
- feed_read since= 增量语义（含 undated 条目计数）
- focus / 结构化 / OCR 档（本轮广谱基线尚未触过的档）
- doc_parse 非 UTF-8（GBK CSV）与多 sheet
