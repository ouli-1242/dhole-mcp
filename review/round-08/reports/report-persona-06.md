# 画像报告 · P06 屠维2（DevRel · L2）· round-08

> 主题：组合与长链。4 发 + p13 补全 2 发。p06-/p13- 前缀。长链双寄存器。

## 场景实录

- **S1 长链双寄存器**：read #challenge as=code（FJEHK）+ read h1 as=title（请按原样抄写…）双名入账；fill {=code} ok；click 被闸拦（pending_approval）；链尾 read ok。**p13 补全**（confirm:true）→ 端到端「抄写正确」，双寄存器端到端不串名。
- **S2 顺序错（fill 在 read 前）**：R07-F1 句全文在场（this one call / as=late）——**回归 PASS**。
- **S3 重名寄存器（两次 as=dup）**：**静默收下**——第二次 read（h1）覆盖第一次（#challenge 的 token），fill {=dup} 实填的是 h1 文本，无任何提示。**→ 候选 R08-003**（last-wins 本身可辩护；无声覆盖是写路径上的静默换值）。

## 摩擦日志
- R08-003：重名即静默换值——调用方以为填的是挑战 token，实际填的是页标题。

## 主观评分
- 5 / 5 / 3 / 总体 4.3（扣在重名无声）。

## 附录
证据：sessions/persona-06/ persona-13/ + p06-run.log p06b-run.log。
