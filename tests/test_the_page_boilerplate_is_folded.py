"""页内重复样板行折叠（boilerplate.py + 抽取链 + 信封）。

边界：只折叠"逐行完全相等、≥24 字、出现 ≥2 次、非 markdown 结构行、不在代码
围栏内"的行；保留首次出现，删掉的条数必须出现在信封上（静默删内容比留着样板更糟）。
"""

from __future__ import annotations

import pytest

from dhole_mcp.boilerplate import collapse_repeated_lines
from dhole_mcp.context import _BOILERPLATE_LINES_COLLAPSED
from dhole_mcp.extractor import extract_content
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _with_agent_hints
from dhole_mcp.wire import _wire_dump

URL = "https://shop.example.com/page"
NAV = "Home | About | Contact | Privacy Policy"


@pytest.fixture(autouse=True)
def _reset_count():
    token = _BOILERPLATE_LINES_COLLAPSED.set(0)
    yield
    _BOILERPLATE_LINES_COLLAPSED.reset(token)


class TestThePureFunction:

    def test_a_repeated_paragraph_keeps_only_the_first(self):
        text = f"# Title\n\n{NAV}\n\nReal one here.\n\n{NAV}\n\nReal two here."
        out, n = collapse_repeated_lines(text)
        assert n == 1
        assert out.count(NAV) == 1
        assert "Real one here." in out and "Real two here." in out

    def test_it_is_idempotent(self):
        text = f"{NAV}\n\n{NAV}"
        out, _ = collapse_repeated_lines(text)
        assert collapse_repeated_lines(out) == (out, 0)

    def test_short_lines_are_left_alone(self):
        assert collapse_repeated_lines("Read more\n\nx\n\nRead more") == (
            "Read more\n\nx\n\nRead more", 0)

    def test_structural_lines_are_left_alone(self):
        for line in (f"# {NAV}", f"- {NAV}", f"1. {NAV}", f"| {NAV} |",
                     f"> {NAV}"):
            text = f"{line}\n\n{line}"
            assert collapse_repeated_lines(text)[1] == 0, line

    def test_lines_inside_a_code_fence_are_left_alone(self):
        text = f"```\n{NAV}\n```\n\n```\n{NAV}\n```"
        assert collapse_repeated_lines(text)[1] == 0

    def test_no_repeat_and_empty_return_unchanged(self):
        assert collapse_repeated_lines("") == ("", 0)
        assert collapse_repeated_lines("one\n\ntwo") == ("one\n\ntwo", 0)


def _page(html: str):
    class FakePage:
        status = 200
        body = html.encode("utf-8")
        encoding = "utf-8"
        url = URL
        headers = {"content-type": "text/html"}
    return FakePage()


class TestTheExtractionChain:

    def test_markdown_folds_in_page_boilerplate_and_counts_it(self):
        html = (f"<html><body><h1>Title</h1>"
                f"<p>Real paragraph one that is long enough to keep.</p>"
                f"<p>{NAV}</p>"
                f"<p>Real paragraph two that is long enough to keep.</p>"
                f"<p>{NAV}</p></body></html>")
        out = extract_content(_page(html), extraction_type="markdown")
        assert "\n".join(out).count(NAV) <= 1
        assert _BOILERPLATE_LINES_COLLAPSED.get() >= 1

    def test_html_mode_is_not_folded(self):
        html = f"<html><body><p>{NAV}</p><p>{NAV}</p></body></html>"
        extract_content(_page(html), extraction_type="html")
        assert _BOILERPLATE_LINES_COLLAPSED.get() == 0


class TestTheEnvelope:

    def _hinted(self, text: str) -> ResponseModel:
        return _with_agent_hints(ResponseModel(
            url=URL, status=200, content=[text], content_ok=True))

    def test_the_count_rides_the_envelope_and_the_summary(self):
        _BOILERPLATE_LINES_COLLAPSED.set(2)
        out = self._hinted("Ordinary body text.")
        assert out.duplicate_lines_collapsed == 2
        assert "duplicate_lines_collapsed" in out.summary

    def test_a_clean_response_leaves_the_field_absent_on_the_wire(self):
        _BOILERPLATE_LINES_COLLAPSED.set(0)
        dumped = _wire_dump(self._hinted("Ordinary body text."))
        assert "duplicate_lines_collapsed" not in dumped
