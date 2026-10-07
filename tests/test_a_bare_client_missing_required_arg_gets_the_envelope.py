"""裸 MCP 客户端的缺参调用要拿标准 invalid_request 信封（评审R1刀2）。

现状（2026-10-07 评审，review/review_drive.py 裸 HTTP 客户端实测）：宿主按
wire schema 的 required 拦缺参调用，所以正常宿主碰不到；但
``ClientSession.call_tool`` 不做客户端校验——服务端不能假设客户端会校验。
那时 dispatch 分支里的直接读键各炸各的：

  * ``web_crawl {resume_token}``（无 url 键）→ ``args["url"]`` KeyError，
    返回体就是 ``{"error": "'url'"}``——KeyError repr 当 error 文本，无信封
    无 next_action；
  * ``web_discover {}`` → ``TypeError: missing required positional argument``；
  * ``web_search`` / ``doc_parse`` 同款 KeyError；
  * ``web_crawl {url: ""}`` → 空串不被拒，静默落进 plan 校验链。

契约（本文件钉住）：
  * 缺席或空串的必需键 → 标准 invalid_request 信封：error 说缺哪个、列出
    该工具接受的全部参数名（与「多余参数」那条错误同款式），next_action
    说传什么、重试；一个请求都不发。
  * 表里的四颗之外零变化：web_read 的 required 是 anyOf（dispatch 里已有
    专检），web_act / web_request / feed_read 的方法体自带"缺什么、去哪
    补"的更优信封，不进这张表。
  * 表与 wire 的 required 声明不漂移：凡 schema 声明 required、又不是自检
    信封的工具，必须在表里。
"""

from __future__ import annotations

import json

import pytest

from dhole_mcp.server import MasterFetchServer, _DISPATCH_REQUIRED, _TOP_LEVEL_ARGS


def _text(result) -> str:
    content, _payload = result
    return content[0].text


class TestABareClientMissingRequiredArgGetsTheEnvelope:

    @pytest.mark.asyncio
    async def test_web_crawl_without_url_is_an_envelope_not_a_keyerror(self):
        out = await MasterFetchServer()._dispatch("web_crawl",
                                                  {"resume_token": "x:0"})
        payload = json.loads(_text(out))
        err = payload["error"]
        assert err.startswith("invalid_request"), err
        assert err != "'url'", "KeyError repr 不能再当 error 文本"
        assert "url" in err and "Missing required argument" in err
        assert "resume_token" in err, "error 列出该工具接受的参数名"
        assert "no request was made" in payload["next_action"].lower()

    @pytest.mark.asyncio
    async def test_web_discover_without_url_is_an_envelope_not_a_typeerror(self):
        out = await MasterFetchServer()._dispatch("web_discover", {})
        text = _text(out)
        assert "invalid_request" in text
        assert "TypeError" not in text
        assert "url" in text and "Missing required argument" in text

    @pytest.mark.asyncio
    async def test_web_search_without_query_is_an_envelope(self):
        out = await MasterFetchServer()._dispatch("web_search", {})
        text = _text(out)
        assert "invalid_request" in text
        assert "query" in text and "Missing required argument" in text

    @pytest.mark.asyncio
    async def test_doc_parse_without_file_path_is_an_envelope(self):
        out = await MasterFetchServer()._dispatch("doc_parse", {})
        text = _text(out)
        assert "invalid_request" in text
        assert "file_path" in text and "Missing required argument" in text

    @pytest.mark.asyncio
    async def test_an_empty_url_is_refused_before_the_plan_chain(self):
        """: empty 不静默落进 plan 校验链——同一个信封，同一个说法。"""
        out = await MasterFetchServer()._dispatch("web_crawl", {"url": "   "})
        payload = json.loads(_text(out))
        assert payload["error"].startswith("invalid_request")
        assert "url" in payload["error"]


class TestTheRequiredTableDoesNotDrift:

    def test_every_wire_required_tool_is_either_tabled_or_self_checked(self):
        schema_required = {
            td["name"] for td in MasterFetchServer._TOOL_DEFS
            if "required" in td["inputSchema"]
        }
        assert schema_required == (
            set(_DISPATCH_REQUIRED) | {"web_act", "web_request", "feed_read"}
        ), "schema 声明 required 的工具要么在 dispatch 表里，要么方法体自检信封"

    def test_a_full_argument_set_passes_the_guard(self):
        assert _DISPATCH_REQUIRED["web_crawl"] == ("url",)
        assert _TOP_LEVEL_ARGS["web_crawl"] >= {"url", "resume_token"}
