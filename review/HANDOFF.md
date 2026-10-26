# 交接文档 · dhole-mcp 真实使用模拟审查循环

> 写于 2026-10-26 · 交接点：round-13 P1 执行到一半被暂停。
> 接手者先读本文档 §2-§4，再按 loop/PROMPT.md（宪章）核对细节。

---

## 1. 任务是什么

自主循环（`/goal` 设定）：**模拟真实用户多场景多工具使用 dhole-mcp → 写问题报告 →
审查代码 → 修复 → 重启服务二次测试 → 回到第一步**。执行按宪章 `loop/PROMPT.md`
的 P0-P7 分相：每轮新建分支/换端口/封存 10 张画像卡 → 画像真机模拟（P1）→
报告（P2）→ 对抗审查（P3）→ test-first 修复（P4，有 CONFIRM 才做）→ 真重启复验 +
全量 pytest（P5）→ 收官提交（P6，`git add -f review/`，review/ 在 gitignore 里）→
下一轮（P7）。铁律：修复必须测试先行；绝不削弱安全/robots 闸；诚实记录 REJECT 与
flake；全量 pytest 保绿；ruff 16 基线。

## 2. 进度总览（截至交接点）

| 轮 | 主题 | 结果 | commit |
|---|---|---|---|
| round-01…10 | （各轮主题见 review/index.md） | 全部闭环，累计 20 个 fix | 见 index.md |
| round-11 | 冷面深水（doc_parse/discover/request/feed 深打） | 1 fix（R11-001 feed max_items 负数拒绝）+ 4 REJECT | `120a5d7` |
| round-12 | 链条协奏（多工具真实链条） | **0 CONFIRM**（第三个零修复轮）；68 发 13/13 工具，5 候选全 REJECT | `22e9f88` |
| **round-13** | **重装链（长链条 × web_act 重交互面）** | **P0 ✓，P1 跑到 8/10 卡被暂停** | 未提交 |

- 全量测试水位：**pytest 7771 passed / 23 skipped；ruff 16 errors（在册基线）**，
  round-12 收官时点平账。
- 历轮材料：`review/round-NN/`（plan/reports/review.md/issues.json/verification.md/
  fixes/scripts/sessions）。跨轮资产：`review/index.md`（轮次一览）、
  `review/common-issues.md`（**每轮末尾有「必测回归点」，下一轮 P1 必须嵌进画像卡**）、
  `review/_journal.md`（收官日志）、`review/backlog.md`、`review/decisions.md`。

## 3. round-13 断点精确状态

**环境**：分支 `review/round-13`（基线 22e9f88）；服务 127.0.0.1:**9213**（pid 17756）
+ 靶站 8399（pid 18636）；env `DHOLE_IGNORE_ROBOTS=1`（ensure_up.ps1 顶层设置）。
工作树干净（仅 .runtime 日志脏）。

**P0 已完成**：主题「重装链」+ 10 张卡封存于 `review/round-13/plan.md`；
`round-state.json` 停在 `P0_done`（P1 完成后需更新）。

**P1 已落台账 48 发**（`review/round-13/sessions/calls.jsonl` + persona-NN/ 回执 +
pNN-run.log）：

| 卡 | 画像 | 状态 | 要点 |
|---|---|---|---|
| p01 | 地铁司机 L1 | ✓ 5 发 | select `#country`→CN 回读一致；state 复盘诚实 |
| p02 | 消防员 L2 | ✓ 5 发 | hover→HOVERED；drag #card-a→#slot-b→**DROPPED** |
| p03 | 法医 L3 | ✓ 5 发 | arm accept→click→**DELETED** + dialogs 台账行；**prompt 形状错**（见 §5）→ 拒绝教学；**无 arm 触发→自动 dismiss→页面 CANCELLED**（诚实可见） |
| p04 | 天文台 L2 | ✓ 6 发 | **frame= 不是顶层参数**（见 §5）→ 拒绝教学；S4/S5 有效 |
| p14(=p04b 补) | 同上 | ✓ 5 发 | **正形实锤**：frame= 写在 action target 里，iframe 内 fill/click/read 全通；错名 frame → action 行报 `matched 0 <iframe>… iframes present: ['app -> …']`（r08 家产 PASS）；recall 对 iframe 词 0 结果如实 |
| p05 | 京剧琴师 L1 | ✓ 5 发 | upload ok + **寿命句**在场；click submit → **pending_approval 闸**（回执如实 nothing uploaded） |
| p06 | 船闸管理员 L2 | ✓ 5 发 | 写闸+confirm；captcha 读题 2VR7A→fill→**click 被闸**（1 action waiting）；**pin hit ✓ 页保持（同题 2VR7A）** |
| p07 | 隧道测量员 L2 | ✓ 7 发 | search/crawl clamp 落 summary；discover 5 行 work=3 对 items=3 |
| p08 | 水质检验员 L1 | ✓ 5 发 | feed since 增量 + 混发逐行 404；xlsx 单表 parse ✓ |
| p09 | 灯光师 L3 | **✗ 0 发**（tab 链脚本已写好：scripts/p09.py，被中断在任何调用前） |
| p10 | 拍卖师 L2 | **✗ 未跑**（checkout 提交闸链：scripts/p10.py） |

**已识别、未执行的补条**（写脚本时用**独立 persona 号**——p15/p16…，勿复用主跑目录，
r12 教训）：p03 的 prompt 正形 `{"dialog": {"answer": "accept", "prompt_text": "名字"}}`；
p05b 上传+submit 带 `confirm:true` 交单（期望 #receipt=`received (N bytes)`）；
p06b captcha 点击带 `confirm:true` 完成提交。

## 4. 续跑手册（命令级）

```bash
cd D:/Dev/Tools/dhole-mcp          # Git Bash

# 0) 服务在不在（9213 pid 17756 / 8399）：
netstat -ano | grep -E ':(9213|8399).*LISTEN'
# 缺谁起谁：pwsh -NoProfile -File review/round-13/ensure_up.ps1
#   （注意：重启后进程内 cred_defaults 清零，所有脚本自带 cred_defaults 打头，无需手工补）

# 1) 续 P1（画像间 ≥18s 节流）：
python -X utf8 review/round-13/scripts/p09.py > review/round-13/sessions/p09-run.log 2>&1
sleep 18 && python -X utf8 review/round-13/scripts/p10.py > review/round-13/sessions/p10-run.log 2>&1
# 再写三个补条脚本（p15/p16/p17 号）跑掉

# 2) P2：写 reports/report-persona-NN.md ×10 + report-round.md（读全回执四账面字段）

# 3) P3：review.md 对抗审查（先推翻 P2 乐观结论）+ issues.json

# 4) P5：taskkill //PID <9213的pid> //F && pwsh -NoProfile -File review/round-13/ensure_up.ps1
#    写 scripts/verify_r13.py（V1-V3 见 verify_r12.py 样板）跑 PASS
#    python -m pytest -q -p no:randomly --dist no -x  → 7771+ 全绿
#    python -m ruff check .  → 16 基线

# 5) P6：更新 index.md 行 / common-issues.md（r13 段+必测回归点）/ _journal.md /
#    round-state.json→P6_done，然后：
git add -A -- src tests; git add -f review/
git reset -q -- $(git diff --cached --name-only | grep __pycache__)   # pycache 清场，验证 grep -c = 0
git commit  # 信息式样见 22e9f88

# 6) P7 → round-14 P0（端口 9214，新 10 职业，先读 common-issues 的 r13 必测回归点）
```

## 5. 铁律与教训（接手者必读）

**判定纪律**：判定「无账/缺账/沉默」之前**必须读全回执四账面字段**
（summary / error / next_action / fetch_hint），且 summary 要**全文**——r08×2、r09×1、
r12×2 共五次「险些立案」全是打印截断（130-500ch）造成的假候选。round-12 的
R12-002 甚至三层叠坑：1745ch 是 envelope 长度不是正文。

**P1 至今新实锤的形状速查**（回执/参数形状，接手者写脚本别再踩）：

- `web_act` **没有**顶层 `frame` 参数——frame 写在**每个 action 的 target 里**：
  `{"fill": {"selector": "#note", "text": "...", "frame": "app"}}`。
- `dialog` 形状：`{"dialog": "accept"}` / `{"dialog": {"answer": "accept", "prompt_text": "..."}}`
  ——answer 只有 accept/dismiss，prompt 的**回答文本放 prompt_text**。
- `tab` 形状：`{"tab": "new"|"close"|"list"|N}` 或 `{"tab": {"open": "https://…"}}`。
- `select` 形状：`{"select": {"selector": "#country", "option": "CN"}}`（value/label 双匹配）。
- **提交闸比想的大**：`#send`（上传表单提交按钮）和 captcha 的 `#challenge-btn`
  这类**会发请求的 click/submit 都吃 pending_approval 闸**——不带 confirm 时该行
  `ok=false, pending_approval=true`，链上后环照跑，页面状态如实反映未提交。
- 表单域取值要 `"property": "value"`（裸 inner_text 是空）。
- 回执字段家族：doc_parse 的 `content` 是**字符串列表**；web_read 是 dict.text 列表；
  discover 的行就是 `pages` 本身（无 links 键）；sys_state 账在 `message` 键；
  research 失败源在独立 `failed` 数组（带 why）；clamp 落 `summary`/`clamped`
  （不在 error）；web_act 弹窗/frame 的具名错误在 **metadata.actions 行内**。
- 真重启后**进程内 cred_defaults 清零**，私网门重新武装——每个脚本/复验先置
  `{"set": {"allow_private": True}}`。

**Windows/流程坑**：杀服务用 `taskkill //PID N //F`（Git Bash kill 无效）；Edit 工具
先 Read；`git rm` pathspec 有一个不匹配就整体中止；补条脚本**绝不复用主跑 persona
目录**（r12 的 p05b 覆盖了 p05 的 S1-S4 回执，靠台账+运行日志才救回）；xdist 的
DNS-recheck 双测试偶发 flake（隔离跑绿，顺序跑绿，如实记录即可）。

**靶站事实**（写卡前先核对，r12 押错过一次 /work/999）：`/work/N` 对任意整数渲染
200（无 404 分支）；真 404 用 `/no/such/page`；`/` 及多数靶页是菜单碎片页
（"no prose (menu fragments only)" 是诚实标签，不是缺陷）；重交互页：
/dialog（alertbtn/delbtn/namebtn→#status）、/frame（iframe name=app，内层 #note/#save/#saved）、
/upload（#doc/#send/#receipt）、/hoverdrag（#nav-files hover、#card-a→#slot-b、#status）、
/dropdown（#country value=CN/JP/+1 与 label=中国/日本/美国 刻意不同）、
/challenge/text（#challenge/#answer/#challenge-btn）、/checkout（#item/#qty/#place-order，
POST 写订单日志=不可逆步）。

## 6. 验收标准（一轮怎样算闭环）

1. P1 台账 ≥40 发、13/13 工具、10 卡全跑（含补条，persona 号独立）；
2. P2 十份画像报告 + 轮报告（每条判定可溯源到回执文件）；
3. P3 审查至少尝试推翻每个候选；CONFIRM 走 test-first 修复（先红后绿）；
4. P5 真重启（pid 换新，记 server-restarts.log）+ 复验探针 PASS + 全量 pytest 绿 +
   ruff 16；
5. P6 四件跨轮资产更新 + 提交（pycache 0 附加）；
6. 全程诚实：REJECT、flake、量具错误都写在报告里，不装没发生过。

——交接完毕。接手后若有与本文不符的现场，以现场回执为准并把本文改对。

---

## 后记（round-13 收官后）

- 接手后已补齐：p09/p10 + 补条 p14-p19 + P3 探针 p20-p22（共 95 发）。
- P3 得 2 个 CONFIRM（R13-001/R13-002），已 test-first 修复；P5 真重启 pid 17028，
  V1-V4 PASS；pytest 7775 绿 / ruff 16。
- 用户指示：**本轮收官后停止循环**，round-14 未启动。`round-state.json` = `P6_done`。
