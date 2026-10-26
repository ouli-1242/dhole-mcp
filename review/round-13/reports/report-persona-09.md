# P09 常岁晏 · 灯光师 L3（猎手）—— 链条⑨tab 页签→跨调用账→中环第一拒 3 发

## 画像与卡面

灯光师，L3，会自己写脚本。情境：演出资料分在两个页签里，他想开一个新页签读标题、列页签、
切回、关掉；再顺手用几种错误参数试探第一拒；最后确认断了还能续。验收：页签账逐条；关错页签
的教学句；断后链可续。

## 读数（9 发：cred + act×4 + request/search/feed 3 连拒 + read）

| 发 | 工具·形状 | 结果 |
|---|---|---|
| S1 | act：`{"tab":{"open":"/article"}}` → read h1 | tab_opened=1，url=/article；h1=**本地抓取工具配置指南**；summary 尾巴说「redirected from / to /article」 |
| S2 | act：`{"tab":"list"}`（**新调用**） | tabs 只有 `[{index:0, url:"http://127.0.0.1:8399/"}]`——S1 开的页签不见了 |
| S3 | act：`{"tab":0}` → read h1 | switch ok；h1=**dhole 模拟测试靶站**（回到 /） |
| S4 | act：`{"tab":"close"}` → `{"tab":"list"}` | close **ok=false**，教学句：`refusing to close the only tab - the chain still needs a page to answer from; use {"tab": "new"} first`；list 仍 1 枚 |
| S5 | request GET / | `invalid_request: method=GET is a read, not a request - web_request takes POST / PUT / PATCH / DELETE` |
| S5 | search max_results=0 | `max_results=0 is outside the supported 1-50 range; returning at most 1`（summary 内） |
| S5 | feed_read `feeds=` | `Unsupported argument(s) for feed_read: ['feeds']. Supported: ['max_items', 'since', 'timeout', 'urls']` |
| S6 | read /work/1（断后） | 200 · 39B · cache（44.9 min 前抓，replay 句在场） |

## 合 · 不合

- **合**：页签动作本身在**一次调用内**是有效的（S1 开→读；S4 关唯一页签被拦下并教出路）；
  三连第一拒颗颗具名（GET 拒绝带出路、clamp 落 summary、unsupported 带全名单）；断后链可续。
- **不合（候选 C3）**：S1 开的页签在 S2（下一次调用）**消失**，`tab list` 只剩新页实例的
  0 号位。产品没有任何「页签只活这次调用」的寿命句——upload 有 lifetime 字段（r08 实修），
  tab 没有。用户视角：开好的页签下一条消息就没了，还以为自己开错。P3 需真机区分
  「per-call 页签」与「漏了 pin」，并查 sys_guide/源码是否已写明。

## 摩擦清单

1. **页签跨调用消失，无寿命句**（C3，本轮最像缺陷的一条）。
2. S1 的 summary 说「redirected from / to /article」——开页签却像整页跳转，措辞让 L3 也愣一下。
3. 中环第一拒都只落在 summary/error 文本里，L3 想脚本化解析要抠字符串（既有合同）。

## 判定

- tab 页签链「动作行 + 教学句」PASS；跨调用页签寿命 = 待审候选 C3。
- 第一拒 3 连发 PASS（r12 必测 3）；断后链续 PASS。

## 主观评分

4/5 —— 单条链很顺；「下一条消息页签没了」是这轮最让我怀疑产品的一条。
