# fix-R06-F2 · R06-002 css_selector 点名的碎片不再被整页散文尺误判

- **现象**：`.card a` / `a[href^='/work/']` **正好取回用户点名的 11 行**（59 字符），却 `content_ok=false`、substance=`nav_only`、summary 头一句「no prose (menu fragments only)」——用户要的碎片拿到了，回执说它不行。两画像两现场。
- **根因**：`_substance_verdict`（quality.py）的两把尺（nav_only 散文尺 / collapsed 塌陷尺）都拿「整页可见字」当分母；css_selector 在场时回执量的是**点名的碎片**，尺子用错了量程。原则已在册：extractor 侧的塌陷**回收**那把尺早就豁免了 css_selector（"narrowed 的调用方要的是更少"）——实质这把尺缺同一枚豁免。
- **修法（判据层）**：`_substance_verdict` 在 served_as_page/空内容两道门之后、collapsed 判据之前加一枚豁免：`_CSS_SELECTOR.get()` 在场 → 返回 `("", "css_selector narrowed this answer to the requested fragment - the whole-page prose and collapse rulers do not apply")`。留空判 = 不给形状意见（content_ok 不再被 nav_only 翻 false）；obfuscated_payload 的 no_words 分支不受影响（那是混淆闸自己的判决）。复用已有的 `_CSS_SELECTOR` ContextVar（分发层已在置位），不加新变量。
- **测试（先红后绿）**：`tests/test_a_fragment_the_caller_asked_for_is_not_judged_as_the_whole_page.py`——前置断言证明整页尺确实会误判（修前现场）；置位后 verdict="" 且 basis 点名 css_selector；未置位照旧 nav_only。修前红，修后绿。
- **线上复验**：真重启后重放（sessions/verify-r6.log W2）：`content_ok: True`、substance 空、basis 全文在场。
- **影响面**：web_read 的 substance/content_ok 判定（仅 css_selector 在场的调用）；wire 价格不受影响。
- **顺带**：清掉一枚基线漂移的旧告警（test_what_this_machine_already_read_is_one_recall_away.py 的未用 asyncio import）——ruff 回到 16 条基线。
