# P05 米酒儿 · 京剧琴师 L1 —— 链条⑤upload 寿命→跨调用复用→feed→state

## 画像与卡面

京剧琴师，L1，用 AI 帮忙整理曲谱。情境：要把一段工尺谱传给站上的表单；不确定上传完还能不能
下一条消息再点发送。目标：传上去、发出去、拿到回执。验收：上传回执 bytes 如实；跨 call 复用的
寿命句在场。

## 读数（主跑 5 发 + p16 补条 2 发）

| 发 | 工具·形状 | 结果 |
|---|---|---|
| S1 | act：upload `#doc` + click `#send`（同 call，无 confirm）+ read `#receipt` | upload ok，**lifetime 句**：`the files ride THIS page instance - submit within the same call (a later call starts a fresh page)`；click 被 pending_approval 闸（行内教 `confirm: true` 写在 click 上）；receipt=`nothing uploaded yet` |
| S2 | act：新调用再 upload（不发） | upload ok（新页面实例，寿命从头算） |
| S3 | feed_read 1 URL max_items=2 | 1 行 2 条，newest first，live 抓取 |
| S4 | sys_state | 1 session、0 cookie、Nothing erased |
| p16-S1 | act：upload → click `{"selector":"#send","confirm":true}` → read `#receipt` | click ok（`submitted: true, submission: POST`）；receipt=**received qinpu2.txt (63 bytes)** |

## 合 · 不合

- **合**：上传寿命句、提交闸、确认后真提交三件事都在同一条链上说清楚（r08 家产回访）。
  补条 p16 把「同 call 上传+确认提交」走通，回执落在页面上。
- **不合 / 账差（环境侧）**：qinpu2.txt 磁盘 **65 字节**（内容结尾 `\r\n`），页面回执写
  **63 bytes**。差额 2 来自本地靶站的多段解析启发式（evals/fixture_site.py:847-850 的
  `size = len(payload) - 2`，把文件自身的结尾 CRLF 吃掉），dhole 只是如实转述页面文字。
  记为**靶站侧量具差**，不记 dhole 缺陷。

## 摩擦清单

1. 上传后跨调用发送会丢（寿命句在，但 L1 用户未必逐字读 metadata）；同一调用里 upload+send 一条链
   才能成（p16 实证）。这是设计，但对 L1 是真实台阶。
2. 写闸在 click 上，confirm 必须嵌在 action 里（p16 才通）；顶层 confirm 会被拒（p10 同坑）。
3. 回执 bytes 与本地文件差 2（靶站解析差，非 dhole）。

## 判定

- upload 链 PASS（r13 卡面验收 2/2：寿命句在场；确认后回执真落页面）。
- 观察项：靶站 bytes 差 2（环境侧，写 env-log）。
- 顶层 confirm 的形状问题与 P10 合并审（C1）。

## 主观评分

4/5 —— 功能全对；L1 的真实台阶在「上传和发送要一口气说完」。
