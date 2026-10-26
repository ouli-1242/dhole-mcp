# P08 伊万 · 俄语分析师 L3（编码与多语言轮）

## 任务
西里尔页、西里尔搜索、西里尔 research、复读缓存。

## 场景与读数
1. `ru.html`：122B，西里尔（U+0400–U+04FF）完整。
2. `web_search "кодировка текст проверка"`：2 条（1 high, 1 med；so360,yandex,sogou），标题俄文正确。
3. `web_research "кодировка UTF-8 проверка"`：1/1 source，6.1s，证据包生成。
4. 复读：`cached=True`、122B 一致。

## 问题
- 无 B 类。西里尔在 read/search/research 三条路都保真。
- 量具注：脚本的 has_cyrillic 断言同样双层转义，回执复核为 True，脚本已修。

## 评分
任务完成 5/5。
