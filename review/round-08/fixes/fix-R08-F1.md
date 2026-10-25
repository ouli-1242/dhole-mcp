# fix-R08-F1 · frame 地址落空就地具名（R08-001 + R08-005）

- **现象**：①P04 `frame="/frame/inner"`（照地址栏抄的相对前缀）——名字/URL 前缀都不中，落进 CSS 分支炸 Playwright 的 `Unexpected token "/"`，错误里没有 "frame" 字样；②P09 `frame="#nosuch"`——CSS 合法但 0 个 `<iframe>` 匹配，10s 超时后被 selector-miss 句 + iframe 出路顶替，矛头指 selector。
- **根因**：`_frame_locator`（actions.py）的前两支（name/URL 前缀）静默放行给 CSS 支，CSS 支没有「frame 地址自身解析失败」的出口；提示句（R04-F1 两处）只说 "a URL prefix" 没说绝对。
- **修法**：CSS 支落地前 `page.locator(frame).count()` 预数顶层文档——**解析失败** → `frame '<x>' is not a usable address - it is neither an iframe name, an absolute URL prefix (copy it from a snapshot frame row), nor a CSS selector for the <iframe>: the CSS parse failed (…)`；**0 个** → `frame '<x>' matched 0 <iframe> element(s) in the top document - the iframes present: [name -> url, …]. frame= takes …`。两处提示句补 "absolute URL prefix (copy it from a snapshot frame row)"。命中 name/URL 前缀的路径零变化（不多花一次往返）。
- **测试（先红后绿）**：`tests/test_a_frame_address_that_resolves_to_nothing_names_itself.py` 4 测试（相对前缀具名 / 0 匹配具名+在场清单 / 命中路径无噪声 / 提示句带 absolute）+ `test_an_iframe_is_not_a_wall.py` 的 fallthrough 用例补 count()（正形假设「iframe 在场」）。修前 6 红，修后全绿。
- **线上复验**：真重启（pid 20768）后 V1a/V1b 重放（sessions/verify-r8.log）：两条具名错误全文在场。
- **影响面**：web_act frame 寻址错误文本；正形行为零变化；wire 价格不受影响。
