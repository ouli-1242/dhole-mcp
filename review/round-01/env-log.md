# round-01 环境日志

## P0 探测（2026-10-25T21:24–21:30+08:00）

| 项 | 实测 |
|---|---|
| dhole | v17.2 editable，capabilities 全 ✓，Chrome 系统浏览器 |
| 服务 | 127.0.0.1:9201（DHOLE_IGNORE_ROBOTS=1, DHOLE_USAGE_LOG=1），后台 job pwsh-2c3dfef136c84322 |
| 靶站 | 127.0.0.1:8399（evals/fixture_site.py），后台 job pwsh-87071b6c85304138 |
| harness ping | serverInfo Dhole 17.2，negotiated 2025-03-26 ✓ |
| tools/list | 13 颗 ✓ |
| harness 自测 search | "python asyncio tutorial" 3 行真实结果，degraded_pool 如实（2 of 6 / 1 of 6） |
| engines probe | 5/12 可用：baidu/yahoo/sogou/yandex/seznam ok；bing/bing_global timeout；ddg/brave/so360 cooling（20–23s）；shenma blocked(circuit)；marginalia empty |
| fake-ip | 合取成立（example.com→198.18.0.106, example.org→198.18.0.107 同入 198.18.0.0/15）→ 公网不会被误判私网 |

## 靶站服务端旁证基线（P6 归档增量）

| 日志 | P0 偏移 |
|---|---|
| review/.runtime/fixture-8399.log | 4 行（启动横幅） |
| evals/.runtime/orders.log | 不存在（0） |
| evals/.runtime/captcha.log | 不存在（0） |
| evals/.runtime/challenge.log | 不存在（0） |

## 过程记录

- 2026-10-25T21:47 dhole-9201 进程消失：日志无 traceback、无错误行，最后一个请求（p01b 21:37）正常 200；job_list 已空 → 判定：goal-round 边界回收后台 job 连带杀进程（环境/宿主行为，猜测；若再现于请求在途时改判稳定性候选）。靶站同命运。处置：新增 ensure_up.ps1（Start-Process 脱离 job 体系拉起，env/端口与宪章一致），本轮起每个画像脚本前预检；重启事件记 server-restarts.log + 此处。
- 2026-10-25T21:49 两服务由 ensure_up.ps1 拉起（9201=1 8399=1）。注意：dhole 重启 = cred_defaults 内存态清零，私网门恢复默认 → 后续画像首发靶站会再撞门（真人多用户长时窗下的真实体验，各画像如实记录）。
- （追加）

## P3–P5 过程记录（追加）

- 2026-10-25T22:41 两服务再次消失（P1 收尾后某刻；非请求在途）→ ensure_up.ps1 拉起。这是「goal 边界回收后台 job」环境行为的第 2 次复现（上次 21:47）。
- 2026-10-25T22:41–22:45 driver「拿不到 session」假象破案：探测用了裸 Client()（无 initialize）——非服务/工具问题。
- **R01-005 破案**：ensure_up.ps1 双引号串里的 `$env:DHOLE_IGNORE_ROBOTS='1'` 在外层被展开成空 → 21:49/22:41 两次拉起的进程 env 从未生效。P03/P07 的 robots 拒 = env 缺席时产品正确行为；「bypassed (process-wide)」缓存自白 = 21:24 旧进程（env 真设过）铸造的磁盘缓存 metadata。已修（顶层赋值）。
- 2026-10-25T23:0x P5 真重启（kill + ensure_up 修复版）→ 复验 8 项全 FIXED。
- 靶站日志偏移（P6 归档用）：fixture-8399.log 基线 4 行；orders.log +1（P07 checkout）；challenge.log +8（P07 5 发 + P3 补测 2 发 + P5 复验 1 发，其中 3 发 ok:true）。
