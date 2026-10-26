# P02 焦挺 · 消防员 L2 —— 链条②discover→hover/drag→read

## 画像与卡面

消防员，L2，常用带工具的 AI 做站内排查。情境：训练材料站上有一张卡片要拖进归档槽，
菜单要先悬停才展开。目标：让 AI 在页面上完成悬停与拖拽，并读回页面自己写的状态。
验收：hover/drag 回执行说清改了什么；read 读到改后值。

## 读数（5 发：cred + discover + act×2 + read）

| 发 | 工具 | 读数 |
|---|---|---|
| S1 | web_discover 种子 / | mapped 8 URL（depth≤2，8 次页面抓取）；行数与首页清单对齐 |
| S2 | web_act hover `#nav-files` → read `#status` | hover ok；`#status` = **HOVERED** |
| S3 | web_act drag `#card-a`→`#slot-b` → read `#status` | drag ok；`#status` = **DROPPED** |
| S4 | web_read /hoverdrag | 200 · 116B · stealthy，页面文本里能看到状态 |

## 合 · 不合

- **合**：hover 和 drag 都不是「假动作」——页面自己的 `#status` 从 idle 变 HOVERED 再变 DROPPED，
  两次都在同一条链里读回。S4 用普通 read 复核，状态文本仍在页面上。
- **不合**：无实质摩擦。discover 地图 8 行与首页行数一致，没有虚行。

## 摩擦清单

1. 只有摩擦是「必须靠页面自定义的 #status 才能证明动作生效」——这是靶站设计，不算产品问题；
   但用户视角确实看的是「页面说 DROPPED」而不是工具自己说成功，这一点反而加分。

## 判定

- hoverdrag 链 PASS：动作行 + 页面状态双账一致（r13 卡面验收 1/1）。
- discover 行可点性再次实证（8 行全真链，r12 家产）。

## 主观评分

5/5 —— 这条链没有一句要用户翻译的话。
