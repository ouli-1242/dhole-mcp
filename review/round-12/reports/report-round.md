# round-12 轮报告 · 主题：链条协奏（多工具真实链条）

> 分支 review/round-12 · 基线 120a5d7 · 服务 127.0.0.1:9212（pid 28016）+ 靶站 8399 ·
> env DHOLE_IGNORE_ROBOTS=1（注明不可观测）。

## 体量

- **68 发真实调用**（p01-p10 主跑 61 + p05b 补条 5 + p05c 补条 2），**13/13 工具**，
  10 张全新画像（建筑师/证券交易员/潜水教练/口腔医生/评估师/高尔夫教练/殡葬策划/
  酒侍/文物修复师/电网调度员；L3×2、L1×3）。
- 每张卡 = 一条 2-4 颗工具的**真实任务链**，专盯跨工具接缝（本轮主题）。

## 十条链的对账结论

| 链 | 接缝 | 结论 |
|---|---|---|
| ①discover→read→parse→recall | 地图行→可点 | ✓ 行 URL 全真链；recall 已索引刚读页 |
| ②search→research→read | 三方各账 | ✓ sources+failed 两名单逐行带 why；timeout 双端具名 |
| ③guide→read→request→act | 写闸+会话 | ✓ confirm 教学句；「欢迎回来」session 交叉 |
| ④feed→parse→read | feed 三连账 | ✓ 3/8/负数具名拒（R11-001 真机回归） |
| ⑤act pin→回读→request | 页保持 | ✓ pin hit：填过的值跨调用活着（p05c 实锤） |
| ⑥search→recall→read | clamp 双向 | ✓ 99→20 出声；5 安静 |
| ⑦crawl→discover→read | 两把尺 | ✓ 各记各账；404 由补条实证（/work/999 是我押错行） |
| ⑧parse→feed→state | 混发分账 | ✓ 逐行 error 具名；GBK 保真；xlsx 双 sheet |
| ⑨链上中环第一拒 | 断点教学 | ✓ 11 发颗颗具名；断后链可续 |
| ⑩request→state→recall→HEAD | 写闸→复盘 | ✓ state 与全程对账；HEAD 免 confirm 实证 |

## 回归压力（round-11 必测 6 项全部真测）

1. feed 负数具名拒 / 0=all / 正数切片+note → P04 三连 PASS。
2. 写闸 confirm 形状（next_action 教）+ session 交叉 → P03/P05/P10 三线 PASS。
3. doc_parse 双码 + GBK 保真 → P08 PASS（not_a_pdf/编码两形 r11 已验，本轮 GBK+xlsx）。
4. feed 混发逐行分账 → P08 PASS（404/connection_refused 具名）。
5. 第一拒全集抽样 → P09 十一发 PASS（4 裸拒带码+出路、3 clamp 具名、3 unsupported 名单、1 家产形状）。
6. 前十轮家产合并回访 → r06 css_selector 0 匹配说出去（P09 S3，summary 内原句）、
   r08 pin 合同（p05c）、r09 recall clamp（P06）、r10 timeout 合同（P02）全部在场。

## 观察项（非缺陷，入册 common-issues）

- **auth_wall 判词**：web_act 对 /login 判 `auth_wall_detected`（内容判词），动作链照跑
  且 actions 行与判词分家（判词说内容、行说动作），next_action 教两扇真门
  （cred_defaults / 换源）。首次实锤，行为合理。
- **recall 空白 query**：「Missing required argument(s): ['query']」——空白按缺席，
  server.py:12855 通用门、三处 test 立规的家产形状。措辞可议，形状在册。

## 与往轮不同的收获

- 零 CONFIRM（继 r05、r10 后第三个零修复轮，非连续）。**候选清零的过程本身有价值**：
  两个「险些立案」的候选都被四账面字段/量具复查推翻（见 review.md R12-001/R12-002）——
  「读全 summary/error/next_action/fetch_hint」铁律在本轮又拦下两次误判。
- 脚本侧教训三枚（全记录、非缺陷）：discover 行就是 pages 本身（无 links 键）；
  doc_parse content 是字符串列表（web_read 是 dict.text）；sys_state 账在 message 键。
  p05b 复用 persona-05 目录覆盖了主跑回执（台账与运行日志保全）——补条脚本从
  round-13 起用独立 persona 号。
