# 画像报告 · P07 阿凯（安全研究员 · L3）· round-01

> 授权本地演练：摸安全门与写纪律。15 发（p07-*），挑战页 5 连试。

## 场景实录

- **S1 门禁盘点**：sys_state show=['identities'] → 未知 token 被拒并报出合法名单（cred_defaults）✓；/private/secret → **robots_disallowed**——靶站 robots.txt「Disallow: /private」，**进程级 DHOLE_IGNORE_ROBOTS=1 没有绕过它**（与 sohu 同形状）。而缓存回执又说「fetched under: bypassed (process-wide)」——**自述与行为矛盾**（候选 R01-005，P3 裁决：env 失效 or 点名优先级是设计）。robots 路径本轮「不可观测」的预设作废——它以另一种方式被观测到了。
- **S2 混淆壳页**：/shell/obfuscated → 「obfuscated_payload: the body is a packed script challenge, not prose (40 long hex run(s))」page_type=js_shell，9,017 字符但 content_ok=false——**形状识别拒绝，不装内容** ✓。untrusted_content 标记路径本靶站无注入页，未观测（注明）。
- **S3 抄写挑战 5 连试**（验证码尝试落 evals/.runtime/challenge.log）：
  1. snapshot {} → 拒，教「{"snapshot": true}」✓
  2. snapshot true → 拿到页面文本（token TGTQ2 在正文里）
  3. read #challenge-text → 选择器不存在；fill 必须 {selector,text}/{index,text}——**没有链内取值**
  4. 无 url 续操作 → 拒「url is required」——**驻留会话解法也被堵死**
  5. 真实结构重试（css_selector 摸到 43MNG）→ fill ✓ click ✓ **submitted: true, submission: POST** → 服务端 403「抄写不符」——机械链路全通，但值传不进去，注定不符。
  → **候选 R01-006：挑战页「同链 read→fill→submit」语义无解**（fill 静态、别名无处消费、跨调用必错）——除非手册/源码里有未被我找到的机制（P3 裁决）。
- **S4 写请求**：无 confirm → 「method=POST is a write: it changes server state, **and stored credentials for this host ride along automatically**」✓；confirm=true → 200「订单已提交…服务端已写入订单日志」✓（靶站 orders.log 落行，P6 归档）。sys_state 终盘：cache 83 条 hits 38/misses 46。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因 | 严重度 |
|---|---|---|---|---|---|
| 22:10 | S1 robots | 「IGNORE_ROBOTS 是装饰品？」 | 8s | env 与行为矛盾（候选 R01-005） | 骂人 |
| 22:12 | S3 挑战 | 「同链取值到底怎么写」 | 5 试 ≈1min | fill 无链内取值（候选 R01-006） | 骂人 |
| 22:12 | S3 无url续操作 | 「驻留会话呢」 | 8s | 设计：一次导航一 URL（与挑战页机制互斥） | 皱眉 |
| 22:14 | S4 | 顺 | 0 | - | 无感 |

## 放弃点

S3 挑战：**5 试后放弃**（机械全通、语义无门）——「这不是我的问题，是它俩（fill 静态 vs 每导航换码）八字不合」。

## 主观评分

- 第一印象 4 / 找答案速度 3（挑战页耗尽耐心预算）/ 答案可信度 4 / 报错可读性 5（每拒都教正确形状）/ 等待感受 4（web_act 5–23s 可感）/ 总体 4.0。

## 改进清单（按感知强度）

1. 【我想骂人→希望】DHOLE_IGNORE_ROBOTS 名副其实，或文档明说「点名条目不豁免」。
2. 【希望】web_act 链内取值（fill text 引用 read 别名），或挑战型任务给「copy: {from_read}」原语。
3. 【希望】403 失败页的「填进来的不是这一页的字符」已经很好——保持。

## 附录

证据：persona-07/02（robots 拒绝）、03（obfuscated_payload）、04/05（动作形状门）、09（snapshot 拿 token）、10（url required）、13/15（提交与 403）、06/07（写闸双态）。靶站旁证：evals/.runtime/challenge.log 5+ 行、orders.log +1 行。
