# fix-R04-F1 · R04-001 web_act selector 超时且页面有 iframe 时指 frame: 寻址

- **现象**：P02 对 iframe 内元素用裸 selector 三连败，各烧 10s（33s 税），步级错误只说 "the selector matched no element (or none became actionable) before the timeout"——从头到尾没提这页有 iframe、元素在里面要用 `frame:` 寻址（机制与文档都在：actions.py `_act_target` 三种拼法 + sys_guide behavior）。
- **根因**：`_action_failure`（actions.py）把 Playwright 异常翻成 callers 话时只有异常文本可用，**失败现场拿得到的 page 没被用上**——「页面有子 frame」是当场可知的一般事实，不是域名特判。
- **修法（一般机制）**：`_action_failure(exc, page=None)`——selector 落空分支里查 `page.frames` 的子 frame 数（异常安全），>0 时步级错误追加：`· this page hosts N <iframe> element(s) and the selector only sees the TOP document - if the element lives inside one, address it with "frame": (the iframe's name, a URL prefix, or a CSS selector for the <iframe>), e.g. {"selector": "...", "frame": "#app"} - sys_guide(topic='behavior') has the full shape`。链内 except 处传 `page=page`（另一调用点 2146 是 open_url 导航失败，无链上下文，不传）。无 frame 页与非 selector 失败一字不加。
- **测试（先红后绿）**：`tests/test_a_selector_timeout_on_a_framed_page_points_at_frame.py`——有子 frame → 提示含 frame/iframe/"frame" 键形；无 frame → 原话不加字；非 selector 失败 → 不加话。修前红，修后绿。
- **线上复验**：真重启后重放 /frame 裸 selector → 步级错误带完整提示（sessions/verify-r4.log W1）。
- **影响面**：web_act 步级错误文本；wire 价格不受影响（tools/list 未动，全量 wire 测试绿）。
