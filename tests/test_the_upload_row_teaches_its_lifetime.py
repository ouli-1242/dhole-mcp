"""R08-002：upload 行要教「文件只活在这一发的页面上」。

现场（round-08 P05）：第 1 发 upload 装文件、第 3 发提交——第 3 发是新导航新页，
`received (0 bytes)` 无任何说明；p12 反证单发组合全通。页面态不跨调用是在册
契约，但咬人处静默。

修后合同：upload 成功行带 `lifetime` 句（this page instance / same call）；
behavior 手册 upload 段补同一句。链路行为零变化。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from dhole_mcp.actions import build_page_action


class _FakeLoc:
    def __init__(self, page):
        self._page = page

    @property
    def first(self):
        return self

    async def set_input_files(self, files, **kw):
        self._page.uploads.append(list(files))


class _FakePage:
    def __init__(self):
        self.main_frame = object()
        self.frames = [self]
        self.uploads: list = []

    def set_default_navigation_timeout(self, ms):
        pass

    def set_default_timeout(self, ms):
        pass

    def locator(self, sel):
        return _FakeLoc(self)

    async def goto(self, url, **kw):
        pass

    def on(self, event, handler):
        pass

    def remove_listener(self, event, handler):
        pass


def test_upload_row_carries_lifetime_note(tmp_path):
    f = tmp_path / "audit.txt"
    f.write_text("x", encoding="utf-8")
    page = _FakePage()
    pa = build_page_action([{"upload": {"selector": "#doc", "files": [str(f)]}}])
    asyncio.run(pa(page))
    row = pa.outcomes[0]
    assert row.get("uploaded") == ["audit.txt"]
    note = row.get("lifetime") or ""
    assert "page instance" in note, note
    assert "same call" in note, note


def test_behavior_manual_upload_section_teaches_the_lifetime():
    md = Path("src/dhole_mcp/skills/dhole-web/references/tools.md")
    text = md.read_text(encoding="utf-8")
    seg = text[text.index("**Upload puts a local file into the page**"):]
    seg = seg[:seg.index("- **") if "- **" in seg[10:] else len(seg)]
    assert "page instance" in seg, "upload 段该说文件只活在这一发的页面上"
