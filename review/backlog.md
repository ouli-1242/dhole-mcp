# E 类 UX 缺口（不进修复队列，按用户感知强度排序）

> 种子：manual-2026-10-25。轮次列记录「已观察轮次」。

| # | 现象 | 场景 | 感知强度 | 已观察轮次 |
|---|---|---|---|---|
| E1 | 批量读行键 results 与直觉 pages 不一致，调用方脚本首写即错 | 批量读 | 皱眉 | manual-2026-10-25 |
| E2 | search/recall 同 URL 多变体不去重；recall 两行 stale 标注打架 | 搜索/召回 | 皱眉 | round-01（R01-004） |
| E3 | offset 模式 summary/total_extracted_chars 说整页不说本片 | 长文续取 | 骂人边缘 | round-01（R01-011） |
| E4 | crawl 升级 stealthy 浏览器时回执不点名（sys_state 里才发现） | 爬取 | 皱眉 | round-01（R01-012） |
| E5 | timeout 钳制消息展示剩余秒数（89→30）而非请求值 | PDF/大页 | 皱眉 | round-01（R01-013） |
| E6 | recall 低分命中（0.06 / 1/3 词）出榜，不如说「没找到」 | 召回 | 皱眉 | round-01（R01-014） |
| E7 | robots 缓存 metadata 的 fetched-under 标注来自铸造时刻，跨进程读易误判当前进程行为 | robots | 皱眉 | round-01（R01-005 旁证） |
| E8 | since 只收 ISO 日期不收「3d」档位词（报错有示例，自愈成本低） | feed 增量 | 无感 | manual + round-01 复现 |
