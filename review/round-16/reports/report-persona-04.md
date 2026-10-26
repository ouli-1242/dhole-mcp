# P04 陈默 · 数据录入员 L1（编码与多语言轮）

## 任务
三份本地数据文件：GBK CSV（缺省与显式 encoding）、UTF-16 CSV；再查手册确认字段读法。

## 场景与读数
1. GBK CSV 缺省：69B，`北京/上海/晴/多云` 全部正确——自动探测生效。
2. GBK CSV `encoding="gbk"`：69B，同样正确（显式参数不破坏探测）。
3. UTF-16 CSV：37B，`产品/价格/咖啡` 正确。
4. `sys_guide topic="fields"`：返回手册原文（非 JSON 文本），内容存在。

## 问题
- 无 B 类。本地文件的编码面比网页面更稳（GBK/UTF-16/emoji/accent 全对）。
- 量具注：sys_guide 非 JSON 回执被 driver 截 600 字（harness，非产品）。

## 评分
任务完成 5/5。
