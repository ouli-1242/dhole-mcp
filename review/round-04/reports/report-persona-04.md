# 画像报告 · P04 贺兰珠（HR 招聘 · L1）· round-04

> 主题：登录态保持 + 凭据不进参数 + 跨会话找回。7 发。p04- 前缀。

## 场景实录

- **S1 登录**：cred_defaults 存 host 作用域 basic 凭据（auth+host 一单存对）→ web_act login 三步 ok（confirm 闸过）。
- **S2 账户页**：/account 200、14B——登录态内页可读。
- **S3 存候选人页**：/article、/collections 双缓存回放。
- **S4 跨会话找回**：新 session web_recall「专题 推荐」→ **/collections 第一行、is_stale: false**（语义近的页排前——这次 L1 找回了）；sys_guide 正常（R01-009 保持）。

## 摩擦日志

（无——本轮最顺的画像）

## 放弃点

无。

## 主观评分

- 第一印象 4 / 速度 5 / 可信 5 / 报错可读 4 / 等待 5 / 总体 4.6。

## 改进清单

1. 【保持】auth+host 同单存、登录链、召回前排命中。
2. 【希望】「存过的页」在召回里提一句「你抓过」（E12 方向）。

## 附录

证据：sessions/persona-04/ + p04-run.log。
