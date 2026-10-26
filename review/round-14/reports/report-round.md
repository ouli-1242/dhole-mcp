# round-14 轮报告 · 主题：回访与新鲜度

> 分支 review/round-14 · 基线 f5f9340 · 服务 127.0.0.1:9214（pid 16212）+ 靶站 8399（pid 22360）·
> env DHOLE_IGNORE_ROBOTS=1（robots 门不可观测）。
> **并发现场**：工作树含另一位 agent 的未提交改动（新动作/文档/测试）；本轮被测对象=当前
> 工作树，按用户指示不提交、不回滚其改动。

## 体量

- **78 发真实调用**：10 张卡主跑 **63** + P1 补条 **15**（p11 discover 正形 3、p12 check 第二发 3、
  p13 r13 回归抽检 9）；**13/13 工具**；13 个 persona 目录。
- 10 张全新画像（图书馆采编/专利审查/咖啡馆主/供应链/数据记者/兽医/策展人/体育数据/
  园艺师/编剧；L3×3、L1×3）。

## 覆盖矩阵

| 卡 | 画像 | 发 | 主链 |
|---|---|---|---|
| p01 | 图书馆采编 L2 | 7 | feed(3d)→read×3→recall |
| p02 | 专利审查 L3 | 8+3 | read(截断/offset)→cache_ttl 三态→check→state |
| p03 | 咖啡馆主 L1 | 6 | guide→read×2→feed→state |
| p04 | 供应链 L2 | 5 | read(urls=3)→回访→request 写闸+confirm |
| p05 | 数据记者 L3 | 6 | research→search→recall→check×2 |
| p06 | 兽医 L2 | 6 | doc_parse(页)feed(1w)→read×2→state |
| p07 | 策展人 L2 | 6+3 | discover→crawl→pin 会话→recall |
| p08 | 体育数据 L3 | 6 | feed(6h/混发)→search clamp→read×2 |
| p09 | 园艺师 L1 | 6 | feed(1w)→xlsx→read×2→state |
| p10 | 编剧 L1 | 7 | search→read(截断/offset)→check×2→css 回归 |

## 主题对账：回访与新鲜度

| 面 | 实测 | 结论 |
|---|---|---|
| 相对 since（3d/1w/6h） | 行内 `since` = 当下减窗口，精确到秒；older/undated 计数自洽 | ✓ 可用（E8 家产升级） |
| 缓存回访 | 第二读 `cached=true`、`fetched_at` 不重盖、过去时说明回放 | ✓ 诚实 |
| `cache_ttl=0` | 0→默认→0 = `None,True,None`，不互相污染 | ✓ |
| `extraction_type=check` | 首问记基线；再问静态页 unchanged（byte-for-byte note）、`/changing` changed + 分叉位置 | ✓ 两相设计成立 |
| offset 续片 | `478B of 7.0KB` → `473B of 7.0KB`，本窗 of 总量（新特性已生效） | ✓ |
| 批量回访 | 三 URL 逐行 cached、逐行 fetched_at | ✓ |
| recall 回访 | 命中同文多变体（E2）；词形巧合无分数（E6） | E 记录 |

## round-13 必测 6 项

| # | 回归点 | 结果 |
|---|---|---|
| 1 | 显式 css_selector 收窄免 collapse | **PASS** p10 S6 / p13 R1（content_ok=true、10 字、error 空） |
| 2 | 缓存回放带 selector 0 匹配注记 | **PASS** p13 R2（live/hit 都有 matched 0 句） |
| 3 | 提交闸 action 级 confirm | **PASS** p04（web_request confirm 顶层，形状不同但闸语义一致）、p07/p13 dialog |
| 4 | dialog prompt 正形/误序 | **PASS** p07（NAMED:顾之遥；pin 命中） |
| 5 | tab/pin 寿命 | **PASS** p07 pin+session 跨调用；tab 本轮未主测（r13 已深打） |
| 6 | 家产合并 | pin/auth_wall/frame/feed 三连/recall clamp 全 PASS（p13） |

## 按用户感知强度排序的摩擦

1. **recall 同文多变体 + 词形巧合**（p01/p05/p07）：问「对话框 删除」返回注意力机制综述
   （命中「删除线」）；无分数。E2/E6 复发，非新 B。
2. **discover 文档与参数面漂移**（p07）：手册仍写 `max_depth`，schema/分发只认 `max_pages`；
   按手册调用被拒（错误列出支持名单，可自愈）。**E 类**，且 docs 正被另一位 agent 编辑，
   本轮不碰。
3. **discover summary 说 `depth <= 2`**：内部默认，用户无法调；与手册的 `max_depth` 相互
   强化误解（并入上条）。
4. **doc_parse summary 不点名 pages**（p06）：字段 `pages_returned=2/pages_total=3` 正确，
   总结只说 1.3KB·parse。E。
5. **L1 工程词**（p03/p09/p10）：`cached` / `fetched_at` / `truncated` 需要翻译。E。
6. **path_include 保留种子行**（p07/p11）：`/work` 过滤后仍带 `/collections?items=6`；根行
   是否例外未在文档说明。D/E。
7. **续读落在半句中间**（p02/p10）：字符偏移的必然，设计取舍。

## 放弃点与 A 类错路

- p07 S1 用 `max_depth`（旧手册词）被拒 → p11 用 `max_pages` 正形补回；A 类，但暴露了
  E 级的文档漂移。
- p02/p10 一开始把 `473B of 7.0KB` 误读成「没点名切片」——复查在途测试后推翻，属量具/读法
  错误（审查阶段改正）。

## 主观评分

| 卡 | p01 | p02 | p03 | p04 | p05 | p06 | p07 | p08 | p09 | p10 |
|---|---|---|---|---|---|---|---|---|---|---|
| 分 | 4 | 4.5 | 4 | 5 | 4.5 | 4.5 | 4 | 5 | 5 | 4 |

均值 **4.45/5**。主题的四颗新面（相对 since / check / offset 窗口 / 逐行缓存）全部成立；
扣分集中在 recall 去重/相关度与文档漂移。

## 能力边界

- 缓存按 URL+extraction_type+selector 分键；`fetched_at`/quality 判决随行，本轮未再发现注记丢失。
- check 基线独立于内容缓存，两相调用；静态页 byte-for-byte。
- 相对 since 支持 `3h/3d/2w`，无日期条目保留。
- offset 是字符窗口，非段落边界。
- discover 的参数面已收窄到 `max_pages/max_total_chars/path_*/resume_token/search`；
  `max_depth` 已不在公开面（文档未同步）。

## 改进清单（按感知强度）

1. recall 同文变体合并/打分（E2/E6 合并议题，backlog 已有）。
2. 同步 discover 手册与 summary 的 depth 说法（E；docs 在并发编辑，下轮或对方落地后处理）。
3. doc_parse summary 点名 pages=N-M（E）。
4. L1 文案层（E，长期）。
5. path_include 是否保留种子行写进文档（E）。

## §18 自检

1. **偷懒走最优路径？** 没有：p07 先踩 `max_depth` 再补正形；p02/p10 的第一次读法错误
   也在审查里改正。
2. **只看结果忽略过程？** 缓存时间戳、check 基线、feed 计数都逐字段核对。
3. **10 画像都发了调用？13/13？** 是（78 发、13 persona、13 工具）。
4. **摩擦 ≥10 条？** 7 类（其中 2 类跨画像重复），另有 A 类与环境量具各列。
5. **归因分清？** A/E/D/环境分开写；靶站/驱动问题未记产品。
6. **审查真推翻过结论？** 是：R14-001（offset 切片）首报被自己推翻；path_include 降为 D/E。
7. **干净是真干净？** 不宣称：列了 5 类 E 与并发在途项；本轮无 B 类 CONFIRM。
8. **分支与产物？** review/round-14；原始回执 78 行台账；不碰另一 agent 的未提交改动。
