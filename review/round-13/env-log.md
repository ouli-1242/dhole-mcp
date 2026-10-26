# round-13 环境日志（P0/P1）

> 服务 127.0.0.1:9213（dhole）+ 127.0.0.1:8399（靶站）· env DHOLE_IGNORE_ROBOTS=1
> · DHOLE_USAGE_LOG=1 · 靶站证据目录 evals/.runtime/。

## 断点重起（接手时）

- 交接时点（round-13 P1 8/10 卡）后进程被回收：`netstat` 9213/8399 均无监听。
- 2026-10-26T08:48:53+08:00 `ensure_up.ps1` 重起：fixture-8399 pid **19576**，
  dhole-9213 pid **16464**（原 17756/18636 已不在）。server-restarts.log 有两行自动记录。
- 起后自测：ping ok（serverInfo Dhole v17.2）+ tools/list=**13**。
- 真重启后进程内 cred_defaults 清零：P1 续跑脚本均自带
  `{"set": {"allow_private": True}}` 打头（台账 cred_defaults 15 发可查）。

## 靶站日志偏移（P1 侧证基准）

| 日志 | P1 续跑前 | 续跑后 | 增量来源 |
|---|---|---|---|
| evals/.runtime/orders.log | 6 行 | 7 行 | p18 checkout 真提交（qty=2，2026-10-26T00:53:18Z） |
| evals/.runtime/challenge.log | 1 行 | 2 行 | p17 captcha 真提交（answer=RPKE6，ok=true，2026-10-26T00:52:47Z） |
| evals/.runtime/requests.log | （未计偏移） | — | web_request P06 两次 POST /login |

## 网络

- P19 research 真实外网 2/2 源读入（github.com/modelcontextprotocol），无失败源；
  P09 search 1 结果（baidu,sogou,yandex）——外网通，未见需等待的环境阻塞。

## 备注

- 本轮 DHOLE_IGNORE_ROBOTS=1：robots 门不可观测（报告注明）。
- 9213 原 pid 17756 的会话 cookie 已随进程消失；P1 续跑用新 session（正常现象）。
