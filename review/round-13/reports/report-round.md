# round-13 轮报告 · 主题：重装链（长链条 × web_act 重交互面 × 预算压力）

> 分支 review/round-13 · 基线 22e9f88 · 服务 127.0.0.1:9213（断点重起后 pid 16464）+
> 靶站 8399（pid 19576）· env DHOLE_IGNORE_ROBOTS=1（robots 门本轮不可观测）。
> 交接点：P1 完成 8/10 卡；本轮接手补齐 p09/p10 + 6 个补条 + 3 组 P3 探针。

## 体量

- **95 发真实调用**：10 张卡主跑 **57** + P1 补条 **17**（p14-p19）+ P3 探针 **21**（p20-p22）。
- **13/13 工具**真实调用；10 张全新画像（地铁司机/消防员/法医/天文台操作员/京剧琴师/
  船闸管理员/隧道测量员/水质检验员/灯光师/拍卖师；L3×2、L1×3）。
- 每条主力链都嵌一颗重交互页：dropdown / hover+drag / dialog×2 / iframe×2 / upload×2 /
  request 写闸 + captcha + pin / crawl+discover+research / feed+parse / tab×4 / checkout 提交闸×2。

## 覆盖矩阵（主跑 + P1 补条；P3 探针单列）

| 卡 | 画像 | 主跑发 | 工具链 | 补条 |
|---|---|---|---|---|
| p01 | 地铁司机 L1 | 5 | guide→read→act(select)→state | — |
| p02 | 消防员 L2 | 5 | discover→act(hover,drag)→read | — |
| p03 | 法医 L3 | 5 | read→act(dialog conf)→act(误序) | p15 prompt 正形 |
| p04 | 天文台 L2 | 6 | act(frame 错层×3)→read→recall | p14 iframe 正形 + 错名回归 |
| p05 | 京剧琴师 L1 | 5 | act(upload+闸)→feed→state | p16 upload+confirm 真提交 |
| p06 | 船闸 L2 | 5 | request 写闸→act(captcha+pin)→pin 回读 | p17 captcha+confirm 真提交 |
| p07 | 隧道测量员 L2 | 7 | search→crawl×2→search→discover→read | p19 research 证据包 |
| p08 | 水质检验员 L1 | 5 | feed(since)→feed(混发)→parse(xlsx)→read | p20 feed 三连 |
| p09 | 灯光师 L3 | 9 | act(tab×4)→request GET 拒→search clamp→feed 错参→read | — |
| p10 | 拍卖师 L2 | 5 | read→act(submit 闸)→act(顶层 confirm 错)→state | p18 action 级 confirm 真提交 |

P3 探针：p20（css 0 匹配 / auth_wall 分家 / feed 三连 / recall clamp，8 发）、
p21（css 缓存对照 / tab 寿命+pin / dialog answer，9 发）、
p22（C6 写→回放对照 / C7 复现，4 发）。

## 十条重装链的对账结论

| 链 | 重面 | 结论 |
|---|---|---|
| ① guide→read→select→state | dropdown | ✓ select 动作 + property 回读 CN 双账一致 |
| ② discover→hover/drag→read | hoverdrag | ✓ HOVERED / DROPPED 页面自证 |
| ③ dialogs arm→trigger→对账 | dialog | ✓ 台账 accepted(armed)；误序 CANCELLED + auto-dismissed note；prompt 正形 p15 |
| ④ iframe 寻址 + 错名 | frame | ✓ action 内 frame 正形全通；错名 matched 0 + iframes present（r08 家产） |
| ⑤ upload 寿命 + 提交闸 | upload | ✓ lifetime 句在场；p16 confirm 真提交 receipt 落页 |
| ⑥ request 写闸 + captcha + pin | 写闸/pin | ✓ confirm 教学；pin hit 同题 2VR7A；p17 真提交 h1=抄写正确 |
| ⑦ search/crawl/discover 两把尺 | 爬取 | ✓ clamp 落 summary；discover work=3=items；research 2/2 源 |
| ⑧ feed since + 混发 + parse | feed/parse | ✓ since 计数自洽；坏源逐行 404；xlsx 单表可读 |
| ⑨ tab new/list/switch/close | 页签 | ✓ 同调用内两页签；关唯一页签教学句；跨调用页签随新页实例消失（见 C3） |
| ⑩ checkout 提交闸 + 真提交 | 提交闸 | ✓ 顶层 confirm 被拒（C1）；p18 action 级 confirm → 订单日志 +1 |

## round-12 必测 6 项 + 家产回访

| # | 回归点 | 结果 |
|---|---|---|
| 1 | pin 命中：同 url+session+pin 站原页 | **PASS** p06 S4（同题 2VR7A + pin hit 句）；p21 B3 复证无 session_id 时 pin 教出路 |
| 2 | css_selector 0 匹配要出账 | **live PASS / cache FAIL**：p22 C6a live 有「matched 0 elements」；p22 C6b 同 URL 回放丢句 → **C6 确认** |
| 3 | 中环第一拒（裸拒/clamp/unsupported） | **PASS** p07 crawl/search clamp、p09 GET 拒 + search clamp + feed 名单 |
| 4 | auth_wall 判词与动作分家 | **PASS** p20 S2：content_ok=false/诊断 auth_wall，fill+read property 行均 ok |
| 5 | feed 三连（负数/0=all/正数）+ 混发 | **PASS** p20 S3（-3 具名拒、0→8 条、2→2 条）+ p08 S2 混发 404 分账 |
| 6 | 前十一轮家产合并 | **PASS** r08 frame（p14 T3）、r09 recall clamp（p20 S4：limit 21→20 具名）、r11 feed 负数（p20 S3）、r06 css 0（p22 C6a）全部真机在场 |

## 按用户感知强度排序的摩擦清单

1. **C7（P3 探针）**：用户点名 `css_selector="h1"`，只拿到 10 字，回执判
   `extraction_collapsed`（「reader lost them」）+ content_ok=false。用户要的就是这一块，
   工具却说抽取器丢了字——**误报**，还会给出错误的恢复建议。B 类。
2. **C6（P3 探针）**：同一 URL+selector 的第二次读命中缓存，「matched 0 elements」这句
   消失，整页照回——R06-001 的诚实句在缓存路径上失守。B 类。
3. **C1（p10）**：submit 闸的 `next_action` 字面像顶层 `{"confirm": true}`，照做第二次仍被闸；
   正形只在动作行 error 里（action 内 confirm）。E 类。
4. **C2（p03）**：dialog 错误文案第二种形状写 `{"answer": ..., "prompt_text"?: ...}`，
   实测 answer 只收 accept/dismiss；p15 才一次通。E 类。
5. **C3（p09）**：S1 开的页签在下一调用消失，无 lifetime 句；pin 讲解只在真的用了 pin 时出现，
   而 pin 还需具名 session_id。E/D。
6. **表单页无正文**（p01 /dropdown、p03 /dialog、p10 /checkout）：read 只回 15-63B
   碎片，「看选项/看表单」必须 act snapshot。诚实标签在，但用户拿不到结构。E（既有）。
7. **上传寿命**（p05）：upload 与 send 必须同一调用；跨调用页面重来。寿命句在 metadata，
   L1 容易漏读。D/E。
8. **iframe 保存不跨调用**（p14 T2）：`not saved` 是真实页面值，但没有 upload 那样的
   lifetime 句提示。E/D。
9. **captcha 后读不存在的 #status**（p06 S3）：动作行 10s 超时（A 类脚本假想），
   产品如实报「selector matched no element」。
10. **回执 bytes 与磁盘差 2**（p16）：靶站 multipart 解析把文件自身结尾 CRLF 吃掉
    （evals/fixture_site.py:847-850），dhole 只是转述。环境侧。

## 放弃点与返工

- p04 主跑：3 发顶层 `frame` 全拒后放弃这条路线，改 read/recall；p14 用正形补回。
  属于 A 类（参数层级），不记产品缺陷。
- p10 S3：顶层 confirm 被拒后没有第三次尝试，改由 p18 用正形完成；C1 记为 E。
- p06 S3：读 `#status` 超时后放弃读状态，p17 改读 h1 拿到「抄写正确」。

## 主观评分

| 卡 | p01 | p02 | p03 | p04 | p05 | p06 | p07 | p08 | p09 | p10 |
|---|---|---|---|---|---|---|---|---|---|---|
| 分 | 4 | 5 | 4 | 3.5 | 4 | 4.5 | 5 | 5 | 4 | 3.5 |

均值 **4.15/5**。功能正确性高（十条链全通），扣分集中在「账的可见性」：
缓存回放、next_action 深层形状、页签寿命、表单页结构。

## 能力边界遭遇

- **页面实例不跨调用**（无 pin）：upload、iframe、tab 全部遵循同一模型；pin 是唯一
  「留住」机制，且需要具名 `session_id`（p21 B3 由产品自己教）。
- **缓存按 URL+extraction_type+selector 分键**：回放带着抓取时刻与质量判决，但
  **参数注记（selector 0 匹配）没有随行**（C6）。
- **显式收窄 ≠ 整页**：substance 尺已豁免（R06-F2），但 extraction_collapsed 判决没有（C7）。
- 表单页/菜单碎片页：read 无正文；要结构得 act snapshot。

## 与「工具视角」的差异

工具视角会说「十条链全绿、回归全过」。用户视角会补三句：绿的回执里，**缓存那一次少了
一句道歉**（C6）；**点名要一块却被告知抽取器坏了**（C7）；**下一条消息页签没了**（C3）。
前两句是账的真缺陷，第三句是模型未讲透的寿命。

## 改进清单（按感知强度）

1. C7：显式 css_selector 在场时，extraction_collapsed 判决要豁免（或改用它点名的块当分母）。
2. C6：缓存写入侧把 selector 注记存进 envelope，回放时原样带出（与质量判决同库）。
3. C1：submit 闸的 next_action 直接给 action 内 confirm 的嵌套示例，别只说 `{"confirm": true}`。
4. C2：dialog 错误文案把 answer 限定说死（accept/dismiss），prompt 文本点名 prompt_text。
5. C3：tab 动作的回执补一句「页签只活本调用，跨调用用 pin + session_id」。
6. 表单页：考虑在 read 的 next_action 里直接给 `{"snapshot": true}` 的 act 形状。
7. 靶站 bytes 差 2（环境，不入产品队列）。

## §18 收尾自检

1. **偷懒走最优路径？** 没有：p04/p10 都先按「自然写法」踩坑再补正形，错路留在台账里。
2. **只重结果忽略过程？** 没有：门、重试、10s 超时、两次被闸都进摩擦清单。
3. **10 画像都发了调用？13/13？** 是（95 发、19 个 persona 目录、13 工具全）。
4. **摩擦 ≥10 条？** 10 条（含 2 条 B、3 条 E、A 类与环境各列）。
5. **归因分清？** 分 A/B/C/D/E 五层写在各卡与 review.md；靶站 bytes 归 C，未记 dhole。
6. **审查真推翻过结论吗？** 有：C1/C2/C3/C4 均被审查降级或判设计；C5 被归环境。
7. **干净是真干净？** 不宣称干净：2 个 B 类 CONFIRM（C6/C7）进修复队列。
8. **分支与产物？** review/round-13 独立分支；原始回执在 sessions/；台账 95 行可追。
