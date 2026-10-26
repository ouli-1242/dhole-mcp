# P04 韩冬 · 供应链计划员 L2 —— 三供应商批量读 → 回访 → 写闸

## 读数（5 发：cred + read×2 + request×2）

| 发 | 工具·形状 | 读数 |
|---|---|---|
| S1 | read urls=3（/work/1..3?cb=） | rows=3，逐行 content_ok=true、39/36/36 字 |
| S2 | 同三 URL 再读 | 逐行 cached=true，三行 `fetched_at` 同为 03:10:51Z |
| S3 | request POST /checkout（无 confirm） | `invalid_request: method=POST is a write…`；next_action 教 confirm=true |
| S4 | 同 call + confirm=true | 200 · 31B；靶站 orders.log **+1 行**（item=供应商变更记录 qty=1 @03:11:08Z） |

## 合 · 不合

- **合**：批量读的逐行缓存账真的逐行——三行同 URL 前缀不同 `?cb=`，第二读三行全 cached
  且时间戳一致，没有「一行缓存一行重抓」的糊账。写闸先拒后确认，服务端日志独立佐证。
- **不合**：无实质缺陷。批量 summary 用「answered without an HTTP error (`successful`
  counts exactly that…)」这种解释句，对 L2 略绕，但信息完整。

## 摩擦清单

1. 批量 summary 的 `successful` 定义句偏绕（无感）。
2. 写闸的 next_action 只说 `confirm=true`（对 web_request 是顶层参数，正确；与 web_act
   submit 的 action 级 confirm 不同——两种工具两种形状，跨工具学习成本）。

## 判定

- 批量读逐行账 PASS；写闸 + confirm 真写 PASS（服务端旁证）。
- r13 提交闸回归（submit/click 的 action 级 confirm）在 P07/P13 另行覆盖。

## 主观评分

5/5 —— 三行三账、写没写成看日志，全对得上。
