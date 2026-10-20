# -*- coding: utf-8 -*-
"""{"snapshot": true} 不许把表单里装着的那一列用户数据当成元素的名字。

一条实测（`review/05-第三轮补测与改进清单v2.md` 项 9，夹具 `review/files/register.html`）：

    snapshot.elements = [
      {index:0, name:"测试用户甲"},                  ← 姓名
      {index:1, name:"13800001234", input_type:"tel"},   ← 手机号
      {index:2, name:"110101199001010000"},              ← 身份证号
      {index:3, name:"fake.ti@example.test"},            ← 邮箱
      ...
    ]

而同一份 `web_read` 的正文里**一个都没有**（input 的 value 不是可见文字）。也就是说
泄漏面不是"页面有没有把 PII 印出来"，而是**这一份 JS 把 value 当成了 name**：

    if (!name && el.value !== undefined) name = String(el.value).slice(0, 80);

它没有区分两种完全不同的 `value`：`input[type=submit]` 的 value 是**按钮上写着的那两个字**
（不收进来，按钮就成了无名氏），而 tel / text / hidden 的 value 是**手机号、身份证、会话
token**（收进来，一次快照就把整张表最敏感的一列抄进了上下文）。场景 10 里那个 hidden 的
`316ddd835cfc` 是同一机制命中的另一类数据。

判据的形状：

1. **能力没有收走，只是换了一扇写明了的门**：真要取值走
   `{"read": {"selector": "#phone", "property": "value"}}` —— 那是一次**指名**的读取，
   而不是"顺手列一下页面上有什么"时的附带品。
2. **`<label for>` 优先于 `name` 属性**：人会说"手机号"，`phone` 是机器名。
3. **textarea 的 innerText 就是它的内容**，所以它和 input 同侧，不能从 innerText 走漏。
4. checkbox / radio / file 不装着自由文本，按原样处理（它们的 value 是选项码，不是数据）。

跑法（chromium 不在时干净跳过，与 `e2e_browser_tier_reads_a_pdf.py` 同一约定）：
    pytest -m e2e tests/e2e_a_snapshot_does_not_name_an_element_by_its_contents.py -v
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e

# 一份"最坏情况"的表单：每一类都故意带上可识别的值。
FORM_HTML = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>注册</title></head><body>
<form method="post" action="/register">
  <label for="name">姓名</label>
  <input id="name" name="name" type="text" value="测试用户甲">
  <label for="phone">手机号</label>
  <input id="phone" name="phone" type="tel" value="13800001234">
  <input id="idno" name="idno" type="text" value="110101199001010000">
  <label for="email">邮箱</label>
  <input id="email" name="email" type="email" value="fake.ti@example.test">
  <input type="hidden" name="csrf_token" value="316ddd835cfc">
  <textarea id="bio" name="bio">家住北京市西城区某号</textarea>
  <select id="city" name="city"><option value="bj">北京</option></select>
  <input type="submit" id="reg" value="提交注册">
</form>
</body></html>"""

# 这些串一旦出现在任何一行的 name 里，就是这次要修的那个洞。
CONTENTS = ["测试用户甲", "13800001234", "110101199001010000",
            "fake.ti@example.test", "316ddd835cfc", "家住北京市西城区某号"]


def _rows_for(html: str):
    """在真 DOM 上跑那一份 JS —— 假对象测不到它，正如 PDF 那一份说的"a fake cannot
    be trusted here"。"""
    from dhole_mcp.actions import INTERACTIVE_SELECTOR, _ENUM_JS
    from dhole_mcp.browser import check_browser_available

    if not check_browser_available():
        pytest.skip("patchright/chromium absent - the browser tier is not installed")

    async def run():
        from patchright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await (await browser.new_context()).new_page()
                await page.set_content(html)
                return await page.evaluate(_ENUM_JS, INTERACTIVE_SELECTOR)
            finally:
                await browser.close()

    import asyncio
    return asyncio.new_event_loop().run_until_complete(run())


@pytest.fixture(scope="module")
def snapshot():
    rows, total = _rows_for(FORM_HTML)
    return {r[1] + "#" + (r[5] or "") + ":" + str(i): r for i, r in enumerate(rows)}, rows


def _names(rows):
    return [r[3] for r in rows]


def test_no_row_is_named_by_what_it_contains(snapshot):
    """整列 name 里不许出现任何一个被填进去的值。"""
    _, rows = snapshot
    joined = " | ".join(_names(rows))
    for secret in CONTENTS:
        assert secret not in joined, f"snapshot 把 {secret!r} 当成了元素名"


def test_a_submit_input_is_still_named_by_its_value(snapshot):
    """反例也要钉住：按钮的 value 是它的标签，收走它按钮就成了无名氏。"""
    _, rows = snapshot
    assert "提交注册" in _names(rows), _names(rows)


def test_an_associated_label_beats_the_machine_name(snapshot):
    """人会说"手机号"，`phone` 是机器名 —— 编号列表要能被念给人听。"""
    _, rows = snapshot
    tel = [r for r in rows if r[5] == "tel"]
    assert tel and tel[0][3] == "手机号", tel


def test_a_field_without_a_label_falls_back_to_its_name_attribute(snapshot):
    """没 <label> 时至少说得出这是哪个字段，而不是它装着什么。"""
    _, rows = snapshot
    idno = [r for r in rows if r[0] and r[3] == "idno"]
    assert idno, _names(rows)


def test_a_hidden_input_does_not_surface_its_token(snapshot):
    """会话/CSRF token 从前是这一行漏出去的（场景 10 的 316ddd835cfc）。"""
    _, rows = snapshot
    hidden = [r for r in rows if r[5] == "hidden"]
    assert hidden and hidden[0][3] == "csrf_token", hidden


def test_a_textarea_leaks_nothing_through_its_inner_text(snapshot):
    """textarea 的 innerText 就是内容本身 —— 这条路也要堵。"""
    _, rows = snapshot
    ta = [r for r in rows if r[1] == "textarea"]
    assert ta and "家住" not in ta[0][3], ta


def test_the_index_and_role_columns_are_unchanged(snapshot):
    """这一刀只动 name 的取法；编号与 role 的口径不许跟着漂。"""
    _, rows = snapshot
    assert [r[0] for r in rows] == list(range(len(rows))), rows
    submit = [r for r in rows if r[5] == "submit"]
    assert submit and submit[0][2] == "button", submit
