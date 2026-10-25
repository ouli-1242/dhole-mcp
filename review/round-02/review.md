# round-02 对抗性审查（P3）· 2026-10-26

> 7 候选逐条攻击；补测 1 项（P10b 受控隔离三态）已计入 P10 报告。

## 裁决总表

| # | 候选 | verdict | 一句话理由 |
|---|---|---|---|
| R02-001 | watch CLI 与 allow_private 断链 | **半 REJECT 半 CONFIRM** | 「add 应拒私网」：REJECT——watch.py:_clean_url 注释明示 add-time 不设第二道规则（run 错误行呈现是设计）；「run 错误行不指路」：CONFIRM——CLI 进程内存态豁免够不着是结构事实，错误行只复述安全门原话。修：私网族错误行追加 watch 进程语境的出路 |
| R02-002 | archive 索引页 content_ok=true | **CONFIRM（B）** | server.py:_fetch_from_archive 接受任何 status<400 且非空的快照体——Wayback 的 capture 列表页（"978 captures"）原样盖章。修：快照体的索引页形状检测，拒绝并 note |
| R02-003 | 401 不走 archive + recover_with 矛盾 | **半 REJECT 半 CONFIRM** | 401 不触发：REJECT——quality.py:1297 docstring 明示「Does NOT fire on auth_required (archive won't have login-gated content)」，且 _ARCHIVE_FALLBACK_STATUSES=(404,410,451) 是同一决策；真缺口是**工具描述只说 "on a hard block" 超承诺**（CONFIRM，修描述点名状态族）。recover_with=''：CONFIRM——quality.py:status_verdict 对 401 返回空杠杆，而同封回执的 next_action 教 cred_defaults，两只手打架；auth_required 那行（同表）明明有杠杆。修：401/403 的杠杆补齐与 next_action 同源 |
| R02-004 | learned ceiling 压预算+每发必念 | **CONFIRM（B，两半）** | server.py:_adaptive_timeout 对见过的域**无视 default_ms**（显式 timeout 同样被压）——R02-041 注释自称 "caller's budget is the contract" 与实现不符，且用户侧无法用显式预算翻身；修：显式 timeout 经 ContextVar 让 learned 让位。噪声面：同句逐发重复；修：缩短为一句带杠杆的教学句（显式修复后「override with timeout=」这句才成真话） |
| R02-005 | 硬墙主机无早停提示 | **REJECT（设计取舍，记录并接受）** | 早停需要 per-host 失败记忆参与升级决策——新机制，误拒可恢复站点的风险大于 90s 等待的代价；预算本身到点真停了（R01-007 的失控未复发），per-attempt 30s 帽与 force_fetcher 已是设计内杠杆。backlog 记观察 |
| R02-006 | E1/E3/E8 三现升级 | **CONFIRM（E，记录）** | 三项都到跨轮三现线，但都是形状/措辞级、波及面大（schema 语义与参数面）→ backlog 显式升级标注，不混进本轮修复 |
| R02-007 | recall 同 URL 变体两轮 | **CONFIRM（B，升级）** | R01 P04 + R02 P10 两轮同形（stale 标注互相打架是可信度伤害，不只是重复）；recall.py 无 URL 合并。修：按 URL 合并，保留最新 fetched_at，variants=N |

## 补测

**P10b（回归#6 闭环）**：9203 受控进程（env 关闭）三态：无豁免→私网门拒；豁免+不带 ignore→`robots_disallowed`；豁免+ignore_robots=true→200 + label「bypassed (ignore_robots=true)」——per-call 档首次真实验证，三态各自独立成立。

## 最终确认队列（B 类）

1. **R02-002** archive 快照索引页守卫（server.py）
2. **R02-003** 401/403 杠杆补齐（quality.py）+ 工具描述点名 archive 状态族（server.py:12187）
3. **R02-004** 显式 timeout 让 learned 让位（ContextVar→_http_tier_budget）+ 教学句缩短
4. **R02-007** recall 按 URL 合并变体（recall.py）
5. **R02-001** watch 私网错误行指路（ops.py）

## 记录并接受

- R02-003a（401 不走 archive）：设计（docstring 在册）——工具描述修至与策略一致。
- R02-005（硬墙早停）：设计取舍；R02-004 的显式预算修复给了调用方真实的自救杠杆。
- R02-006（E1/E3/E8）：backlog 升级标注。
