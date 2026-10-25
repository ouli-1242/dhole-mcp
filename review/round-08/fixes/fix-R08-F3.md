# fix-R08-F3 · upload 行教寿命（R08-002）

- **现象**：P05 第 1 发 upload 装文件、第 3 发提交——第 3 发是新导航新页，`received (0 bytes)` 无任何说明（p12 反证单发组合全通，`received network-audit.txt (26 bytes)`）。
- **根因**：「文件是页面态、活到这一发导航为止」在 behavior 手册（tools.md）upload 段与 upload 动作行里都没说；咬人现场（空 input 提交）落在靶站侧，dhole 侧无从插话。
- **修法**：在装文件的现场说——upload 成功行带 `lifetime` 句：`the files ride THIS page instance - submit within the same call (a later call starts a fresh page)`；tools.md upload 段补同一句（含 `received (0 bytes)` 的空结果形状）。链路行为零变化。
- **测试（先红后绿）**：`tests/test_the_upload_row_teaches_its_lifetime.py` 2 测试（行带 lifetime / 手册段落含 page instance）。修前 2 红，修后绿。
- **线上复验**：真重启后 V3 重放（sessions/verify-r8.log）：lifetime 句全文在场。
- **影响面**：web_act upload 动作行多一个键 + skill 手册一句；行为零变化。
