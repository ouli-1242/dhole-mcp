# 画像报告 · P10 越千帆2b（SDET · L3 猎手）· round-08

> 主题：组合与长链。9 发。p10- 前缀。组合态攻击。

## 场景实录

- **S1 dialog 武装但弹窗不出（frame 页）**：armed accept + click #save（内层无弹窗）→ 账行 **`armed_unused: 1, note "armed answer(s) that never met a dialog"`**——武装未触发的 case 如实交账，不静默。
- **S2 双 session 交替**：sA(alice)/sB(bob) 各自 POST+GET 登录；各自 /account 读「欢迎回来」——两会话互不串（注：/account 内容不含用户名，隔离证据是双独立成功 + 互不干扰，非内容区分）。
- **S3 跨 frame 寄存器**：fill R10（frame:app）→ read property:value as=v（**R10**）→ fill {=v}-ok → click → **SAVED:R10-ok**——寄存器 ×property 读 ×frame 寻址三件套组合成立。
- **S4 tab 边界**：tab 3 → `tab 3 does not exist - inventory: ['/about']` 具名带清单 ✓；tab list ✓。

## 摩擦日志
（无候选：四个组合态全部具名可解释。）

## 主观评分
- 5 / 4 / 5 / 5 / 总体 4.8。

## 附录
证据：sessions/persona-10/ + p10-run.log。
