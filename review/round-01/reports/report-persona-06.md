# 画像报告 · P06 小美（电商运营 · L2 急躁）· round-01

> 大促前一周：读报表、批量读 12 个商品页、扫二维码。8 发（p06-*）。

## 场景实录

- **S1 报表**：doc_parse sample.xlsx → markdown 表（Name/Score/Alice/Bob）0.3s。「这表直接能贴周报。」
- **S2 批量读**：带 max_content_chars_per（我在 crawl 上记错了参数名）→ 具名拒绝+全名单；**改 max_content_chars 重发 → 12 行 results 全回**（0.4s），行行带 url/content_ok/chars。「12 个页面一发拿齐，这个爽。」
- **S3 手滑**：url=123 → 「url must be a string, got int (123). Pass url as text.」；anti_detect='超级隐身' → 「must be one of off, basic, auto, aggressive … The rungs go weakest to strongest; there is no fifth, and no single fingerprint knob to override.」——**连'别想第五档'都提前答了**。
- **S4 二维码**：/qr 页 → 5 字「签到二维码」，content_ok=false 如实说没正文；/qr.png → 走了 OCR 档，**image_ocr_thin：整图只认出 1 个字「回」**——「二维码又不是给人读的字」。放弃（「让人扫码吧」）。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 等/卡 | 根因 | 严重度 |
|---|---|---|---|---|---|
| 22:07 | S2 参数名 | 「_per 到底是谁的参数」 | 10s | crawl 与 read 的每页钳制参数名不一致（E） | 骂人边缘 |
| 22:08 | S4 QR | 「扫不出码」 | 5s | 无 QR 解码能力（OCR 只管字）（E，诚实拒绝不硬编） | 皱眉 |
| 22:08 | S4 页面 5 字 | 「就一行标题」 | 0 | 页面本来没正文（D） | 无感 |

## 放弃点

S4 QR：**放弃**（能力边界，拒绝诚实）。

## 主观评分

- 第一印象 5（报错全像贴心同事）/ 找答案速度 4 / 答案可信度 5 / 报错可读性 5 / 等待感受 5 / 总体 4.6。

## 改进清单

1. 【希望】web_read 支持每页独立钳制（或文档里把 crawl 的 _per 与 read 的全局钳制差异写醒目）。
2. 【希望】/qr 这类图片页：识别出 QR 码给内容或明确「QR 解码不在能力内，试试 xxx」。
3. 【希望】批量 12 行的 summary 给「12 of 12 ok」式总览（这次要自己数）。

## 附录

证据：persona-06/01（xlsx 表）、02（_per 拒绝）、03/04（类型与枚举拒绝教科书）、05（QR 页 5 字）、06（OCR thin）、07（批量 12 行）。
