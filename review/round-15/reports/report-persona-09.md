# P09 花婶 · 园艺师 L1（并行与隔离轮）

## 任务
两个具名 session 各自在登录页填名字：一个不 pin（预期重导航丢值），一个 pin（预期留住）。

## 场景与读数
1. session gardenA：fill「花婶」+ read property=value → fill ok、read 回「花婶」。
2. 同 session 再读（不 pin）：**动作行报 `cannot access local variable 'empty_hint' where it is not associated with a value`**——读空表单框的 property 时抛 Python 未绑定异常，用户看不懂。
3. session gardenB：fill「月季」+ read → ok，读回「月季」。
4. gardenB pin 再读：`pin hit`，读回仍是「月季」。
5. sys_state：7 sessions、2 cookies。

## 问题
- **R15-001（B，已修）**：`read {"selector": ..., "property": "value"}` 命中空值时，`empty_hint` 只在 attribute/文本分支赋值，property 分支未赋值 → UnboundLocalError 直接上脸。正常场景即可触发（未填的表单、重导航后的空框）。已按 test-first 修复并转绿，待 P5 真重启复验。
- 设计确认：不 pin 时同 session 重导航丢值、pin 保留——与 r13 的寿命/机制一致。

## 评分
修复前 2.5/5（核心读操作崩，且错误不可读）；修复后复验通过则 4.5/5。
