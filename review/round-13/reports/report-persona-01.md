# P01 商执辔 · 地铁司机 L1 —— 链条①guide→read→select→state

## 画像与卡面

地铁司机，L1，日常只会对着手机问 AI。情境：调度室里要在一张在线表里把「国家」改成中国，
不确定网页上的控件怎么点。目标：让 AI 替他在页面上完成这次选择，并确认真的选中了。
验收：下拉选中值在回执有账；state 与全程对上；L1 不迷路。

## 读数（4 发：cred + guide + read + act + state）

| 发 | 工具 | 读数 |
|---|---|---|
| S1 | sys_guide topic=start | 3781 字符、7 节的路由图，一页读完 |
| S2 | web_read /dropdown | 200 · 63B · **no_content**「no prose (menu fragments only)」——表单页读不出正文 |
| S3 | web_act select `#country`=CN → read @value | select ok=True；`read '#country' @value (property)` = **CN**（value=CN 与 label=中国 刻意不同，匹配到 value 这路） |
| S4 | sys_state | `1 session(s) open, holding 0 cookie(s)…Nothing erased.` |

## 合 · 不合

- **合**：S3 的 select 动作和读值在同一条链里闭合——动作行说 `select 'CN' in '#country'`
  ok，紧接着 property 读到 `CN`，L1 用户能看懂的「真的选上了」。
- **不合**：S2 读 /dropdown 只回 63B 碎片。页面上的三个选项（中国/日本/美国）一个字都没出现，
  用户只能凭常识猜 `CN` 再让工具去试。这是「表单页无正文」的老摩擦（诚实标签在，但用户仍拿不到选项清单）。

## 摩擦清单

1. 下拉页 read 无正文（63B menu fragments）——要看选项必须先 act，用户得猜 value。
2. sys_guide 一页 3781 字符，对 L1 偏长；但他只读了路由图第一屏就够用，未构成放弃。

## 判定

- 下拉选择链 PASS：动作行 + property 回读双账一致（r13 卡面验收 1/1）。
- 摩擦 1 入册（E 类候选，与 P03/P10 的「表单页无正文」同根，P3 合并审）。

## 主观评分

4/5 —— 事情办成了，中间那一步「先猜再验」像一个熟练工，而不是一个会问一句的助手。
