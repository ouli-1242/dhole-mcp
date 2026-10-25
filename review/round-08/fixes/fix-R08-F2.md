# fix-R08-F2 · 重铸同名寄存器要出声（R08-003）

- **现象**：P06 链 `[read #challenge as=dup, read h1 as=dup, fill {=dup}]`——重铸静默 last-wins，fill 实填 h1 页标题，调用方以为填的是挑战 token，零信号。
- **根因**：寄存器铸造点（actions.py 链内 read 执行段）对「名字已在 values」无任何检查——覆盖是合法语义（刷新型重读），缺的只是覆盖现场的信号。
- **修法**：重铸同名的那枚 read 行带 `register_overwritten` 注记：`'dup' was already minted by an earlier read in this chain ('<被覆盖的值>') - this read replaced it; later {=dup} uses THIS value`。首铸不加，语义零变化。
- **测试（先红后绿）**：`tests/test_a_reminted_register_says_so.py` 2 测试（重铸行带注记且点名被覆盖者 / 首铸安静）。修前 1 红，修后绿。
- **线上复验**：真重启后 V2 重放（sessions/verify-r8.log）：注记全文在场（'UHXMR' 被覆盖点名）。
- **影响面**：web_act read 动作行多一个键；行为零变化。
