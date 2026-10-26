# round-13 计划（P0 封存）· 主题：重装链（长链条 × web_act 重交互面 × 预算压力）

> 分支 review/round-13 · 基线 22e9f88（round-12 收官）· 服务 127.0.0.1:9213 + 靶站 8399。
> r12 的链全部咬合，但链上的 web_act 还都是轻动作（snapshot/fill/read）。本轮每条链
> 都嵌一颗**重交互页**（dialog/tabs/iframe/upload/hoverdrag/dropdown/captcha/checkout
> 提交闸）——r08 深水轮是单工具打的，重面进**链**没试过：重动作的回执怎么接下一环、
> 弹窗账与下一发 read 怎么对、页签开关后 map 行还指不指得到。回归绑定 round-12
> 必测 6 项（pin 命中/css 0 匹配有账/中环第一拒/auth_wall 分家/feed 三连+混发/家产）。

## 十张画像卡（封存，执行前不改）

### P01 商执辔 · 地铁司机 L1
- 目标：链①guide→read /dropdown→act 下拉选择（select 动作）→state 复盘。
- 验收：L1 不迷路；下拉选中值在回执有账；state 与全程对上。
- 链：sys_guide + web_read + web_act + sys_state。

### P02 焦挺 · 消防员 L2
- 目标：链②discover→act /hoverdrag（hover 展开+drag 拖拽改写 #status）→read 复核状态文本。
- 验收：hover/drag 的回执行说清改了什么；read 读到改后值。
- 链：web_discover + web_act + web_read。

### P03 商无咎 · 法医 L3（猎手）
- 目标：链③read /dialog→act 先 arm 后 trigger（dialogs 台账行）→第二发读回执对账
  （弹窗文本与 arm 的 handler 是否互认）。
- 验收：dialogs 行逐条有账；误序（先 trigger 后 arm）的教学句在场。
- 链：web_read + web_act×2。

### P04 顾北望 · 天文台操作员 L2
- 目标：链④act /frame iframe 内点击→read 外层+内层两本账→recall 收尾。
- 验收：frame= 寻址不越层；r08 frame 落空具名回归（错名一发）。
- 链：web_act + web_read + web_recall。

### P05 米酒儿 · 京剧琴师 L1
- 目标：链⑤act /upload 上传（同 call submit——寿命句 r08 必测）→feed→state。
- 验收：上传回执 bytes 如实；跨 call 复用上传被拒的寿命句在场。
- 链：web_act + feed_read + sys_state。

### P06 乌恒 · 船闸管理员 L2
- 目标：链⑥request POST 写闸→act /challenge/text 验证码三试上限→pin 命中回读
  （r12 必测1：同 url+session+pin 站原页）。
- 验收：写闸教学；三试上限句；pin hit 实证。
- 链：web_request + web_act×2。

### P07 陆沉舟 · 隧道测量员 L2
- 目标：链⑦search→crawl 种子→discover 对账→read 落点（r12 必测3 中环第一拒抽 2 发）。
- 验收：两把尺对账；中环 clamp 落 summary/clamped。
- 链：web_search + web_crawl + web_discover + web_read。

### P08 温故 · 水质检验员 L1
- 目标：链⑧feed since 增量→parse 构造单表 xlsx→read 原文（r12 必测5 前半）。
- 验收：since 账可读；parse→read 接上；L1 零迷路。
- 链：feed_read + doc_parse + web_read。

### P09 常岁晏 · 灯光师 L3（猎手）
- 目标：链⑨tab 动作 new/list/切页签/close→跨页签 read→第一拒中环抽样 3 发
  （r12 必测3；tab 台账与 read 对账）。
- 验收：页签账逐条；关错页签的教学句；断后链可续。
- 链：web_act×3 + web_read + web_request(GET 拒)。

### P10 覃九鼎 · 拍卖师 L2
- 目标：链⑩read /checkout snapshot→act 表单填写+submit 闸（pending Approval 分家：
  动作行 ok、content_ok 判词）→confirm 真提交→state 复盘（r12 必测4）。
- 验收：闸前动作行逐条 ok；闸后 confirm 落账；state 收尾对账。
- 链：web_read + web_act×2 + sys_state。

## 覆盖核对

- 职业 10 全新（地铁司机/消防员/法医/天文台操作员/京剧琴师/船闸管理员/隧道测量员/
  水质检验员/灯光师/拍卖师——r01-r12 均未用）✓；L3×2（P03/P09）、L1×3（P01/P05/P08）✓。
- 13/13：read×6、act×8（dropdown/hoverdrag/dialog/frame/upload/captcha/tab/checkout
  八个重面）、request×2、search×1、research（P07 旁支）×1、recall×2、crawl×1、
  discover×2、feed×2、parse×2、state×3、guide×1、cred 打头 ✓。
- 回归绑定：round-12 必测 6 项全部嵌链（pin 命中 P06 / css 0 匹配 P04 / 中环第一拒
  P07+P09 / auth_wall 分家 P10（checkout 提交闸同族） / feed 三连+混发 P08 / 家产合并）。

## 执行参数

- 节流：画像间 ≥18s；链环间 5-8s。
- 端口：9213；env DHOLE_IGNORE_ROBOTS=1（注明不可观测）。
- 方法论：先引手册/描述原句 → 真机回放 → 记「合/不合」；判定前读全四账面字段
  （summary/error/next_action/fetch_hint）——三轮同根教训（量具截断）为铁律；
  补条脚本用独立 persona 号（r12 教训）。
