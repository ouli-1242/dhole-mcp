# 画像报告 · P09 甘棠（客户成功 · L1）· round-07

> 主题：手册与实况。5 发。p09- 前缀。报错→按报错自救全流程。

## 场景实录

- **S1 私网门先撞再自救**：第一发 web_read /article 吃了 **cache replay**（7 分钟前的拷贝）——「撞门」腿被共享缓存中和，门没被真撞到（**量具/环境注**：同 round-04 缓存教训家族，受控场景该 cache_ttl=0/变 URL）。自救腿：cred_defaults set allow_private → `?r=1&cache_ttl=0` 真抓 → identity_applied 在场。**合**。
- **S2 写闸 confirm 路**：无 confirm 的 place-order → 拒（闸=True）；补 confirm → ran。**合**。

## 摩擦日志
（无产品候选；「错误会教你」的承诺在自救腿成立。撞门腿的缓存中和如实记录为环境注，非缺陷。）

## 主观评分
- 4 / 5 / 5 / 总体 4.7（扣分在第一发的缓存串场，非产品账）。

## 附录
证据：sessions/persona-09/ + p09-run.log。
