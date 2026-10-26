# round-16 轮报告（编码与多语言）

> 主题：十个职业在非 ASCII 世界里的读、搜、解析。10 画像 + 补条 · **70 发** · 13/13 工具。
> 端口 9216 + 靶站 8399 + 自建多语言站 8398 · 基线 6f262fa。
> 工作树含另一位 agent 的未提交改动；本轮只读不写其文件。

## 结论

- **1 CONFIRM（B）：R16-001** —— 只在 `<meta>` 里声明编码的页面（HTTP 头无 charset）被当 UTF-8：
  - GBK 页：`web_read` 返回 `[Binary content detected …]` 占位句且 content_ok=true；
    `web_crawl` 则直接是乱码 `'���…'`，同样 content_ok=true。
  - Latin-1 页：重音全部变 U+FFFD（`fran�ais`），content_ok=true。
  - **修复推迟**：解码路径正由另一位 agent 编辑（fetcher/crawl/trafilatura_extractor，
    11:38-11:50 注释翻译在途）；按用户指示不动其文件。测试草稿与修法已备好
    （`fixes/test-R16-F1-draft.py`），其落地后即可接线。
- 多语言正向实锤：
  1. UTF-8 正文保真：阿拉伯文 / 日文 / 韩文 / 西里尔 / 中文 / emoji 全对（P02/03/07/08/09）。
  2. 非 ASCII URL 参数可作缓存键：重复读命中且时间戳一致（P01/02/05/07/09）。
  3. 非 ASCII 表单值：阿拉伯语用户名登录成功（P02），web_act 填表读回一致（P11）。
  4. 本地文件编码：GBK（缺省与显式）、UTF-16、emoji/accent CSV 全保真（P04/P06/P10）。
  5. CJK recall：中文/繁体/emoji 查询都能召回（P01/05/09）。
  6. 多语言搜索：中/日/韩/俄/法查询均返回 high 结果，rerank=neural（P01/03/06/07/08）。

## 问题清单（P3 后）

| # | 级别 | 现象 | 去向 |
|---|---|---|---|
| R16-001 | **B** | meta-only 编码页被当 UTF-8：GBK 占位/乱码、Latin-1 U+FFFD，content_ok 仍 true | CONFIRM，修复推迟（并发编辑）|
| R16-002 | E | emoji ZWJ(U+200D)/零宽空格(U+200B)被解析器丢弃；组合字符归一为预组合 | backlog |
| R16-003 | E6 | recall 混入不相干外部页；繁简不互通 | backlog（既有）|
| R16-004 | 量具 | 同 URL 跨画像复用导致「首读 cached=True」；脚本码点断言双层转义 | 记录（已修脚本）|
| R16-005 | A | recall 查本地 CSV 词汇 0 行（doc_parse 不入网页召回缓存） | 用法 |

## 评分

P01 5 · P02 5 · P03 4.5 · P04 5 · P05 4.5 · P06 2.5（待修） · P07 5 · P08 5 · P09 4.5 · P10 3（待修）。
