# P02 陆知微 · 专利审查员 L3（猎手）—— offset 续片 · cache_ttl 语义 · check 两相

## 读数（8 发主跑 + p12 补条 3 发）

| 发 | 工具·形状 | 读数 |
|---|---|---|
| S1 | read /longaside max=500 | `478B of 7.0KB`、truncated、next_offset=478 |
| S2 | 同 URL offset=478 max=500 | `473B of 7.0KB`、truncated——**summary 点名「本窗 of 总量」**（新特性，见下）；正文从「。第 11 段」半句续起 |
| S3 | cache_ttl 0→默认→0 | `cached=None, True, None`——三种读法互不污染 |
| S4 | check 两 URL 首问 | `0 changed, 0 unchanged, 2 baseline recorded`，逐行 note 说「已记基线，再问一次」 |
| S5 | sys_state | 1 session、0 cookie、Nothing erased |
| p12-T1/T2 | check 静态页首问/再问 | 再问：`0 changed, 1 unchanged`，note「body matches the baseline byte for byte」 |

## 合 · 不合

- **合**：`cache_ttl=0` 的语义精确（不缓存、不拿旧版冒充）；check 的两相设计诚实——首问
  记基线并说明，第二问才回答 changed/unchanged。p05 的 `/changing` 第二问得到
  `changed=true, first_divergence_at=111, baseline 184 / fresh 203`，证据链完整。
  offset 续片的 summary 写 `473B of 7.0KB`——**本窗字数 of 整页总量**，不是把整页写成
  473B；这正是这轮在途新特性（`test_an_offset_window_summary_names_the_slice.py`）的
  目标形状，实测已生效。
- **不合（小）**：窗口命名用字节数（B/KB）而不是起止字符坐标，引用专利段落时仍要自己
  数；对 L3 可接受，属措辞取舍。

## 摩擦清单

1. offset 窗口用 B/总量命名，不给起止字符（无感）。
2. 续片起点落在半句中间（字符偏移的必然结果；建议按段落边界切，但属设计取舍）。
3. check 首问的 `0 changed, 0 unchanged` 读起来像「什么都没发生」，要读到 note 才知道
   基线已记（措辞可优化，无感）。

## 判定

- cache_ttl 三态 PASS；check 两相 PASS；offset 续片内容无重复（P10 复核）PASS；
  offset 窗口命名 PASS（新特性实测生效）。
- 无新 B 类。

## 主观评分

4.5/5 —— 三件事都对，窗口也点名了；扣在续片落在半句中间。
