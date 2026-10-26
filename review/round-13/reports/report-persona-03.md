# P03 商无咎 · 法医 L3（猎手）—— 链条③dialogs 台账：arm→trigger→对账 + 误序教学

## 画像与卡面

法医，L3，自己搭过 MCP。情境：要删一条记录，页面会弹 confirm；另一处会弹 prompt 问名字。
目标：先武装弹窗答案再触发，确认弹窗账与页面结果互认；故意先 trigger 后 arm 一次，看教学。
验收：dialogs 行逐条有账；误序的教学句在场。

## 读数（7 发：cred + read + act×3 主跑 + p15 补条 2）

| 发 | 工具·形状 | 结果 |
|---|---|---|
| S1 | web_read /dialog | 200 · 30B · no_content（碎片页，诚实标签） |
| S2 | act：`dialog accept` arm → click `#delbtn` → read `#status` | DELETED；**dialogs 行**：`confirm "delete this item?" answered "accepted (armed)"` |
| S3 | act：`{"dialog": {"answer": "商无咎"}}` → click `#namebtn` | **invalid_request**：`action 0 'dialog' must be {answer} - a bare "accept"/"dismiss", or {"answer": ..., "prompt_text"?: ...}`；未执行 |
| S4 | act：无 arm 直接 click `#delbtn` | 页面 **CANCELLED**；dialogs 行：`auto-dismissed` + note「no {"dialog": "accept"} was armed before the action that popped this」 |
| p15-S1 | act：`{"dialog": {"answer": "accept", "prompt_text": "商无咎"}}` → click `#namebtn` | `#status` = **NAMED:商无咎**；dialogs 行 `prompt "your name?" answered "accepted (armed)"` |

## 合 · 不合

- **合**：arm→trigger→页面状态→dialogs 台账四本账互认（S2 + p15）。误序不是静默失败：
  页面 CANCELLED，dialogs 行按「auto-dismissed」入账并说明原因（S4）。这是本轮最干净的一页。
- **不合（候选 C2）**：S3 的错误文案第二形状写作 `{"answer": ..., "prompt_text"?: ...}`，
  实测 `{"answer": "商无咎"}` 被拒；正形要 `answer=accept + prompt_text=名字`（p15 实锤）。
  「answer 只收 accept/dismiss」这层约束在文案里没有点名，用户按字面试一次会多走一个回合。
  这是文案两可，不是功能缺失——p15 一发即通。P3 判 E 还是 D，看源码/测试出处。

## 摩擦清单

1. prompt 形状第一次被拒，错误文案说「or {"answer": ..., "prompt_text"?: ...}」，字面像接受任意 answer；
   实际只有 accept/dismiss。多花一个回合（p15 才通）。
2. /dialog read 只有 30B 碎片，弹窗文本不在正文里——要看「delete this item?」只能靠 dialogs 台账。

## 判定

- dialogs 链 PASS（r13 卡面验收 2/2：逐条有账、误序教学在场）。
- C2（prompt 错误文案两可）转 P3 审查，候选等级初判 E。
- r08 家产「无 arm → auto-dismiss 可见」PASS。

## 主观评分

4/5 —— 台账与页面互认很好；扣分在那句两可的 prompt 形状说明。
