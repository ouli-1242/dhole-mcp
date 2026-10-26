# P06 克莱尔 · 法语编辑 L2（编码与多语言轮）

## 任务
读一份只在 `<meta>` 里声明 iso-8859-1 的法语页；重音搜索；重音 CSV；重音 recall。

## 场景与读数
1. `latin1.html`：ok=true、111B，但正文是
   `Voici un texte fran�ais avec des accents : � � � � � �`——**每个重音都成了 U+FFFD**。
   页面自己的 `<meta charset="iso-8859-1">` 被忽略（HTTP 头没有 charset）。
2. `web_search "encodage français accents"`：2 条全 high，重音查询正常。
3. `doc_parse emoji_table.csv`：`café` / `très bien` 正确。
4. `web_recall "café français"`：0 行（CSV 是本地解析，不进网页召回缓存；查询词没有网页来源）。

## 问题
- **R16-001（B，已确认，修复推迟）**：meta-only 编码页被当 UTF-8 解码；重音变 U+FFFD，
  且 content_ok=true。用户拿到的「正文」不可引用。
- 修复推迟原因：解码路径（fetcher.py / crawl.py / trafilatura_extractor.py）正由另一位
  agent 编辑（11:38-11:50），按用户指示不动其文件；测试草稿与修法在
  `fixes/test-R16-F1-draft.py` 与 review.md。

## 评分
修复前 2.5/5（核心阅读乱码）；修复后应回到 5。
