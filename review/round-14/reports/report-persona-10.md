# P10 罗青 · 编剧 L1 —— 长文续读 · check · 点名取块回归

## 读数（7 发：cred + search + read×2 + check×2 + css 回归）

| 发 | 工具·形状 | 读数 |
|---|---|---|
| S1 | search「history of the printing press」 | 2 results（2 high） |
| S2 | read /longaside max=500 | `478B of 7.0KB`、truncated、next=478 |
| S3 | 同 URL offset=478 max=500 | `473B of 7.0KB`、truncated——**summary 点名本窗 of 总量**；正文从「。第 11 段」半句起 |
| S4 | check 首问 | `0 changed, 0 unchanged, 1 baseline recorded` |
| S5 | check 再问 | `0 changed, 1 unchanged`（静态页如实） |
| S6 | read `css_selector=h1` | content_ok=true、10 字、error 空（**R13-001 回归 PASS**） |

## 合 · 不合

- **合**：截断→`next_offset`→续读的流程通，两块内容不重复；续读 summary 写
  `473B of 7.0KB`（本窗 of 总量，新特性生效）；check 对静态页给 unchanged；
  R13-001 的修复在真机回归（点名取块不再被判塌陷）。
- **不合（小）**：窗口命名用 B/总量、不给起止字符，L1 要助手翻译「这一块是哪一段」；
  属 E 类措辞，不是缺陷。

## 摩擦清单

1. offset 窗口不含起止字符坐标（E，L1 需要解释）。
2. 续读起点落在半句中间（字符偏移的必然；无感/皱眉之间）。
3. L1 面对 `truncated` / `next_offset` 需要助手解释（E，既有）。

## 判定

- offset 续读内容 PASS；窗口命名 PASS；check PASS；R13-001 回归 PASS。
- 无新 B 类。

## 主观评分

4/5 —— 长文能续、变化能查、窗口也点名了；扣在接缝落在半句中间。
