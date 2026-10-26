# P10 艾米 · QA L3（保存与下载轮）

## 场景与读数
1. `save_to` 指向目录：`is a directory, not a file`，未写。
2. `save_to=""`：错误是 **`max_save_bytes= was given without save_to=`**——我没给
   max_save_bytes，给的是空 save_to；被点名的参数不对（R17-001）。
3. `save_to=raw17.html`（无后缀限制）：保存原始 HTML 1,265B，文件头 `<!doctype`。
4. sys_state：2 sessions、1 cookie；sys_guide start 可取。

## 问题
- **E（R17-001）**：空 `save_to` 触发的拒绝句点名 `max_save_bytes`，与调用方实际给的参数不符；
  next_action 仍指向补 save_to，可自愈。属错误话术，不是数据风险。
- 无 B。目录/空串/任意后缀三面都未造成误写。
- E（R17-002）：save_to 的 summary 把保存回执句长度当 `NB text` 报告。

## 评分
任务完成 4/5（两处措辞扣 1）。
