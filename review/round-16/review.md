# round-16 对抗审查（审查员帽）

> 输入：10 份画像报告 + 轮报告 + 70 发原始回执。审查阶段读源码/测试。
> **并发现场**：另一位 agent 正在把 src 注释翻成中文（fetcher.py 11:50、crawl.py 11:43、
> trafilatura_extractor.py 11:38；`git diff` 为注释/文案改动）。本轮只读，不写其文件。

## 候选与逐条攻击

### R16-001 · 只在 <meta> 声明编码的页面被当 UTF-8（B）
- **证据**：
  - `gbk.html`（GBK 字节，HTTP 头裸 `text/html`，页面 `<meta charset=gbk>`）：
    `web_read` → content=`[Binary content detected …]`、content_ok=true、96 visible chars；
    `web_crawl` → 正文 `'�������ҳ��…'` 乱码、content_ok=true。
  - `latin1.html`（Latin-1 字节，meta iso-8859-1）：`web_read` → `fran�ais`、U+FFFD 成串、
    content_ok=true。
- **攻击**（是不是我们服务器的错/设计取舍？）：
  - `python -m http.server` 对 .html 只发 `Content-Type: text/html`（无 charset），与大量
    静态站一致；meta 是页面唯一的编码声明。浏览器会读 meta。
  - 读 `fetcher.py`：`_decode_html_bytes(body, declared)` 在 declared 是 UTF-8 时直接
    `body.decode("utf-8", errors="replace")` 并返回；`declared` 只来自 HTTP 头
    （`_charset_from_content_type` 无 charset 时默认 utf-8）。`sniff_html_charset` 不存在。
  - 二进制判定 `_is_binary_payload(raw, declared, content_type)` 同样只看头；GBK 高位字节被
    控制字符比例判成“二进制”，于是 read 路径返回占位句。crawl 路径不经该闸，直接把 GBK
    字节按 UTF-8 解成乱码。
  - 归因：`git show HEAD:src/dhole_mcp/fetcher.py` 无 sniff 函数；对方 diff 只有注释翻译。
    **既有缺陷，非本轮 in-flight 引入，也非靶站伪影。**
- **危害**：一类真实存在的静态页（meta-only charset）读不出正文；crawl 把乱码当成功内容；
  content_ok=true 让调用方把占位句/乱码当可引用证据。**B 类确认。**
- **修法（已备测试草稿，未接线）**：
  1. `fetcher.py` 加 `sniff_html_charset(body)`：取前 16KB 的 `<meta …>`，读
     `charset=`（含 `http-equiv=Content-Type` 的 content 内写法）。
  2. `_decode_html_bytes`：declared 是 UTF-8 时先严格解；`UnicodeDecodeError` 时嗅 meta，
     有非 UTF-8 声明就按它解；字节严格解成 UTF-8 时仍听字节（防错 meta 毁好页）。
  3. `trafilatura_extractor.extract_with_trafilatura` 的二进制判定：HTTP 头无 charset 且
     载荷像 HTML 时，用 `sniff_html_charset` 的结果作为 declared，再判二进制。
  4. 测试：`fixes/test-R16-F1-draft.py`（4 枚：GBK、Latin-1、错 meta 不毁 UTF-8、两种
     meta 形状）——其落地后可整文件移入 tests/。
- **为何不本轮修**：三个目标文件正被另一位 agent 编辑；按用户指示不打扰。修复推迟，
  缺陷保持 CONFIRM 状态。
- **Verdict：CONFIRM(B)，修复推迟（并发编辑）**。

### R16-002 · emoji ZWJ / 零宽空格被丢弃
- **证据**：emoji.html 源文含 👩‍💻（U+200D）与零宽空格 U+200B；抽取后两者都不在，
  组合字符 e+U+0301 归一为预组合 é。
- **攻击**：这是 HTML 解析/归一化的常见行为；视觉与语义几乎不变，且不留 U+FFFD。
- **Verdict：REJECT→E（R16-002；backlog）**。

### R16-003 · recall 噪声与繁简不互通
- **证据**：P01 中文 recall 混入「普通硅酸盐水泥」等外部页；P09 繁体「語言/頁面」不命中
  简体正文，只命中「混合」。
- **攻击**：E6（低分命中出榜）与 CJK 二元组词法均在册；回执 `terms_found` 如实列出命中词。
- **Verdict：REJECT→E6 复发**。

### R16-004 · 量具：跨画像缓存与脚点断言
- **证据**：P05 首读 emoji 页 `cached=True`（P03 刚读过同一 URL）；p02/p03/p07/p08 脚本
  的码点断言写成双层转义，打印 False。
- **攻击**：两者都在量具侧；回执 JSON 与码点复核给出真值（True）。
- **Verdict：REJECT（量具；脚本已修，报告不据此立案）**。

### R16-005 · recall 查不到本地 CSV 的词
- **证据**：P06 `web_recall "café français"` → 0 行，terms=[]。
- **攻击**：recall 的语料是**网页抓取缓存**；`doc_parse` 的本地文件不进这条路（回执 note
  说「cache only knows pages fetched through dhole」）。查询的是 CSV 里的词。
- **Verdict：REJECT（A/用法）**。

### R16-006 · 多语言正向复核（PASS）
- UTF-8 六种文字保真；非 ASCII URL 参数可作缓存键；阿拉伯语表单值与登录；GBK/UTF-16/emoji
  本地文件；CJK/emoji recall；五语搜索 high + neural；discover 9 URL 分账（6+3）。
- **Verdict：PASS**。

### R16-007 · crawl 的 content_ok 与 read 分家
- **证据**：同一 GBK 页，read 给占位句、crawl 给乱码，二者 content_ok 都 true。
- **攻击**：这不是两个缺陷，而是 R16-001 的两个出口；修好解码后两边都应恢复。crawl 的
  `content_ok` 由 `body_substance` 判（乱码被当作可见文本），根因仍在解码。
- **Verdict：并入 R16-001**。

## 最终确认队列

**1 CONFIRM(B)：R16-001（修复推迟，因目标文件并发编辑）**。
其余：E×2（002/003）· 量具×1（004）· A×1（005）· PASS×1（006）。

## 审查过程记录

- 本轮刻意区分「我们的测试站不标准」与「产品缺陷」：`http.server` 的行为与常见静态站一致，
  且 dhole 自己的注释承认过 meta 被忽略；用 HEAD blob 确认无 sniff 代码、对方 diff 只有
  注释翻译，排除了 in-flight 与伪影两种归因。
- 并发纪律：测试草稿放 `fixes/` 不接进套件（避免留下红测试），P6 不暂存 src 下任何文件。
