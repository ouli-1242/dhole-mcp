# fix-R07-F1 · R07-001 寄存器具名拒绝补「一条链 + pinned 重读」出路

- **现象**：P02（L3 猎手）按「pin 保页」做跨调用 read→fill——寄存器不跨调用，拒绝句只说「no earlier action stored that name」。手册本身是对的（text 挑战在同一链内解、pin 解决的是 IMAGE 案的人在中途——tools.md:40 原文），但拒绝现场没把「寄存器只活一条链、pin 保的是页面、重读即同值」这半个前题教出去。
- **根因**：`_validate_actions`（actions.py）的具名拒绝在验证层，写于 pin 语义成熟之前；手册后来长出了 pin 的一整章，拒绝句没跟上。
- **修法（话术层）**：`no earlier action` 分支追加：`- registers live for this one call: mint the name in THIS chain with an earlier read step's as=<name> before the fill; a pinned page (pin=true) holds the page open, so a re-read in the next call's chain returns the same token`。有可用寄存器的分支（available: [...]）不加（那半个前题只对「根本没读过」的现场有用）。行为零变化。
- **测试（先红后绿）**：`tests/test_the_register_miss_points_at_the_pin_way.py`——拒绝句含 one call/this chain、read as=、pin；有寄存器时仍列 available。修前红，修后绿。
- **线上复验**：真重启后 pin=true 裸 fill 重放（sessions/verify-r7.log W1）：补句全文在场。
- **影响面**：web_act 验证层错误文本；wire 价格不受影响。
