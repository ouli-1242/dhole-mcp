# P01 苏禾 · 中文编辑 L2（编码与多语言轮）

## 任务
中文搜索 → 用中文 URL 参数读靶站页 → CJK recall。

## 场景与读数
1. `web_search "缓存 失效 重新验证"`：3 条全 high（shenma,so360,baidu,sogou，rerank=neural），标题正文均为中文。
2. 中文 URL 参数 `/article?cb=中文参数`：首读 387B、再读 `cached=True` 且 `fetched_at` 相同。
3. `web_recall "中文 参数 页面"`：terms 全命中，4 行；含外部页（语义近邻），属 E6 在册的召回噪声。
4. sys_state：1 session、0 cookies。

## 问题
- 无 B 类。中文搜索、中文 URL、CJK recall 三面都成立。
- E6（在册）：recall 混入不相干页（「普通硅酸盐水泥」等）。

## 评分
任务完成 5/5。
