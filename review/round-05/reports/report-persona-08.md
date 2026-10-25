# 画像报告 · P08 傅筛（安全研究 · L3 猎手）· round-05

> 主题：私网门全谱 + 怪值。9 发。

## 场景实录

- **S1 谱**：169.254.169.254 → **「internal cloud metadata endpoint」单独具名（永不放行）**；10.0.0.1 → 「(10.0.0.1 in 10.0.0.0/8)」段名精确；[::1] → network_error（fixture 只听 IPv4——诚实）。
- **S2 怪值**：allow_private 传 0 / "" / [] → 全拒：「pass true (loopback only) or a list of host names — cloud metadata e…」（形状句完整）。
- **S3 豁免后 [::1]** → TCP preflight 超时（OS 层如实；非门的错）。
- **sys_state** 正常。

## 摩擦日志
（门的谱系与话术全部对得上）

## 主观评分
- 4 / 5 / 5 / 5 / 5 / 总体 4.8。

## 附录
证据：sessions/persona-08/ + p08-run.log。
