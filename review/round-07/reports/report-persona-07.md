# 画像报告 · P07 越千帆（QA · L3 猎手）· round-07

> 主题：手册与实况。6 发。p07- 前缀。错误消息「合法值名单」逐个验证。

## 场景实录

- **S1 名单内真收 / 名单外真拒**：
  - `force_fetcher=http` 打 JS 壳页 → `js_shell_detected: page requires JavaScript rendering…`（具名，不是含糊失败）；同页 `force_fetcher=stealthy` → 200 OK。**合**。
  - `force_fetcher='curl'` → `invalid_request: … Valid values: 'http', 'stealthy'.` **合**。
  - `focus_mode=filter` → 200 OK（名单内真收）。**合**。
- **S2 extraction_type='bogus'** → 全名单第一拒句。**合**。

## 摩擦日志
（无候选：名单与门一一对应，没有多一个也没有少一个。）

## 主观评分
- 5 / 5 / 5 / 5 / 总体 5。

## 附录
证据：sessions/persona-07/ + p07-run.log。
