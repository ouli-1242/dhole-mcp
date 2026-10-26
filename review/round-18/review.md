# round-18 对抗审查（审查员帽）

> 输入：10 份画像报告 + 轮报告 + 4 组探针 + 144 发原始回执。审查阶段读源码/测试。
> **并发前提**：另一位 agent 仍在翻 src 注释（actions.py 有 552+/327- 在途改动）；
> 本轮只改 `actions.py` 的一个校验块，P6 用 HEAD 基线重建单文件补丁只暂存本轮 hunk。

## 新候选

### R18-001 · `press.key` 非字符串被 str() 吞下，失败句错怪 selector（B · CONFIRM）
- **证据**：
  - P11 A10：`{"press": {"selector": "#note", "key": ["Enter"]}}` → 步骤 `ok=false`，
    `act_err = the selector matched no element (or none became actionable) before the
    timeout: Locator.press: Unknown key: "['Enter']"`；
  - P15 复现：key=`["Enter"]` / `13` / `{"code": 13}` 三种都同句；`#note` 在
    `/frame/inner` 上确实存在，且随后合法的 `key="Enter"` 步骤 `ok=true`。
- **代码攻击**：`actions.py` `_validate_actions` 的 press 分支（约 1394 行）
  `k = str(val.get("key", "")).strip()` —— 任何类型都先字符串化，非空即通过校验；
  Playwright 到执行期才抛 `Unknown key`，而 `_action_failure`（约 2173 行）把含
  「waiting for locator」的驱动文本一律归为 selector miss。于是**类型错误穿上了
  selector 失败的外衣**，调用方会去查 DOM/frame，而真正该改的是 key。
- **危害**：动作确实失败（不是静默成功），但第一诊断句是假的、且唯一真因埋在驱动原文
  末尾；与 R08-001「frame 地址落空指错对象」同族。动作子字段里只有 press 这一颗
  还在 `str()` 吞类型（P11/P13/P14 的其余 20 余颗都具名拒绝）。
- **修法（P4）**：dict 形状下 `press.key` 必须是 str，否则当场
  `action i 'press.key' must be a key string (e.g. "Enter"), got <type>`；测试先红后绿。
- **Verdict：CONFIRM(B) → fixed（R18-001）**。

### R18-002 · web_search 参数拒绝缺 `content_ok:false` / `invalid_request:` 前缀（A）
- **证据**：P08 S1/S5/S6 的 `_param_refusal` 族：`error="Invalid mode: ..."`、
  无 `content_ok`；而 P12 B5（schema 层拒绝）有 `content_ok:false` 与前缀。
- **攻击**：`search.py:1427-1454` 写明这是刻意设计——前缀会占掉「参数名开头」那格词，
  且搜索成功回执本来就不带 `content_ok`（P01 S5 亦然）；`error/summary/next_action`
  三格齐全。不是缺陷。
- **Verdict：REJECT→A（设计）**。

### R18-003 · 动作失败句原样带 Playwright 文本（E）
- **证据**：P06 S3 `/ Locator.wait_for: Timeout 10000ms exceeded`；P07 S5 同；
  P11 A10 `Locator.press: Unknown key`。
- **攻击**：`_action_failure` 有意保留驱动首行作证据（注释：第一句用调用方听得懂的说法，
  后面接原文）。前一句为真时这是可接受的证据；R18-001 修好后 press 不再进此分支。
  其余 selector miss 仍带驱动原文，属话术毛刺，不误导成败。
- **Verdict：REJECT→E（R18-003）**。

### R18-004 · doc_parse 收到 URL 时只报「文件不存在」（E）
- **证据**：P09 S3：`file_path="http://127.0.0.1:8399/article"` →
  `File not found: <server cwd>\http:\127.0.0.1:8399\article`，next 只教补绝对路径。
- **攻击**：URL 形状在 `doc_parse` 里被当相对路径，错误句未点破「这是 URL，网页去 web_read」。
  可达且不危险，属自愈指引缺口。
- **Verdict：REJECT→E（R18-004）**。

### R18-005 · nested_fields 非法 JSON 暴露 Python 解码器细节（E）
- **证据**：P12 B4 `nested_fields="a,b"` →
  `nested_fields is not valid JSON: Expecting value: line 1 column 1 (char 0)`。
- **攻击**：参数名与形状都在，末尾是 json 模块原文；可自愈，纯话术。
- **Verdict：REJECT→E（R18-005）**。

### R18-006 · 数字字符串被接受、浮点被拒（A/量具）
- **证据**：P01 S1 `"500"` 接受；P01b `"50"` 读成 50 后 `clamped 50->500` 并在 summary
  报出；P02 S6 discover `"5"` 按 5 页跑；P02 S3 3.5 → `must be an integer`。
- **攻击**：`params.py` 顶注写明「客户端会把数字发成字符串」并按 int 读；浮点会被读成
  另一个意思，故拒绝。行为一致且 clamp 不静默，非缺陷。
- **Verdict：REJECT→A（设计）**。

### R18-007 · P01 S7 靶页记错（量具）
- `/dialog` 没有 `#note`；`#note` 在 `/frame/inner`。该发实际演示了 selector miss 具名；
  补条在正确页面上 click 成功。
- **Verdict：measurement**。

## 对抗后的正向实锤（PASS）
- 13 个工具对未知字段/错类型/空值/枚举错/范围错**逐颗具名**，next_action 可自愈；
- 危险键名 `__proto__`/`constructor` 被参数表拒绝，误用后同会话正常调用不受影响；
- 秘密（basic password / bearer / Authorization 头）**零回显**；明文凭据头被拒并指回
  `cred_defaults`；sys_state 只报名不报值；
- 资源帽 clamp 一律在 summary 具名（含把 `"50"` 读成 50 后的 `clamped 50->500`）；
- 误用后 allow_private / 会话 / 缓存未被污染（P10 S6-S7）。
