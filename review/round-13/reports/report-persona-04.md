# P04 顾北望 · 天文台操作员 L2 —— 链条④iframe 内点击/读账 + frame 错名回归

## 画像与卡面

天文台操作员，L2。情境：观测站的表单嵌在同源 iframe 里，外层没有可交互元素。
目标：让 AI 进 iframe 填备注、点保存、读回执；顺带验一次错名 frame 的报错。
验收：frame= 寻址不越层；r08「matched 0 <iframe> + 在场清单」回归。

## 读数（主跑 6 发 A 类全灭 + p14 补条 5 发）

主跑 p04（把 `frame` 写在顶层参数）：

| 发 | 工具·形状 | 结果 |
|---|---|---|
| S1-S3 | web_act 顶层 `frame=app` / 读回执 / 错名 `frame=#no-such` | **3 发全 invalid_request**：`Unsupported argument(s) for web_act: ['frame']` + 全支持名单；next_action 教「重命名或删掉」 |
| S4 | web_read /frame 外层 | 200 · 41B（外层只有一枚 iframe，无正文） |
| S5 | web_recall iframe 词 | 0 结果（0 也如实） |

补条 p14（同一画像，`frame` 写进每个 action 的 target）：

| 发 | 工具·形状 | 结果 |
|---|---|---|
| T1 | fill `#note` + click `#save`（各带 `frame:"app"`） | 两行 ok |
| T2 | read `#saved`（同 frame） | `not saved`——新调用是新页面实例，T1 的保存不跨调用 |
| T3 | read `#saved` 带错名 `frame:"#no-such"` | action 行具名错误：`frame '#no-such' matched 0 <iframe> element(s) in the top document - the iframes present: ['app -> http://127.0.0.1:8399/frame/inner']`（**r08 家产 PASS**） |
| T4 | web_recall iframe 词 | 0 结果（0 也如实） |

## 合 · 不合

- **合**：frame 正形（写 action target 里）寻址不越层，fill/click 都落在内层；错名报错把在场
  iframe 的 name→URL 清单点出来，用户不用二次侦察（r08 家产直接回访）。
- **不合**：
  1. **主跑 3 发全灭是 A 类**——我把 `frame` 当顶层参数。工具的支持名单与 next_action 都
     说得很清楚，没有误导；但这是我作为「用户」第一次会踩的坑，记进摩擦不记缺陷。
  2. T1 保存后 T2（新调用）读回 `not saved`。页面实例不跨调用是既有模型（上传寿命句同族），
     但 iframe 这一页的回执没有像 upload 那样的「lifetime」句，用户会以为保存丢了。

## 摩擦清单

1. frame 参数层级的第一次试错：3 发被拒（A 类；支持名单可读，产品无责）。
2. iframe 保存状态跨调用消失（T2），回执无寿命句提示；要看「已保存」必须在同一调用里读，
   或依赖 pin（p14 未用 pin）。

## 判定

- iframe 链 PASS（p14 正形实锤）；r08 frame 错名家产 PASS。
- 观察项（E 类候选）：iframe 页无 lifespan/寿命提示，与 upload 的 R08-002 家产不对称；
  但 p14 T2 的 `not saved` 是页面真实状态，不是错误回执。P3 定级。

## 主观评分

3.5/5 —— 正形一通到底；扣分在「保存不跨调用」的认知落差，以及我自己先踩的顶层坑。
