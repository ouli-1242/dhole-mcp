"""B5-14b：`web_act` —— 页面动作从 web_read 里搬出来（§4 第 2 行，第 17 颗工具）。

读的那扇门上挂着 `actions` 是 17.0 之前最别扭的一处：一条动作链强制浏览器层、绕开
缓存、改的是页面的状态，而 web_read 的整个设计（缓存、升级梯子、归档兜底、`urls=`
批量）都假设它碰到的东西不会变。搬出来之后，读的门只剩读，写的门能把自己那三条规矩
说清楚：snapshot 先数、submit 先问、验证码三次就转交。

一处与 §4 的**有意偏离**，钉在这里也写在描述里：施工图给 web_act 留 7 个名字，这里
落 11 个。多出来的四颗（`session_id` / `cdp_url` / `confirm` / `check_login`）不是
装饰，是"在不交出一个密码的前提下动作"这条能力本身 —— 命名会话的 cookie 桥（recipes
里那条兜底路径）与在用户真实 Chrome 里动作（§3-16 先问再记的那道门，confirm 是门的
一部分，check_login 是它唯一的验真手段）。把它们删了，这扇门就只能动公开页面：那不是
拆小，那是拆掉。反过来没有的（`urls` / `cache_ttl` / `force_fetcher` / `schema` /
`offset` / `include_*` / `pages`）也都是有意的：一颗要么与"一次动作属于一个页面"
矛盾，要么与"动作按定义就是浏览器层、且不碰缓存"矛盾。
"""

from __future__ import annotations

import asyncio
import inspect
import json

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer

URL = "https://example.com/feed"
CHAIN = [{"scroll": 2}, {"click": "button.load-more"}]


def _defs() -> dict:
    return {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}


def _capture_read(monkeypatch, **call):
    """走一次真实分发，抓读的那条管线实际收到的参数（动作链只能由它执行）。"""
    seen: dict = {}

    async def fake(self, url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        from dhole_mcp.server import ResponseModel
        return ResponseModel(url=url, status=200, content=["body"],
                             fetcher_used="stealthy")

    monkeypatch.setattr(MasterFetchServer, "web_read", fake)
    args = {"url": URL, "actions": CHAIN}
    args.update(call)
    asyncio.run(MasterFetchServer()._dispatch("web_act", args))
    return seen


def _dispatch(tool: str, args: dict) -> dict:
    _content, structured = asyncio.run(MasterFetchServer()._dispatch(tool, args))
    return structured


class TestTheToolExists:

    def test_it_is_registered_with_the_names_it_can_honour(self):
        assert "web_act" in _defs(), sorted(_defs())
        assert server_mod._TOP_LEVEL_ARGS["web_act"] == frozenset({
            "url", "actions", "extraction_type", "max_content_chars", "focus",
            "timeout", "anti_detect", "session_id", "cdp_url", "confirm",
            "check_login"})

    def test_both_required_names_are_on_the_schema(self):
        schema = _defs()["web_act"]["inputSchema"]
        assert schema["required"] == ["url", "actions"]
        assert set(schema["properties"]) == set(server_mod._TOP_LEVEL_ARGS["web_act"])

    def test_it_is_a_write_on_the_wire_but_not_the_irreversible_one(self):
        """readOnly/idempotent 都是 False；destructive 留给 submit 那颗门去管。

        动作链会改页面，但真正不可逆的那一步（submit）不带 confirm 就暂停 —— 客户端
        要按"每次都要问"来标它，就会把 scroll 也一起问一遍，那道门的设计意义全没了。
        """
        ann = _defs()["web_act"]["annotations"]
        assert ann["readOnlyHint"] is False and ann["idempotentHint"] is False
        assert ann["destructiveHint"] is False and ann["openWorldHint"] is True

    def test_the_three_rules_came_along_with_the_parameter(self):
        """搬工具不许搬走规矩：snapshot 先数 / submit 先问 / 验证码三次就转交。"""
        desc = _defs()["web_act"]["description"]
        for needle in ('"snapshot"', "PAUSES", "at most 3", "hand off",
                       "never from cache", "metadata.actions"):
            assert needle in desc, needle

    def test_the_door_it_points_at_exists(self):
        assert "sys_guide(topic='behavior', section='Page interaction')" \
            in _defs()["web_act"]["description"]


class TestWhatItRequires:

    def test_a_missing_chain_says_what_is_missing_and_stops(self, monkeypatch):
        seen = _capture_read(monkeypatch, actions=None)
        out = _dispatch("web_act", {"url": URL})
        blob = json.dumps(out).lower()
        assert not seen, "没有动作链却已经把页面动了一遍"
        assert "required" in blob and "actions" in blob, out
        assert "typeerror" not in blob, "缺必填炸成栈异常，等于把契约换了个形状"

    def test_an_empty_chain_refuses_too(self, monkeypatch):
        seen = _capture_read(monkeypatch, actions=[])
        out = _dispatch("web_act", {"url": URL, "actions": []})
        assert not seen, "空链也发出去了"
        assert "actions" in json.dumps(out).lower(), out

    def test_a_missing_url_refuses_before_anything_is_sent(self, monkeypatch):
        seen = _capture_read(monkeypatch, url="")
        out = _dispatch("web_act", {"actions": CHAIN})
        assert not seen and "url" in json.dumps(out).lower(), out


class TestEveryParameterArrives:
    """11 颗名字逐颗到达到管线 —— 少转一颗就是能力在 wire 上不存在。"""

    def test_the_chain_and_the_url_are_forwarded_by_name(self, monkeypatch):
        seen = _capture_read(monkeypatch)
        assert seen["url"] == URL and seen["actions"] == CHAIN, seen

    def test_an_action_call_never_reads_the_cache(self, monkeypatch):
        """链后的正文只在动作之后存在：从缓存答就等于答了一个没被问过的页面。"""
        seen = _capture_read(monkeypatch)
        assert seen["cache_ttl"] == 0, seen

    @pytest.mark.parametrize("key,value", [
        ("extraction_type", "html"), ("max_content_chars", 1234),
        ("focus", "quotes"), ("timeout", 45000), ("anti_detect", "off"),
        ("session_id", "sess1"), ("cdp_url", "http://127.0.0.1:9222"),
        ("confirm", True), ("check_login", True),
    ])
    def test_each_optional_name_reaches_the_pipeline(self, monkeypatch, key, value):
        seen = _capture_read(monkeypatch, **{key: value})
        assert seen.get(key) == value, seen

    def test_an_unsent_name_is_absent_not_none(self, monkeypatch):
        """没传的键不许被递成 None —— 那会盖掉管线自己那个非 None 的默认。"""
        seen = _capture_read(monkeypatch)
        for key in ("extraction_type", "focus", "anti_detect", "session_id",
                    "cdp_url"):
            assert key not in seen or seen[key] is None, key


class TestTheReadSideLostTheChain:

    def test_web_read_no_longer_advertises_actions(self):
        props = _defs()["web_read"]["inputSchema"]["properties"]
        assert "actions" not in props
        assert "actions" not in server_mod._TOP_LEVEL_ARGS["web_read"]
        assert "actions" not in _defs()["web_read"]["description"]

    def test_the_old_spelling_names_the_new_door(self):
        with pytest.raises(ValueError, match="Unsupported argument") as e:
            _dispatch("web_read", {"url": URL, "actions": CHAIN})
        msg = str(e.value)
        assert "actions" in msg and "web_act" in msg, msg

    def test_the_dispatcher_cannot_smuggle_a_chain_back_into_the_read(self):
        """调度器不许把 `actions` 悄悄塞回 web_read：那条通道一开，两扇门又是一扇。

        形状而不是单词 —— 这一段里"actions"作为散文出现是正常的（元素截图那颗
        注记就在讲它），被**传下去**才是问题。
        """
        src = inspect.getsource(MasterFetchServer._dispatch)
        branch = src.split('if name == "web_read":')[1].split("elif name ==")[0]
        for shape in ("actions=", '"actions"', "'actions'"):
            assert shape not in branch, (shape, branch)

    def test_the_pipeline_still_takes_the_chain(self):
        """公开面搬走，管线不动：web_act 走这一条。

        拆门的意义是"一扇门上不说两套规矩"，不是把执行链拆成两条 —— 真拆了，回放和
        登录流就各自长出一套动作语义。
        """
        assert "actions" in inspect.signature(MasterFetchServer.web_read).parameters
        assert "actions" in inspect.signature(MasterFetchServer.web_act).parameters


class TestARungIsNamedOrRefused:
    """第五档不存在，所以".ultra"要被拒并列出那四颗 —— 而不是炸一个 KeyError。

    14b 拆出这颗门的时候踩过一次：rung 的归一化写在 `name == "web_read"` 那一个
    分支里，web_act 转发的值没人问就进了 `_ANTI_DETECT[level]`。 Literal 只存在于
    schema，读参数的还是这台服务器自己。
    """

    @pytest.mark.parametrize("tool", ["web_read", "web_act"])
    def test_a_fifth_rung_is_refused_naming_the_four(self, tool):
        """问这一颗的是 `_validate_tool_args` —— 那才是 wire 上真的那道门。

        Literal 只到客户端为止；`_dispatch` 是门后面的分发器，测试打在门上才是
        客户端会碰到的行为。
        """
        with pytest.raises(ValueError, match="must be one of") as exc:
            server_mod._validate_tool_args(tool, {"anti_detect": "ultra"})
        msg = str(exc.value)
        for rung in ("off", "basic", "auto", "aggressive"):
            assert rung in msg, (rung, msg)

    def test_the_rung_list_has_one_home(self):
        """名单只有一份：加了会读档位的工具，参数面与那道门一起跟上。"""
        for tool in server_mod._RUNG_TOOLS:
            assert "anti_detect" in server_mod._TOP_LEVEL_ARGS[tool], tool

    @pytest.mark.parametrize("tool,base", [
        ("web_read", {"url": URL}),
        ("web_act", {"url": URL, "actions": [{"wait": 1}]}),
    ])
    def test_an_absent_rung_stays_absent_so_the_stored_one_can_answer(self, tool, base):
        """`anti_detect` 缺席不等于 "auto"：缺席才是"用 cred_defaults 存的那一档"。"""
        seen = {}

        async def fake_read(self, url, **kwargs):
            seen.update(kwargs)
            from dhole_mcp.server import ResponseModel
            return ResponseModel(url=url, status=200, content=["body"],
                                 fetcher_used="http")

        monkey = pytest.MonkeyPatch()
        try:
            monkey.setattr(MasterFetchServer, "web_read", fake_read)
            asyncio.run(MasterFetchServer()._dispatch(tool, base))
        finally:
            monkey.undo()
        assert seen.get("anti_detect", None) is None, seen


class TestScreenshotPixelsReachTheWire:
    """web_act 的截图像素必须真的到 content —— 回执只留 `image: true` 这个名字。

    web_act 把链委托给 web_read 执行，`_report_action_outcomes` 在那里把 base64
    从回执挪进 `_action_images`（PrivateAttr，不进 JSON），像素再往前一步变成
    ImageContent 是 dispatch 层的活。那条线此前只接在 web_read 分支上 —— web_act
    分支把它们丢在了原地：回执说「有图」，客户端却什么都看不到。真实协议层才测得
    到（单元层一直只看回执形状，而这根管子在 dispatch）。2026-10-05 那次真实使用
    全真模拟第一次过 wire 时抓到。
    """

    def test_pixels_ride_the_content_list(self, monkeypatch):
        from mcp.types import ImageContent

        async def fake_act(self, url, actions=None, **kwargs):
            from dhole_mcp.server import ResponseModel
            result = ResponseModel(url=url, status=200, content=["body"],
                                   fetcher_used="stealthy")
            result._action_images = [{"data": "cGl4ZWw=", "mime_type": "image/png"}]
            return result

        monkeypatch.setattr(MasterFetchServer, "web_act", fake_act)
        content, _structured = asyncio.run(MasterFetchServer()._dispatch(
            "web_act", {"url": URL, "actions": CHAIN}))
        imgs = [c for c in content if isinstance(c, ImageContent)]
        assert imgs and imgs[0].data == "cGl4ZWw=", (
            "web_act 的截图像素在 dispatch 处被丢掉 —— "
            "_wire_result 少了 images= 那一参")
