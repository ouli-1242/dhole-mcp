# 画像报告 · P01 阎立本（数据分析 · L2）· round-06

> 主题：structured 档 + css_selector 对照。4 发。p01- 前缀。

## 场景实录

- **S1 structured 档**：JSON 形（title/author/date/body）键值齐——交付形态对。
- **S2 命中 selector（候选 R06-002）**：`.card a` → **正好是我要的 11 行卡片标题（59 字符）**——却 `content_ok: false`、summary 头一句「no prose (menu fragments only)」。散文尺量的是**整页**，量到的是**用户点名要的碎片**：要的东西拿到了，回执说它不行。
- **S3 0 匹配 selector（候选 R06-001）**：`.no-such-class-xyz` → **整页 269 字符原样回来、content_ok: true、summary 一个字没提 selector 没匹配**。缩取参数被静默丢弃——比报错更糟的是它**看起来像成功了**。

## 摩擦日志

| 时刻 | 场景 | 口头反应 | 根因 | 严重度 |
|---|---|---|---|---|
| S3 | 「我筛的哪去了」 | 0 匹配静默回全页（R06-001） | 骂人级 |
| S2 | 「拿到了却说不行」 | 命中被散文尺误判（R06-002） | 骂人边缘 |

## 主观评分
- 3 / 4 / 3 / 3 / 4 / 总体 3.4（本轮最低：抽取面正是主题，两处都在刀口上）。

## 附录
证据：sessions/persona-01/ + p01-run.log。
