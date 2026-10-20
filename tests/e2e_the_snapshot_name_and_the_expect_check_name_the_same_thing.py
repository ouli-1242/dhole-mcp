# -*- coding: utf-8 -*-
"""snapshot 那一行写着的名字，必须就是 `expect` 拿去核对的那一个名字。

这把刀是被上一刀（`e2e_a_snapshot_does_not_name_an_element_by_its_contents.py`）
撞出来的，不是外部报告的：那一刀把 `_ENUM_JS` 里"数据型字段不取 `el.value`"改掉了，
而 `expect` 用的是**另一份** JS（`_NAME_JS`），那一份至今还留着

    if (!name && el.value !== undefined) name = String(el.value).slice(0, 120);

于是同一个元素在一条链里有两套名字，而两处都是文档写明了要用的
（`references/tools.md:29`：snapshot 给 index/tag/role/**name**，`expect` 核对的是
"the element's **visible name**"）：

· **正确的用法当场失败**（这一半是上一刀撞出来的）。`{"fill": {"index": 3, "expect":
  "手机号"}}` 是快照之后按编号操作的标准写法。字段一旦有值（预填的表单、上一轮已经填过
  的），`expect` 比的是 `13800001234`，句里那句"手机号"永远匹配不上 —— 而列表里明明就写
  着"手机号"。
· **失败信息把内容抄进上下文**（这一半在 HEAD 上就开着，与上一刀无关，实测形状见
  `test_a_mismatch_names_the_label_and_never_what_the_field_holds`：靶页密码框上
  `expect:"姓名"` 交回 `element 2 reads 'hunter2secret', expected ~'姓名'`）。那一句是
  `element {i} reads {name!r}`，而 `name` 就是 `el.value`：手机号、身份证，还有
  `<input type=password>` 里此刻打着的字。上一刀堵的是列表那一侧，这一侧是同一类泄漏
  的第二扇门。

判据的形状：

1. **一份阶梯两位读者**。名字怎么取只写一处，`_ENUM_JS` 与 `_NAME_JS` 都从它取值；
   两处各自记一遍就一定会再漂一次。
2. **`expect` 的本职是"这个编号还是不是我要的那个元素"**，用的是元素的稳定身份
   （label / placeholder / aria / 机器名）；字段里此刻打着什么字会变，不配当身份。
   要验"填进去的字此刻在页面上"走的是另一扇写明了的门（`read` 的 `property:"value"`
   与 `attribute_says`），`references/fields.md:147` 说的就是它。
3. **按钮那一侧不许退化**：`input[type=submit]` / `button` 的 value 就是它脸上的字，
   链接的 innerText 就是它的名字 —— 这两类照旧进名字，否则 `expect:"提交注册"` 反而
   从此匹配不上。
4. **非表单域一个字都不许动**：`<a>` 的 innerText 本来就是可见文字，它在正确那一侧。

跑法（chromium 不在时干净跳过，与上一刀同一约定）：
    pytest -m e2e tests/e2e_the_snapshot_name_and_the_expect_check_name_the_same_thing.py -v
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.e2e

FORM_HTML = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>注册</title></head><body>
<form method="post" action="/register">
  <label for="name">姓名</label>
  <input id="name" name="name" type="text" value="测试用户甲">
  <label for="phone">手机号</label>
  <input id="phone" name="phone" type="tel" value="13800001234">
  <label for="pw">密码</label>
  <input id="pw" name="pw" type="password" value="hunter2secret">
  <input type="hidden" name="csrf_token" value="316ddd835cfc">
  <textarea id="bio" name="bio">家住北京市西城区某号</textarea>
  <input type="submit" id="reg" value="提交注册">
</form>
<a href="/help">帮助文档</a>
</body></html>"""

# 出现在 expect 的失败信息里，就是这一族泄漏的第二扇门被留下了。
CONTENTS = ["13800001234", "测试用户甲", "hunter2secret",
            "316ddd835cfc", "家住北京市西城区某号"]


def _collect():
    """一次 chromium 会话里把两份口径与 expect 的实际反应全收集齐，交回纯数据。

    page 出不了会话（浏览器一关就废），所以每一句断言要用的东西都得在这里取完。
    """
    from dhole_mcp.actions import (INTERACTIVE_SELECTOR, _ENUM_JS, _NAME_JS,
                                   _check_expect)
    from dhole_mcp.browser import check_browser_available

    if not check_browser_available():
        pytest.skip("patchright/chromium absent - the browser tier is not installed")

    async def work(page):
        rows, _total = await page.evaluate(_ENUM_JS, INTERACTIVE_SELECTOR)
        names = []
        for row in rows:
            loc = page.locator(INTERACTIVE_SELECTOR).nth(row[0])
            names.append(str(await loc.evaluate(_NAME_JS)).strip())

        def find(pred):
            for row in rows:
                if pred(row):
                    return row[0], row[3]
            return None, None

        async def expect(index, text):
            try:
                await _check_expect(page, index, text)
                return None
            except Exception as e:  # noqa: BLE001 - 这一条测的就是失败时的措辞
                return f"{type(e).__name__}: {e}"

        probes = {}
        for key, pred, wanted in (
                ("tel", lambda r: r[5] == "tel", "手机号"),
                ("pw", lambda r: r[5] == "password", "姓名"),
                ("submit", lambda r: r[1] == "input" and r[5] == "submit", "提交注册"),
                ("link", lambda r: r[1] == "a", "帮助")):
            index, listed = find(pred)
            probes[key] = (index, listed, await expect(index, wanted))
        return {"rows": rows, "names": names, **probes}

    async def run():
        from patchright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await (await browser.new_context()).new_page()
                await page.set_content(FORM_HTML)
                return await work(page)
            finally:
                await browser.close()

    return asyncio.new_event_loop().run_until_complete(run())


@pytest.fixture(scope="module")
def both():
    return _collect()


def test_the_two_readers_of_the_ladder_return_the_same_name(both):
    """每一位读者、每一个编号：列表里写着什么，expect 就拿什么去比。"""
    rows, names = both["rows"], both["names"]
    assert rows, "fixture produced no interactive elements"
    for row, named in zip(rows, names):
        assert named[:80] == row[3], (
            f"index {row[0]} {row[1]}/{row[5]}: snapshot 说 {row[3]!r}，"
            f"expect 认 {named!r}")


def test_expect_naming_the_label_passes_on_a_field_that_already_holds_a_value(both):
    """快照之后按编号操作的写法，不许因为字段有值就当场失败。"""
    _idx, listed, err = both["tel"]
    assert listed == "手机号", both["rows"]
    assert err is None, f'expect:"手机号" 在已填字段上失败了：{err}'


def test_a_mismatch_names_the_label_and_never_what_the_field_holds(both):
    """失败信息要能告诉人"编号变了"，同时不把他刚打的字抄进上下文。"""
    _idx, _listed, err = both["pw"]
    assert err is not None, "expect 用错了名字却没有报错，这一族失效了"
    assert "密码" in err, err
    for secret in CONTENTS:
        assert secret not in err, f"失败信息泄漏了字段内容 {secret!r}：{err}"


def test_a_button_is_still_matched_by_the_text_written_on_it(both):
    """反例：收走 value 不能把按钮变成无名氏，否则 expect 从此点不动它。"""
    _idx, listed, err = both["submit"]
    assert listed == "提交注册", both["rows"]
    assert err is None, f'expect:"提交注册" 失败了：{err}'


def test_a_link_is_still_matched_by_its_own_text(both):
    """非表单域的可见文字本来就是名字，这一刀不许动它。"""
    _idx, listed, err = both["link"]
    assert listed == "帮助文档", both["rows"]
    assert err is None, f'expect:"帮助" 失败了：{err}'
