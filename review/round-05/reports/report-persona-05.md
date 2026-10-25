# 画像报告 · P05 亢明玉（采购 · L2）· round-05

> 主题：非法参数第一拒。3 发。

## 场景实录

- **S1 force_fetcher='magic'** → 第一拒全名单：「force_fetcher='magic' is not a fetcher tier. Valid values: 'http', 'stealthy'.」
- **S2 未知参数 magic_param** → 「Unsupported argument(s) for web_read: ['magic_param']. Supported: [全名单]」——拼错一次就学到全部。
- **S3 stealthy 正形** → 200（liveness probe 自白在场）。

## 摩擦日志
（无——两处第一拒都是「教全形」的范本）

## 主观评分
- 4 / 5 / 5 / 5 / 5 / 总体 4.8。

## 附录
证据：sessions/persona-05/ + p05-run.log。
