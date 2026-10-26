# round-12 对抗审查（P3）

> 立场：推翻 P2 的乐观结论优先，补刀 P2 没打到的角落。本轮 5 个候选线索全部查透，
> **0 CONFIRM / 5 REJECT + 2 观察项**——继 r05、r10 后第三个零修复轮（非连续）。

## 候选一（R12-001）：web_read css_selector 0 匹配「静默回落整页」→ REJECT

- **首报**：P09 S3 / p05b S9 对 /article 发 `css_selector="#no-such"`，回执 387B 整文、
  error 空——疑 R06-001 家产（0 匹配说出去）丢失。
- **查**：四账面字段铁律——读全 summary（517ch）后：
  `css_selector '#no-such' was NOT applied: it matched 0 elements on this document - the
  whole document was returned (narrowing never empties the answer)` 原句在 summary 中段。
  代码侧 `trafilatura_extractor.py:582` `_note_selector_matched_nothing` →
  `_ARG_NOTES` → server.py 折进 summary（7534 注释明确「折进 summary」）。
- **判**：REJECT。家产在场，首判是 130ch 截断所致（本轮同根教训第三次，r08×2/r09×1）。

## 候选二（R12-002）：/ 页缓存「代际倒退」1745ch→36B → REJECT

- **首报**：P0 smoke 读 / 得 1745ch；p09 S1 缓存 replay 只有 36B「no prose (menu
  fragments only)」，存入时间 7.7min 前（≈p07 crawl 时段）——疑 crawl 的碎片抽取
  污染了 read 的缓存代际（r10 R10-001 的镜像案）。
- **查**：(a) curl 实测 `/` 原始 HTML 仅 450B（标题+7 个导航链接），36B markdown
  **就是全页的忠实抽取**；(b) 1745ch 之謎——raw client 复发：单 content block 1744ch，
  那是**整个 envelope JSON**（status/content/summary/metadata…），我的 smoke 把
  envelope 长度当正文长度。`total_extracted_chars: 36` 与 curl 互证。p05c S12 的
  /login 24B「menu fragments only」同理（登录页本来就只有表单）。
- **判**：REJECT。无代际、无污染、无倒退——三个量具错误叠加出的假候选。

## 候选三（R12-003）：search max_results=0「无具名拒绝」→ REJECT

- **首报**：P09 S4 发 max_results=0，err=None——疑 r09 家产（max_results 具名）丢失。
- **查**：全 summary：`max_results=0 is outside the supported 1-50 range; returning at
  most 1` 在 summary 内，且真返回了 1 行（clamp 落地）。research 同形（`clamped:
  ['max_sources -> 1 (1-5)']`）、crawl 同形（`max_pages clamped 0->1 (1-40)`）。
- **判**：REJECT。clamp-and-name 的落点字段是 summary/clamped，不是 error——
  越界具名三连都在，首报看错了字段。

## 候选四（R12-004）：web_act 对 /login 的 auth_wall 判词「挡住合法填表」→ REJECT（转观察项）

- **首报**：p05b S6 对 /login fill 被 `auth_wall_detected` 拦下，疑过度拦截。
- **查**：actions 行显示 fill ok=true（**动作链照跑**），判词只给内容打分
  （content_ok=false）；next_action 教两扇真门（cred_defaults 存凭据 / 换源）；
  quality.py:1077 判词与 1187 注释明确「判词说内容、动作说动作」分家设计
  （R01-028 的直接产物）。fill 的值在 p05c 回读实证仍在。
- **判**：REJECT。行为合理且账目分明；入册为观察项（auth_wall 首次实锤）。

## 候选五（R12-005）：recall 空白 query 报「Missing required」措辞不实 → REJECT（转观察项）

- **首报**：query="   " 被报 `Missing required argument(s): ['query']`——传了但空白，
  说「没传」不精确。
- **查**：server.py:12855 通用门对空白参数按缺席处理，`test_server_10.py` 等 3 处
  test 立规；error 带参数名+支持名单，next_action 教修法。仓库判例（r11 R11-003/
  R11-005）：教学在、形状在册 → 不立案。
- **判**：REJECT。措辞可议但形状是 test 立规的家产；入册观察。

## P2 没打到、P3 补刀的角落

- **脚本侧误用三枚全排除**：discover 无 links 键（行=pages）、doc_parse content 是
  字符串列表、sys_state 账在 message——回执形状各自一致，非缺陷。
- **p05b 覆盖回执事故**：补条脚本复用 persona-05 目录，主跑 S1-S4 回执被覆盖。
  台账 calls.jsonl 与运行日志保全，报告数据两源互证无损。程序教训入册（补条用独立号）。
- **写闸在链上的第三种活法**：P10 S5 HEAD 免 confirm——S1 的教学句预告、S5 实证，
  合同闭环。

## 结论

**0 CONFIRM。** 十条链、68 发、13/13 工具，无一发需要改产品代码。两个险些立案的
候选都是量具/截断问题——「判定前读全四账面字段」铁律继续有效，建议 round-13 的
卡片继续按「链」设计（跨工具接缝是当前最薄的墙，而它本轮全部咬合）。
