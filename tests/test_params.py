"""test_params.py —— 由 4 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import pytest
from dhole_mcp.params import ArgumentRefusal
from dhole_mcp.server import MasterFetchServer
import asyncio
import inspect
import logging
import re
from dhole_mcp import reranker, search_proxy
import io
from dhole_mcp import crawl as crawl_mod
from dhole_mcp import guide
from dhole_mcp.server import _TOP_LEVEL_ARGS


# ============================================================================
# 来源：test_the_pasted_log_says_what_the_recipient_was_told.py
# ============================================================================
# 日志不许比收件人知道得多（2026-10-18 · R02-F108 · R02-127）

HOME = str(Path.home())
CWD = os.getcwd()
USER = (os.environ.get("USERNAME") or os.path.basename(HOME)).strip()
#: 这顶帽子的四种写法：真反斜杠 / 正斜杠 / 全小写 / 百分号编码（R02-125 认过的那一族）
HATS = [v for b in (HOME, CWD, USER)
        for v in (b, b.replace("\\", "/"), b.lower(),
                  b.replace("\\", "%5C").replace("/", "%2F").replace(":", "%3A")) if v]


def _carries_machine_identity(text: str) -> bool:
    return any(h in text for h in HATS)


@pytest.fixture
def usage_log(tmp_path, monkeypatch):
    """把审计出口搬到临时文件：`env_registry.value_of` 每次调用都读 env，所以钉得住，
    而且测试绝不往这台机器真实的 `~/.dhole/usage.jsonl` 里写字。"""
    target = tmp_path / "usage.jsonl"
    monkeypatch.setenv("DHOLE_USAGE_LOG", str(target))
    return target


def _rows(usage_log: Path, skip: int) -> list[dict]:
    if not usage_log.exists():
        return []
    lines = usage_log.read_text(encoding="utf-8").splitlines()
    return [json.loads(ln) for ln in lines[skip:] if ln.strip()]


async def _call_on(server: MasterFetchServer, tool: str, args: dict,
                   usage_log: Path) -> tuple[dict, list[dict]]:
    """一发调用，同时取回**收件人看到的那一封**与**日志新 append 的那几行**。

    传进来的实例就是跑这一发的实例 —— r232 头一版在这里造了个新对象，于是两棵「分支」格子
    其实走的是正常 `_dispatch`，读数绿得没有意义（§6.3 归量具，原版档案留着）。
    """
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    skip = len(_rows(usage_log, 0))
    transport = InMemoryTransport(server._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    payload = json.loads("".join(getattr(b, "text", "") for b in res.content))
    return payload, _rows(usage_log, skip)


def _audit_error(rows: list[dict]) -> str:
    return " || ".join(str(r.get("error") or "") for r in rows)


#: 作者遮过的那几族（句子由 `parse` / `server` 的路径作者写下，两处都该是遮过的形状）
MASKED_FAMILIES = [
    ("路径不存在（家目录里的名字）",
     lambda: {"file_path": os.path.join(HOME, "r234-definitely-not-here.txt")}),
    ("受限目录守卫（家目录里的孩子）",
     lambda: {"file_path": os.path.join(HOME, ".ssh", "id_ed25519")}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("label,make", MASKED_FAMILIES)
async def test_the_masked_families_are_masked_on_both_outlets(label, make, usage_log):
    """作者遮过的那几族：日志不许留着一份遮前的句子（R02-127 被改口的那一句就是它）。"""
    payload, rows = await _call_on(MasterFetchServer(), "doc_parse", make(), usage_log)
    receipt = str(payload.get("error") or "")
    logged = _audit_error(rows)
    assert receipt and logged, (label, payload, rows)
    assert not _carries_machine_identity(receipt), (label, receipt[:120])
    assert not _carries_machine_identity(logged), (
        f"{label}：收件人这面已经遮过，日志那面却裸着 —— 两个出口两位作者。{logged[:120]}")


@pytest.mark.asyncio
@pytest.mark.parametrize("label,make", MASKED_FAMILIES)
async def test_the_log_writes_the_recipients_own_words(label, make, usage_log):
    """日志那一格必须是收件人 `error` 里出现过的字 —— 它不许自己另装配一句。

    这条比「两处都遮」更硬：它放行不了「日志在出口再套一层自己的遮蔽器」那种修法
    （套出来的句子收件人没见过，就不是镜像）。
    """
    payload, rows = await _call_on(MasterFetchServer(), "doc_parse", make(), usage_log)
    logged = _audit_error(rows)
    assert logged in str(payload.get("error") or ""), (label, logged[:150])


@pytest.mark.asyncio
async def test_a_refusal_raised_before_the_envelope_is_mirrored_too(usage_log):
    """不经信封那两条分支同样只是镜像：参数拒绝支与兜底 Exception 支各造一颗真抬的格子。

    分支取的是 `str(e)`，信封取的是同一颗 `str(e)`（前者只多加 `invalid_request: ` 抬头，
    后者截到 300），所以日志永远排在收件人后面或与他齐平 —— 这一颗用**真抬起来**的句子钉，
    message 里故意放着家目录的根。
    """
    leaky = f"a library exploded while opening {os.path.join(HOME, 'private', 'note.txt')}"
    for boom in (ArgumentRefusal(leaky, next_action="rewrite it."), RuntimeError(leaky)):
        server = MasterFetchServer()

        async def raising(*_a, **_k):
            raise boom

        server._dispatch = raising
        payload, rows = await _call_on(
            server, "doc_parse", {"file_path": os.path.join(HOME, "x.txt")}, usage_log)
        logged = _audit_error(rows)
        receipt = str(payload.get("error") or "")
        assert logged and logged in receipt, (type(boom).__name__, logged[:150])
        assert not (_carries_machine_identity(logged)
                    and not _carries_machine_identity(receipt)), type(boom).__name__


@pytest.mark.asyncio
async def test_the_two_outlets_move_with_one_author(usage_log, monkeypatch):
    """归属锁（F93）：把作者的遮蔽器换成空转，两个出口必须**一起**变裸。

    两处各自遮一遍的两副面孔在这里露馅 —— 那会是一个动、一个不动。
    """
    from dhole_mcp import paths as P

    monkeypatch.setattr(P, "mask_machine_paths", lambda text: text)
    payload, rows = await _call_on(
        MasterFetchServer(), "doc_parse",
        {"file_path": os.path.join(HOME, "r234-definitely-not-here.txt")}, usage_log)
    receipt = str(payload.get("error") or "")
    logged = _audit_error(rows)
    assert _carries_machine_identity(receipt), receipt[:150]
    assert _carries_machine_identity(logged), (
        f"收件人已经裸着而日志还遮着 ⇒ 日志有第二位作者：{logged[:150]}")
    assert logged in receipt, logged[:150]


@pytest.mark.asyncio
async def test_what_the_caller_typed_himself_still_comes_back_in_both(usage_log):
    """调用方自己打出去的路径两处都原样回来（F106 的例外）—— 这把锁不许把它遮掉。

    它要钉的是「镜像」，不是「遮」：把这条也遮了就是把收件人的证据改成了假话，
    而日志只是跟着他看到的那一封。
    """
    typed = os.path.join(HOME, "x.txt")
    payload, rows = await _call_on(MasterFetchServer(), "doc_parse",
                                   {"file_path": typed, "offset": typed}, usage_log)
    receipt = str(payload.get("error") or "")
    logged = _audit_error(rows)
    assert receipt.startswith("invalid_request:") and "offset" in receipt, receipt[:150]
    assert _carries_machine_identity(receipt), "调用方自己打的那颗路径不该被这把锁遮掉"
    assert logged in receipt, logged[:150]
    assert _carries_machine_identity(logged), logged[:150]


@pytest.mark.asyncio
async def test_the_log_still_records_the_call(usage_log, tmp_path):
    """正控制：锁不许把审计修成静默 —— 拒答那一发照样有一行在册且带着那句话。

    `ok` 的意思是「这一发跑完了没抬异常」，所以一次拒答是 `ok: true` **加** `error`
    （`audit._log_tool_call` 的 docstring 立的就是这个加法语义：`ok` 不改含义，失败的话
    搭在 `error` 里走）。这一颗要挡的是另一种退步：行还在、`error` 没了 —— 那正是
    `_result_error` 当初被加进来的原因（一次被 SSRF 守卫拒掉的 `feed_read` 记成了一条
    看着像成功的行）。
    """
    payload, rows = await _call_on(
        MasterFetchServer(), "doc_parse",
        {"file_path": os.path.join(HOME, "r234-definitely-not-here.txt")}, usage_log)
    assert len(rows) == 1, rows
    assert rows[0].get("tool") == "doc_parse"
    assert "error" in rows[0], f"审计被修成静默了：{rows[0]}"
    assert rows[0]["error"] in str(payload.get("error") or ""), rows[0]

    doc = tmp_path / "r234-readable.txt"
    doc.write_text("AAA1 BBB2 CCC3 DDD4 EEE5\n" + "w" * 60, encoding="utf-8")
    ok_payload, ok_rows = await _call_on(MasterFetchServer(), "doc_parse",
                                         {"file_path": str(doc)}, usage_log)
    assert not ok_payload.get("error"), ok_payload.get("error")
    assert len(ok_rows) == 1 and "error" not in ok_rows[0], ok_rows


# ============================================================================
# 来源：test_the_action_door_says_its_action_never_ran.py
# ============================================================================
# 动作那一扇门被形状顶回来时，要说"这一发动作没跑过"（R02-087 · P3）。

ACT_URL = "https://action-door.invalid/"
BAD_SHAPES = [
    ("selector 是对象", [{"click": {"selector": {"nested": 1}}}]),
    ("键名不存在", [{"teleport": {"selector": "#x"}}]),
    ("fill 收 text 不收 value", [{"fill": {"selector": "#x", "value": "v"}}]),
]


def _act(actions) -> str:
    """读这一格交回的话。`_dispatch` 对动作形状**抛** `ArgumentRefusal`（不是交回带 error 的
    载荷 —— 把它翻成载荷的是 MCP 那一层），所以两种形状都要读：抛出的那颗句子与 next_action
    就在异常对象上。F75 那把记过一次同族（判据读错了交付形状），这是第二次。
    """
    from dhole_mcp.params import ArgumentRefusal
    srv = MasterFetchServer()
    try:
        out = asyncio.run(srv._dispatch("web_act", {"url": ACT_URL, "actions": actions}))
        text = out[0][0].text
    except ArgumentRefusal as e:
        return str(e) + " " + (getattr(e, "next_action", "") or "")
    try:
        o = json.loads(text)
    except Exception:
        return text
    return (o.get("next_action") or "") + " " + (o.get("error") or "")


class TestTheActionDoorSaysItNeverRan:

    @pytest.mark.parametrize("why,actions", BAD_SHAPES)
    def test_a_refused_action_says_nothing_ran(self, why, actions):
        text = _act(actions)
        assert "Nothing was run." in text, f"{why}：{text[:220]}"
        for wrong in ("Nothing was fetched.", "Nothing was sent."):
            assert wrong not in text, f"{why}：动作门上说了别扇的话 {wrong}：{text[:220]}"

    def test_the_shape_advice_it_travels_with_stays(self):
        """换了申报口径，不许把那条真出路一起弄丢（sys_guide 那一指是本句的全部用处）。"""
        text = _act(BAD_SHAPES[0][1])
        assert "sys_guide(topic='behavior'" in text, text[:240]
        assert "Page interaction" in text, text[:240]


class TestTheOtherDoorsKeepTheirWords:
    """反向锁：第三种词不许把前两扇带跑 —— 这三格是 F77 那把的成果，不是本把的靶子。"""

    def test_the_write_door_still_says_sent(self):
        srv = MasterFetchServer()
        out = asyncio.run(srv._dispatch("web_request", {
            "url": "https://write-door.invalid/", "method": "HEAD",
            "headers": {"Host": "elsewhere.invalid"}}))
        text = out[0][0].text
        assert "Nothing was sent." in text, text[:220]
        assert "Nothing was run." not in text, text[:220]

    def test_the_read_door_still_says_fetched(self):
        srv = MasterFetchServer()
        out = asyncio.run(srv._dispatch("web_read", {
            "url": "https://read-door.invalid/", "headers": {"Host": "x.invalid"}}))
        text = out[0][0].text
        assert "Nothing was fetched." in text, text[:220]
        assert "Nothing was run." not in text, text[:220]


class TestTheSentenceHasOneAuthor:

    def test_the_action_way_out_is_not_a_hardcoded_constant_anymore(self):
        """漏分岔的来路之一就是常量：句子必须从助手取词。"""
        import dhole_mcp.server as m
        assert not isinstance(getattr(m, "_ACTION_REFUSAL_WAY_OUT", None), str), (
            "还是模块常量 —— 常量没法按门取词，这就是这一句在动作门上说 fetched 的来路")
        fn = m._action_refusal_way_out
        # 只看**代码里的字面量**，不看 docstring —— 那一处 docstring 在引用被修的原文
        # （F77 的同一把尺：判据不许把"解释这句话从哪来"当成"手抄了这句话"）。
        consts = [c for c in fn.__code__.co_consts
                  if isinstance(c, str) and c != fn.__doc__]
        for c in consts:
            assert "Nothing was" not in c, f"函数里手抄了句子口径：{c[:60]!r}"
        src = inspect.getsource(fn)
        assert "_nothing_went_out(" in src

    def test_the_helper_names_three_doors_not_two(self):
        from dhole_mcp.server import _nothing_went_out
        src = inspect.getsource(_nothing_went_out)
        assert '"actions"' in src, "动作门的分岔判据没在助手里"
        assert "door" in inspect.getsource(_nothing_went_out).splitlines()[0], (
            "动作车道上 _TOOL 是 web_read（web_act 在 web_read 的实现里跑），"
            "所以门名要能由调用点交进来")
        assert _nothing_went_out() in ("Nothing was fetched.", "Nothing was sent.",
                                      "Nothing was run.")


# ============================================================================
# 来源：test_a_refusal_sentence_says_where_the_key_lives_not_which_version.py
# ============================================================================
# 交出去的话里不许带版本号：三处句子只说"现在在哪儿"，不说"哪一版改的"。

# 与语料门同一族的形状：`1x.y` 后面不接时长、不接 IP 段（那条正则在册
# `tests/test_the_corpus_carries_no_maintainer_history.py:27`，这里只取版本那一项）。
VERSION_TALK = re.compile(r"(?<![\d.])1[0-9]\.[0-9xX]+(?![\d.s])")


def _options_refusal_text(options: dict | None = None) -> str:
    """把沈序那一发的句子取出来 —— 直接问写这句话的那位作者。

    `_reject_unknown_args(tool, args)` 就是 `server.py:4277-4303` 那三行 raise 的作者；
    从工具面绕一圈反而拿不到（校验在调用分发那一层）。
    """
    from dhole_mcp.server import _reject_unknown_args
    try:
        _reject_unknown_args("web_read", {"url": "http://x/",
                                          "options": options or {"retries": 3}})
    except ArgumentRefusal as e:
        return str(e)
    return ""


class TestTheOptionsRefusalStaysVersionFree:

    def test_no_version_token_in_the_refusal(self):
        text = _options_refusal_text()
        assert "options" in text, f"这一发本来就该点名 options：{text[:200]}"
        assert not VERSION_TALK.search(text), (
            f"拒绝话术里出现了版本号，调用方不在那一版时这句是假话：{text[:300]}")

    def test_the_key_still_says_where_it_lives(self):
        """去掉版本叙述之后，处方必须一寸不少：键在顶层、不要装进对象、支持集点名。"""
        text = _options_refusal_text()
        low = text.lower()
        assert "top-level" in low or "top level" in low, text[:300]
        assert "supported" in low, text[:300]

    def test_the_keys_that_can_move_out_are_still_named(self):
        """`leftover` 那一支（本次袋子里哪些键能原样搬出去）是本刀之前就在的信息，不许退。"""
        text = _options_refusal_text({"max_content_chars": 500})
        assert "max_content_chars" in text, text[:300]


class TestTheLegacyRerankerNoticeStaysVersionFree:

    def test_warning_names_what_runs_and_not_which_version_changed(self, monkeypatch,
                                                                    caplog):
        monkeypatch.setattr(reranker, "_legacy_selection", lambda: "m3-old-choice")
        with caplog.at_level(logging.WARNING):
            reranker.active_model()
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "m3-old-choice" in text, f"遗留值必须还在（它是要被删的那颗文件）：{text[:200]}"
        assert reranker.DEFAULT_MODEL in text, text[:200]
        assert not VERSION_TALK.search(text), (
            f"警告里写了哪一版删的：{text[:240]}")
        assert "delete" in text.lower(), f"出路（删掉那个文件）不许丢：{text[:240]}"

    def test_no_warning_when_the_config_matches_the_only_model(self, monkeypatch, caplog):
        """反面锁：没有遗留分歧时不许凭空说一句。"""
        monkeypatch.setattr(reranker, "_legacy_selection", lambda: reranker.DEFAULT_MODEL)
        with caplog.at_level(logging.WARNING):
            reranker.active_model()
        assert not VERSION_TALK.search(" ".join(r.getMessage() for r in caplog.records))
        assert "reranker.json" not in " ".join(r.getMessage() for r in caplog.records)


class TestTheProxyPoolNoticeStaysVersionFree:

    def test_one_line_of_proxies_says_what_it_does_now(self, monkeypatch, caplog):
        monkeypatch.setattr(search_proxy.env_registry, "value_of",
                            lambda name: "http://127.0.0.1:1/,http://p2:80"
                                          if name == "DHOLE_SEARCH_PROXY" else "")
        with caplog.at_level(logging.WARNING):
            got, source = search_proxy.configured_proxy()
        text = " ".join(r.getMessage() for r in caplog.records)
        assert got and "127.0.0.1" in got, f"第一颗照样要用：{got!r}"
        assert "127.0.0.1" in text or "first" in text.lower(), text[:240]
        assert not VERSION_TALK.search(text), (
            f"警告里写了哪一版删的池子：{text[:240]}")
        assert "rotat" in text.lower() or "first" in text.lower(), (
            f"「不轮换、只用第一颗」这一句信息量不许丢：{text[:240]}")
        assert source == "DHOLE_SEARCH_PROXY"


class TestTheLoopbackAndTheUAareNotCollateral:

    def test_loopback_and_pip_and_ua_tokens_survive(self):
        """判据问的是句子，不是"有没有点号"：这三种数字不是维护史。"""
        assert not VERSION_TALK.search("http://127.0.0.1:9222")
        assert not VERSION_TALK.search("Dhole-Sitemap/8.0")
        assert not VERSION_TALK.search('python -m pip install -e ".[all]"')
        assert not VERSION_TALK.search("~1.5-2.5s")


# ============================================================================
# 来源：test_advice_never_names_a_knob_the_named_door_refuses.py
# ============================================================================
# 建议不许教人传一颗**那扇门根本不收**的名字（R02-082 · P2）。

#: 只在响应里活着、两扇门的 wire 面都不收的一颗名字（R02-082 的那一位）。
RESPONSE_ONLY = "discover_only"

_TOOL_NEAR = re.compile(r"\bweb_(read|crawl|discover|search|act|request|resolve)\b")


def _names_the_door_wrong(lines, label):
    """"提到某扇 web_ 门之后要求传 discover_only=" 的片段。"""
    bad = []
    for where, text in lines:
        for m in re.finditer(rf"\b{RESPONSE_ONLY}\s*=", text):
            window = text[max(0, m.start() - 90):m.start()]
            hit = _TOOL_NEAR.findall(window)
            if hit:
                bad.append(f"{label} {where}: 提到 web_{hit[-1]} 之后要求传 {RESPONSE_ONLY}=")
    return bad


class TestTheAdviceNeverNamesAnUnreachableSwitch:

    def test_the_map_advice_in_crawl_does_not(self):
        """两处地图建议串不许再把调用方送去 `web_crawl(discover_only=…)`。

        只扫建议散文这一种形状：`discover_only=True` 作为内部 kwarg 在这份文件里合法地出现几十次
        —— 按裸名字扫就是本次缺陷描述里那条"grep 一次就误判"的同款陷阱。
        """
        src = io.open(crawl_mod.__file__, encoding="utf-8").read().splitlines()
        hits = _names_the_door_wrong(list(enumerate(src, 1)), "crawl.py")
        assert not hits, "地图上那句 discover_only 在门外走不通：\n" + "\n".join(hits)

    def test_the_handbook_does_not(self):
        """`sys_guide` 送出去的每一卷都不许教一颗两扇门都不收的名字。"""
        lines = []
        for path in sorted(guide._ROOT.rglob("*.md")):
            for i, line in enumerate(io.open(path, encoding="utf-8").read().splitlines(), 1):
                lines.append((f"{path.name}:{i}", line))
        hits = _names_the_door_wrong(lines, "手册")
        assert not hits, "手册在教一件真机上做不到的事：\n" + "\n".join(hits)

    def test_the_wire_descriptions_do_not(self):
        """常驻层那份（工具描述与每颗参数的描述）同一位作者，同一把尺。"""
        lines = []
        for d in MasterFetchServer._TOOL_DEFS:
            lines.append((d["name"], d.get("description") or ""))
            for key, v in (d.get("inputSchema", {}).get("properties") or {}).items():
                lines.append((f"{d['name']}.{key}", v.get("description") or ""))
        hits = _names_the_door_wrong(lines, "wire")
        assert not hits, "\n".join(hits)


class TestThePremiseIsStillTrue:
    """前提：那颗名字今天确实进不了这两扇门。前提变了要改规则，不许留一条防空气的守卫。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool", ["web_crawl", "web_discover"])
    async def test_dispatch_refuses_it_on_both_doors(self, tool):
        """真机读数与这里必须同一种形状：dispatch 是**抛**拒绝，不是回一颗 error 字段。

        （r84 量到的是 `_reject_unknown_args` 冒出的 `ArgumentRefusal`；本把这格第一次写成
        "读 payload 的 error"，于是它把一次正确的拒绝读成了一次失败 —— 记 C 类，改成按异常判。）
        """
        from dhole_mcp.params import ArgumentRefusal

        srv = MasterFetchServer()
        with pytest.raises(ArgumentRefusal) as got:
            await srv._dispatch(tool, {"url": "https://example.invalid/",
                                       RESPONSE_ONLY: False})
        assert RESPONSE_ONLY in str(got.value), (
            f"{tool} 的拒绝没点名 {RESPONSE_ONLY}：这一格的判据要跟着改")
        assert RESPONSE_ONLY not in _TOP_LEVEL_ARGS[tool], \
            f"{tool} 的 dispatch 名单里又出现了 {RESPONSE_ONLY}"
