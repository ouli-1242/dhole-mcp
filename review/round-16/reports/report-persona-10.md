# P10 艾米 · 多语言 QA L3（编码与多语言轮）

## 任务
混合页两读；crawl/discover 多语言站；emoji CSV；state。

## 场景与读数
1. `mixed.html`：两读一致，第二发 cached=True、时间戳相同。
2. crawl 多语言站 4 页：4/4 content_ok，page_type 全 fallback。
   **其中 gbk.html 的正文是乱码**（`'�������ҳ��…'`），仍被计为 content_ok=true。
3. discover：9 个 URL，6 行本次出门，plan 留 3 行（摘要如实）。
4. `doc_parse emoji_table.csv`：59B，😀 与 café 正确。
5. sys_state：2 sessions、1 cookie。

## 问题
- **R16-001（B）的另一面**：crawl 路径把 GBK 页解成乱码而不是占位句，且 content_ok=true；
  与 P06 的 Latin-1 乱码同根（meta-only charset 被忽略）。
- 发现/state 行为正常。

## 评分
修复前 3/5（crawl 把乱码页当成功内容）；修复后应回到 4.5。
