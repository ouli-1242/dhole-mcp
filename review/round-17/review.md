# round-17 对抗审查（审查员帽）

> 输入：10 份画像报告 + 轮报告 + 77 发原始回执。审查阶段读源码/测试。
> **并发前提**：另一位 agent 仍在翻 src 注释（reranker.py 14:11 在改）；本轮只动
> `fetcher.py`（11:50 后未再动），P6 用 HEAD 基线生成单文件补丁只暂存本轮 hunk。

## 结转：R16-001（B）修复复核

- **修法**：`fetcher.py` 三层——
  1. `sniff_html_charset(body)`：前 16KB 的 `<meta>` 里读 `charset=`（含 http-equiv 形状）；
  2. `Response.encoding`：HTTP 头明确声明 charset 时听头；否则 sniff `<meta>`，再退回原值；
  3. `_decode_html_bytes`：UTF-8 声明先严格解，失败时按 meta 解；字节合法 UTF-8 时仍听字节
     （防错 meta 毁好页）。
- **测试**：`tests/test_a_page_declaring_gbk_in_meta_decodes_as_gbk.py` 4 枚，先红后绿。
- **真机**：GBK 页 read=「国标编码页面…」、Latin-1=「français … é è ê à ç ù」、
  crawl 的 GBK 页不再乱码；pytest 7845→**7849**，无回归。
- **Verdict：CLOSED（fixed）**。

## 新候选（本轮）

### R17-001 · 空 save_to 的拒绝句点名错参数（E）
- **证据**：p10 S2 `save_to=""` → `invalid_request: max_save_bytes= was given without save_to=`；
  调用方并没有给 max_save_bytes。
- **攻击**：读 `server.py:7883-7890`——`if save_to is not None or max_save_bytes is not None:`
  后统一用 `if not (isinstance(save_to, str) and save_to.strip())` 返回同一句
  「max_save_bytes 没配 save_to」。空串进入这条分支时，被点名的参数不是调用方给的那个。
- **危害**：错误可自愈（next_action 仍教补 save_to），无数据风险；属话术。
- **Verdict：REJECT→E（R17-001）**。

### R17-002 · save_to 的 summary 把回执句长度当正文（E）
- **证据**：p01 `summary: 200 OK · 82B text`，content=`saved 1,265 bytes to …`（82 字），
  `extracted_type=text`、`total_extracted_chars=82`。
- **攻击**：save_to 在抽取之前分叉（代码注释明说「交字节，不交正文」），但 summary/extracted_type
  仍按抽取回执说话。metadata 的 saved_to/bytes/sha256 才是真账。
- **Verdict：REJECT→E（R17-002）**。

### R17-003 · save_to 的两套路径语义（E）
- **证据**：read：已存在文件→拒；缺父目录→拒。截图/PDF：同路径→覆盖；缺父目录→自动创建。
- **攻击**：两条都是「调用方点名写哪里」，但同一参数名下两种合同；错误/回执各自说清，
  不致误写。统一或文档点名均可，行为改动需产品裁决。
- **Verdict：REJECT→E（R17-003）**。

### R17-004 · 相对遍历被允许
- **证据**：`save_to=../escape17.md` 落盘到 sessions/ 上一级（脚本随后清理）。
- **攻击**：save_to 是调用方在自己机器上指定的路径；唯一硬边界是系统目录名单
  （P02 的 `C:\Windows` 被拦且未发请求）。不做沙箱是设计。
- **Verdict：REJECT（D，设计）**。

### R17-005 · save_to 永不吃阅读缓存
- **证据**：p08 同 URL 连续三次 save_to，三次 `cached=None`。
- **攻击**：代码注释明确：缓存里是**抽取出来的正文**，save_to 要**原始字节**，两者不是一份东西；
  r14 亦记「save_to 的回执不存缓存」。回执没有正面点明这一点，属文档缺口。
- **Verdict：REJECT→D/E（回执可补一句）**。

### R17-006 · 上传回执 selector 落空
- **证据**：p05/p11 读 `#status` 全落空；p12 读 `#receipt` 得到
  `received 审计清单-中文.txt (26 bytes)`。
- **攻击**：靶站 /upload 的真实 id 是 `#receipt`；`#status` 是 /dialog 的。A 类自查失误。
- **Verdict：REJECT（A）**。

### R17-007 · PNG 头断言误报
- **证据**：p03/p09 打印 png_magic=False；字节复核 `89 50 4E 47 0D 0A 1A 0A` 正确。
- **攻击**：脚本里的 bytes 字面量双层转义（与 r16 码点断言同族）。量具，脚本已修。
- **Verdict：REJECT（量具）**。

### R17-008 · 保存/下载面正向复核（PASS）
- 覆盖门先拒后发；小帽具名且不落半截；系统目录写前拒；字节账 sha256 一致；
  中文/emoji 文件名；PNG/%PDF 头；PDF→doc_parse 往返；二进制原样保存；上传中文名 26B 回执。
- **Verdict：PASS**。

## 最终确认队列

**0 个新 CONFIRM**。结转 R16-001 已修（4 测试绿 + 真机复验）。
其余：E×3（001/002/003）· D×1（004）· D/E×1（005）· A×1（006）· 量具×1（007）· PASS×1（008）。

## 审查过程记录

- 本轮两次踩到「脚本字面量双层转义」的量具坑（PNG 头），已固化教训：**断言字节/码点前
  先在 REPL 里打印真实值**；报告以文件字节为准。
- R16-001 的修复只动一个文件，且用 HEAD 基线生成补丁：staged 版本不含对方的注释翻译，
  其工作树改动原样保留。
