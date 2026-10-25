# 画像报告 · P05 云苏2（网络工程师 · L2）· round-08

> 主题：组合与长链。6 发 + p12 补全 2 发。p05-/p12- 前缀。上传三跳 + select。

## 场景实录

- **S1 上传三跳**：装文件 #doc ok（uploaded: [network-audit.txt]）；**无 confirm 的 #send → pending_approval 具名拒**；confirm:true 重发 → POST submitted。
  - **但跨调用现场**：文件在第 1 发装进、提交在第 3 发——第 3 发是新导航新页，**#receipt = "received (0 bytes)"**，无任何「你上一发装的那份没跟过来」的说明。**→ 候选 R08-002**（页面态不跨调用是在册设计，但咬人处静默）。
- **S1b 补全（p12）**：upload+submit **同一发** + confirm → `received network-audit.txt (26 bytes)` ✓——单发组合全通，反证跨调用丢失是状态寿命问题而非上传损坏。
- **S2 dropdown**：select CN（selected 行带 value/label）✓、fill #note ok；click #save/read #saved 超时——**量具注**：/dropdown 页本无 #save/#saved（我脚本记错页，那是 /frame/inner 的元素）；select 与 fill 本身全部成功。
- **对照组**：fill #not-a-select → `Element is not an <input>…` 具名（fill 门指 select 的教学形状）。

## 摩擦日志
- R08-002（唯一候选）；另记量具教训：跨页元素凭印象写 selector 必炸。

## 主观评分
- 3 / 5 / 4 / 总体 4（扣分在跨调用现场的无声）。

## 附录
证据：sessions/persona-05/ persona-12/ + p05-run.log p05b-run.log。
