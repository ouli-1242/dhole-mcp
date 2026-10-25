# 画像报告 · P06 秋原野（摄影师 · L1）· round-05

> 主题：边界值钳制申报。5 发。

## 场景实录

- **S1 max_content_chars=-1** → 「clamped -1->500 (supported range 500-200000)」——钳了且申报（不静默改）。
- **S2 offset=99999** → 内容「[No more content.]」+ next_offset 无——越过页尾有具名哨兵，不是空手哑回。
- **S3 上限 200000** → 正常交回。
- **S4 login 链** → 三步 ok。

## 摩擦日志
（无）

## 主观评分
- 4 / 5 / 5 / 5 / 5 / 总体 4.8。

## 附录
证据：sessions/persona-06/ + p06-run.log。
