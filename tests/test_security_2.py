"""test_security_2.py —— 由 7 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import json
import pytest
import dhole_mcp.security as sec
from dhole_mcp.context import nothing_went_out
from dhole_mcp.security import SecurityError, validate_url
from dhole_mcp.server import MasterFetchServer
from dhole_mcp import security
from dhole_mcp.security import PRIVATE_ADDRESS_RECOVERY
from dhole_mcp.security import _METADATA_HOSTS, allow_private_scope
from dhole_mcp.security import redact_api_key
from dhole_mcp.server import _identity_value_error


# ============================================================================
# 来源：test_a_guard_refusal_wears_the_same_envelope.py
# ============================================================================
# SSRF 解析器守卫的那句拒绝，六张脸里有五张不穿信封（评审 R02-113 · P3）。

TARGET = 'http://example.com/r113'
VERBS = ('fetched', 'sent', 'run', 'searched', 'stored')


class _PublicVerdict:
    """把守卫的**两个输入**钉死：进程判词 public + 解析答案是一颗隧道段地址。

    第一版只钉判词，留 `_resolves_to_internal` 去问这台机器的 DNS —— 那一跑红得没道理：
    同一颗 `example.com` 在 r172（06:07Z）答 `fd00::9d`，两分钟后这格答的是可路由地址，
    于是门放行、真发了一发 404。判据的**前提**不能挂在会变的机器状态上（这把尺量的是信封，
    不是这台机器此刻的解析器）。所以这里把解析答案也钉成一颗固定隧道地址：门仍然自己走
    `validate_url` 的那条支路（判决与拒绝都是真的产品代码），只有它的两个输入是受控的。
    """

    ANSWER = '198.18.0.99'

    def __enter__(self):
        self._cache = sec._fake_ip_cache
        self._probe = sec.probe_fake_ip
        self._resolve = sec._resolves_to_internal
        # `_DNS_CHECK_CACHE` 也要一起收：`validate_url` 会把解析判决按主机缓存几分钟，
        # 覆写期间留下的那颗"内网"会漏给后面真的问 DNS 的测试（合跑时把
        # `test_audit_fixes.py` 两格带红，单跑却绿 —— 顺序依赖就是这么来的）。
        self._dns_cache = dict(sec._DNS_CHECK_CACHE)
        sec._fake_ip_cache = ('public', '', 'forced by ruler')
        sec.probe_fake_ip = lambda *a, **k: ('public', '', 'forced by ruler')
        sec._resolves_to_internal = lambda hostname: self.ANSWER
        return self

    def __exit__(self, *exc):
        sec._fake_ip_cache = self._cache
        sec.probe_fake_ip = self._probe
        sec._resolves_to_internal = self._resolve
        sec._DNS_CHECK_CACHE.clear()
        sec._DNS_CHECK_CACHE.update(self._dns_cache)
        return False


async def _face(tool: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    from dhole_mcp.server import MasterFetchServer
    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    return json.loads(''.join(getattr(b, 'text', '') for b in res.content))


DOORS = [('web_read', {'url': TARGET}),
         ('web_discover', {'url': TARGET}),
         ('web_crawl', {'url': TARGET}),
         ('web_request', {'url': TARGET, 'method': 'POST', 'body': 'x', 'confirm': True}),
         ('web_act', {'url': TARGET, 'actions': [{'read': {}}]})]


class TestTheGuardSpeaksInTheEnvelope:

    @pytest.mark.asyncio
    @pytest.mark.parametrize('tool,args', DOORS, ids=[d[0] for d in DOORS])
    async def test_the_guard_refusal_wears_the_envelope(self, tool, args):
        with _PublicVerdict():
            d = await _face(tool, args)
        err = d.get('error') or ''
        na = d.get('next_action') or ''
        clause = nothing_went_out(tool)
        assert sec.RESOLVER_GUARD_NEEDLE in err, f'守卫那一族没在这扇说话：{err[:160]}'
        assert err.startswith('invalid_request:'), f'缺前缀，按前缀分类的分支读不到：{err[:160]}'
        assert na.startswith(clause), f'{tool} 的门话不在行首：{na[:170]}'
        wrong = [v for v in VERBS if ('nothing was ' + v) in na.lower()
                 and ('nothing was ' + v) != clause.lower().rstrip('.')]
        assert not wrong, f'{tool} 的申报说了别门的动词 {wrong}：{na[:170]}'
        assert 'fake-ip' in na or 'dhole --doctor' in na, f'出路被收紧掉了：{na[:170]}'

    @pytest.mark.asyncio
    async def test_the_gate_still_refuses_after_the_wording_change(self):
        """收紧信封不许松动闸门：同一颗地址仍然必须被拒、内容一格不许有。"""
        with _PublicVerdict():
            d = await _face('web_read', {'url': TARGET})
        assert d.get('status') == 0, json.dumps(d, ensure_ascii=False)[:200]
        assert not (d.get('content') or []), '守卫放行了 —— 这一族只许改说法'
        diag = d.get('diagnosis') or {}
        assert diag.get('code') == 'resolver_guard', diag
        assert diag.get('retryable') is False, diag


class TestTheFeedDoorKeepsTheWayOut:
    """`feed_read` 的出路表（server.py:11610-11620）只登记了两族，其余掉进通用形状提示。

    半径量在 `r178_feed_way_out_table.json`（档案 `sessions/P02-R55-feed-way-out-table.jsonl` · 五族）：
    私网与超时两族**有**专属出路（表内），守卫族与云元数据族被
    `Pass urls=[…] with 1-50 absolute http(s) RSS/Atom feed URLs.` 顶掉（表外）；
    URL 形状族落在那个提示里是**对的**（那一发的出路本来就是 urls= 的形状）。
    前缀与本门申报五族都在 —— 缺的只是第三件。这一族就是 R02-096（出路在教一件本来就对的事）
    在同一张表上的复发。
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize('urls,needle,way_out', [
        (['http://example.com/f'], 'resolver guard', 'fake-ip'),
        (['http://169.254.169.254/latest/meta-data/'], 'permanent boundary', 'design boundary'),
    ], ids=['守卫族', '云元数据族'])
    async def test_the_way_out_survives_the_feed_envelope(self, urls, needle, way_out):
        body = _PublicVerdict() if needle == 'resolver guard' else None
        if body:
            body.__enter__()
        try:
            d = await _face('feed_read', {'urls': urls})
        finally:
            if body:
                body.__exit__()
        na = d.get('next_action') or ''
        err = d.get('error') or ''
        assert err.startswith('invalid_request:'), err[:150]
        assert na.startswith(nothing_went_out('feed_read')), na[:150]
        assert 'Pass urls=[...]' not in na, (
            f'{needle} 的出路被通用形状提示顶掉了：{na[:180]}')
        assert way_out in na, f'出路没说出那一族真正的路：{na[:180]}'

    @pytest.mark.asyncio
    async def test_a_badly_shaped_url_list_keeps_the_shape_hint(self):
        """反向锁：形状族落在那个提示里是对的，修表不许把它也"特殊化"。"""
        d = await _face('feed_read', {'urls': ['not a url at all']})
        na = d.get('next_action') or ''
        assert 'Pass urls=[...]' in na, na[:180]


# ============================================================================
# 来源：test_a_refusal_that_quotes_what_the_caller_actually_sent.py
# ============================================================================
# R02-018 · 拒绝话术里 `got:` 那一格：它承诺引用调用方发出去的东西，却印着解析的残片。
# 重名消歧：_face → _face__a_refusal_that_quotes_wh_61d0

_ANCHOR = "Only http and https URLs are supported, got:"


def _face__a_refusal_that_quotes_wh_61d0(url: str) -> str:
    with pytest.raises(SecurityError) as got:
        validate_url(url)
    return str(got.value)


# ---- 那一格必须印出调用方发出去的东西 --------------------------------------

@pytest.mark.parametrize("url", [
    "上一条里最新的那篇",
    "the latest one from before",
])
def test_a_pronoun_input_quotes_what_the_caller_actually_sent(url):
    """题面那两发：空格里要有货，而且货是**他那串字**。"""
    text = _face__a_refusal_that_quotes_wh_61d0(url)
    assert text.split(_ANCHOR)[1].strip(), text
    assert url in text, text


@pytest.mark.parametrize("url,why", [
    ("example.com/page", "有 host 没 scheme —— 最常见的那一发"),
    ("//example.com/x", "协议相对写法"),
    ("/tmp/report.pdf", "本地路径当 URL 发过来"),
])
def test_a_schemeless_input_says_the_thing_that_is_missing(url, why):
    """不许只印一串引号：没有 scheme 这件事要由这句话自己说出口。"""
    text = _face__a_refusal_that_quotes_wh_61d0(url)
    assert "no scheme" in text, (why, text)
    assert url in text, (why, text)


# ---- 解析器读出来的东西要写明是**读出来的** --------------------------------

def test_a_host_port_is_not_relabelled_as_a_scheme():
    """实测的第二张脸：`localhost:8399/x` 从前印 `got: localhost`。

    那既不是空也不是对的 —— 它把 host 说成 scheme。修之后输入原文必须在场，
    而那词要说这是解析读出来的结果，不是对输入的判断。
    """
    text = _face__a_refusal_that_quotes_wh_61d0("localhost:8399/x")
    assert "localhost:8399/x" in text, text
    assert "as read from" in text, text
    assert text.split(_ANCHOR)[1].strip() != "localhost", (
        "裸一个 host 在 got: 后面，读起来还是像在说 scheme")


def test_a_real_scheme_is_still_named():
    """既有能力不许退化：`mailto:` 那一发从前就印对了，今天还得印得出名字。"""
    text = _face__a_refusal_that_quotes_wh_61d0("mailto:grants@example.org")
    assert "mailto" in text, text
    assert "grants@example.org" in text, text


# ---- 引用别人输入时要付的三张成本 ------------------------------------------

def test_a_long_input_is_quoted_up_to_a_bound_and_says_so():
    """回执不是缓冲区：引用有帽，而帽要**说出口**。"""
    url = "https-not-used/" + "a" * 4_000
    text = _face__a_refusal_that_quotes_wh_61d0(url)
    assert len(text) < 400, text[:200]
    assert "not quoted" in text or "more chars" in text, text


def test_the_quote_escapes_what_could_forge_a_line():
    """换行与控制字符不许原样上路（日志伪造 / 回执折行）。"""
    text = _face__a_refusal_that_quotes_wh_61d0("上一条\nFAKE: only http and https URLs are supported")
    assert "\n" not in text, repr(text)
    assert "\\n" in text, repr(text)


def test_a_key_inside_the_input_does_not_ride_the_quote():
    """引用调用方的输入不等于把他的凭据抄进回执与日志：走既有 redaction。"""
    secret = "sk-" + "a" * 24
    text = _face__a_refusal_that_quotes_wh_61d0(f"example.com/page?api_key={secret}")
    assert secret not in text, text
    assert "[API_KEY_REDACTED]" in text, text


# ---- 不越界：安全边界与别人的句子 ------------------------------------------

@pytest.mark.parametrize("url,keeps", [
    ("file:///C:/Windows/win.ini", "URL scheme 'file' is not allowed"),
    ("javascript:alert(1)", "URL scheme 'javascript' is not allowed"),
    ("http://evil.com\\@127.0.0.1/", "backslash"),
    ("http://127.0.0.1/x", "private"),
    ("", "non-empty string"),
])
def test_the_safety_boundaries_did_not_move(url, keeps):
    """§12：不许为了让句子好看而放宽 robots/私网/凭据/解析那一族的拒绝。"""
    assert keeps.lower() in _face__a_refusal_that_quotes_wh_61d0(url).lower(), _face__a_refusal_that_quotes_wh_61d0(url)


def test_one_author_for_the_whole_family():
    """装配这一格的作者只许有一位：`got:` 在 src 里出现一次。"""
    import inspect

    import dhole_mcp.security as sec
    src = inspect.getsource(sec)
    assert src.count("URLs are supported, got:") == 1, src.count(
        "URLs are supported, got:")


def test_the_next_action_still_owns_the_prescription():
    """一句处方只有一个作者：error 这一格只说看到了什么，怎么改仍是 next_action 的活。"""
    from dhole_mcp import server as srv
    import inspect
    body = inspect.getsource(srv)
    assert "takes an absolute http(s) URL" in body
    assert "takes an absolute http(s) URL" not in _face__a_refusal_that_quotes_wh_61d0("上一条里最新的那篇")


def test_the_manual_says_the_quote_is_the_callers_own_string():
    from dhole_mcp import guide
    corpus = "\n".join(p.read_text(encoding="utf-8")
                       for p in guide._ROOT.rglob("*.md"))
    # 两句都得在手册里活着：读手册的 agent 要分得出「没有 scheme」与「解析把某词读成
    # scheme」这两种形状，而那正是这一格从前说不出话的地方。
    assert "no scheme in" in corpus, "手册没写这一格今天引用的是什么"
    assert "' as read from '" in corpus or "as read from" in corpus
    assert "your own argument, quoted back" in corpus, "手册没说引用有帽、有 redaction"


# ============================================================================
# 来源：test_a_control_character_in_a_url_is_named_at_the_door.py
# ============================================================================
# 一颗控制字符进了 URL，门口要说它在那儿，而不是让解析与传输各自替它"修好"（评审 R02-099 · P3）。

CLEAN = "http://example.com/r146x"
BAD = {
    "LF": "http://example.com/r146\nx",
    "TAB": "http://example.com/r146\tx",
    "CR": "http://example.com/r146\rx",
    "VT": "http://example.com/r146\x0bx",
    "NUL": "http://example.com/r146\x00x",
    "DEL": "http://example.com/r146\x7fx",
}
SPACE = "http://example.com/r146 x"


class TestTheDoorNamesTheCharacter:
    def test_a_clean_url_still_walks_through_unchanged(self):
        assert validate_url(CLEAN) == CLEAN

    @pytest.mark.parametrize("name", sorted(BAD))
    def test_the_door_refuses_a_control_character(self, name):
        with pytest.raises(SecurityError) as got:
            validate_url(BAD[name])
        msg = str(got.value)
        assert "control character" in msg.lower(), msg
        assert "U+" in msg, f"要说得出是哪一颗字符的码位：{msg}"

    @pytest.mark.parametrize("name", sorted(BAD))
    def test_the_refusal_never_echoes_a_raw_control_character(self, name):
        """引用输入要转义：原样带一颗换行上路，等于让一次调用伪造回执的一行。"""
        with pytest.raises(SecurityError) as got:
            validate_url(BAD[name])
        raw = str(got.value)
        assert "\n" not in raw and "\r" not in raw and "\t" not in raw and "\x00" not in raw, repr(raw)

    def test_a_space_is_still_a_space_not_a_refusal(self):
        """对照锁：量到的待遇是空格编码后目标保留；不许把这一把的收紧扩成"不许有空白"。"""
        assert validate_url(SPACE) == SPACE

    def test_the_quoted_neighbourhood_is_redacted_and_capped(self):
        """引用调用方的输入不等于把他的凭据抄进回执 —— 走既有 redaction（同族的第三张成本）。

        这一格是 §14 教出来的：第一版只报码位与偏移，`test_a_refusal_that_quotes_what_the_caller_
        actually_sent` 那条既有契约判"读者手里没有可改的东西"，于是补了上下文引用 —— 补了引用，
        就必须同批付上转义、帽、redaction 三张成本，缺一张就是换了个新漏法。
        """
        secret = "sk-" + "a" * 24
        with pytest.raises(SecurityError) as got:
            validate_url(f"http://example.com/{secret}\nx")
        msg = str(got.value)
        assert secret not in msg, msg[:200]
        assert "[API_KEY_REDACTED]" in msg, msg[:220]
        assert "\n" not in msg and "\\n" in msg, repr(msg)[:200]


class TestTheReceiptsSpeakFromTheirOwnDoor:
    @pytest.mark.asyncio
    async def test_web_read_refuses_before_any_request_goes_out(self):
        r = await MasterFetchServer()._dispatch("web_read", {"url": BAD["LF"]})
        rows = r[1] if isinstance(r, tuple) else r
        d = rows if isinstance(rows, dict) else json.loads(rows[0].text)
        assert str(d.get("error", "")).startswith("invalid_request:")
        assert (d.get("next_action") or "").startswith("Nothing was fetched")
        assert "control character" in str(d.get("error", "")).lower()
        assert d.get("status") == 0

    @pytest.mark.asyncio
    async def test_the_search_door_says_it_sent_nothing_itself(self):
        """同一颗值在另一扇门上要说另一扇门的动词 —— 不许由读门代答（R02-098/100 那条轴）。"""
        r = await MasterFetchServer().web_discover(url=BAD["LF"], max_pages=1)
        d = r.model_dump()
        assert str(d.get("error", "")).startswith("invalid_request:")
        na = d.get("next_action") or ""
        assert na.startswith("Nothing was fetched") or na.startswith("Nothing was mapped"), na[:160]
        assert "control character" in str(d.get("error", "")).lower(), str(d.get("error"))[:160]


class TestTheGatesDidNotMove:
    def test_the_backslash_rule_is_untouched(self):
        with pytest.raises(SecurityError) as got:
            validate_url("http://example.com\\..\\etc")
        assert "backslash" in str(got.value).lower(), str(got.value)

    def test_the_private_address_rule_is_untouched(self):
        with pytest.raises(SecurityError) as got:
            validate_url("http://127.0.0.1:8399/")
        assert "127.0.0.1 in 127.0.0.0/8" in str(got.value), str(got.value)

    def test_query_sanitization_is_a_different_value_and_stays(self):
        """搜索词那一族的清洗（security 的 strip control chars）不是 URL 的路径，本把不碰。"""
        from dhole_mcp.security import validate_search_query
        assert validate_search_query("hello\x01 world").replace(" ", "") != ""


# ============================================================================
# 来源：test_a_refused_address_names_the_address_not_the_url_shape.py
# ============================================================================
# 拒一个内网地址，要说的是地址，不是 URL 的拼法（2026-10-08 真实使用模拟 · 刀3）。

_PRIVATE = ["http://127.0.0.1:8080/admin", "http://10.0.0.1/", "http://[::1]/",
            "http://0.0.0.0:8765/x", "http://localhost:6379/"]


async def _envelope(tool: str, args: dict) -> dict:
    out = await MasterFetchServer()._dispatch(tool, args)
    rows = out[1] if isinstance(out, tuple) else out
    text = rows[0].text if not isinstance(rows, dict) else json.dumps(rows)
    return json.loads(text)


class TestAPrivateAddressSaysSo:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tool,pack", [
        ("web_read", lambda u: {"url": u}),
        ("web_request", lambda u: {"url": u, "method": "HEAD"}),
        ("web_act", lambda u: {"url": u, "actions": [{"snapshot": True}]}),
    ])
    @pytest.mark.parametrize("url", _PRIVATE)
    async def test_the_advice_names_the_address_not_the_url_shape(self, tool, pack, url):
        d = await _envelope(tool, pack(url))
        assert "internal" in d["error"], d
        na = d["next_action"]
        assert "absolute http(s) URL" not in na, (
            f"又在教一条本来就写对的 URL 怎么写：{na[:120]}")
        assert "allow_private" in na, "本机服务那扇真正的门要在建议里"
        assert PRIVATE_ADDRESS_RECOVERY in na, "这句话全仓只许有一份"

    @pytest.mark.asyncio
    async def test_a_crawl_refusal_does_not_offer_a_retry(self):
        """一个包都没发的拒绝，不许说"换个参数再试"。"""
        d = await _envelope("web_crawl", {"url": "http://127.0.0.1:9/"})
        na = d.get("next_action", "")
        assert "retry with different parameters" not in na, na[:160]
        assert "allow_private" in na, na[:160]

    @pytest.mark.asyncio
    async def test_discover_and_feed_read_quote_the_same_sentence(self):
        for tool, args in (("web_discover", {"url": "http://127.0.0.1:9/"}),
                           ("feed_read", {"urls": ["http://127.0.0.1:9/f.xml"]}),
                           ("web_read", {"url": "http://127.0.0.1:9/"})):
            d = await _envelope(tool, args)
            blob = json.dumps(d, ensure_ascii=False)
            assert PRIVATE_ADDRESS_RECOVERY in blob, f"{tool} 还在自己编这句话"


class TestTheMetadataFamilyIsNamedByAddress:

    def test_an_ipv4_mapped_metadata_address_says_metadata(self):
        """泛泛的 "private IP" 抹掉了它是谁 —— 任何拼法都不放行这件事要靠话说清楚。"""
        with pytest.raises(SecurityError) as boom:
            security.validate_url("http://[::ffff:169.254.169.254]/latest/meta-data/")
        assert "metadata" in str(boom.value), str(boom.value)

    @pytest.mark.parametrize("spelling", [
        "169.254.169.254", "0xA9FEA9FE", "2852039166", "[::ffff:169.254.169.254]"])
    def test_no_spelling_of_the_metadata_address_gets_an_allowlist_exit(self, spelling):
        from dhole_mcp import identity
        identity.set_value("allow_private", [spelling.strip("[]")])
        try:
            host = f"[{spelling}]" if spelling.startswith("[") else spelling
            with pytest.raises(SecurityError):
                security.validate_url(f"http://{host}/latest/meta-data/")
        finally:
            identity.set_value("allow_private", None)


class TestATypedUrlStillGetsUrlAdvice:

    @pytest.mark.asyncio
    async def test_a_malformed_url_keeps_the_shape_lesson(self):
        d = await _envelope("web_read", {"url": "not-a-url-at-all"})
        assert "http(s)" in d["next_action"], (
            "真拼错的 URL 不该被换成一段内网教程")


# ============================================================================
# 来源：test_the_aliyun_imds_literal_slips_past_the_ip_gate.py
# ============================================================================
# 云元数据字面量 100.100.100.200 从 IP 门溜走 —— 「元数据永不放行」在默认路径不成立。

IMDS_SPELLINGS = [
    "http://100.100.100.200/",
    "http://100.100.100.200/latest/meta-data/instance-id/",
    "http://1684301000/",  # decimal integer -> 100.100.100.200
    "http://0x646464c8/",  # hex integer -> 100.100.100.200
]


@pytest.mark.parametrize("url", IMDS_SPELLINGS)
def test_the_aliyun_imds_literal_is_refused_in_every_spelling(url):
    with pytest.raises(SecurityError, match="metadata"):
        validate_url(url)


def test_the_bare_metadata_hostname_is_refused_too():
    with pytest.raises(SecurityError, match="metadata"):
        validate_url("http://metadata/latest/meta-data/")


@pytest.mark.parametrize("url", IMDS_SPELLINGS)
def test_naming_the_imds_in_the_allowlist_does_not_open_it(url):
    with allow_private_scope(["100.100.100.200"]):
        with pytest.raises(SecurityError, match="metadata"):
            validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://metadata/",
        "http://[fd00:ec2::254]/latest/meta-data/",
        "http://100.100.100.200/latest/meta-data/",
    ],
)
def test_every_metadata_endpoint_member_is_refused_by_the_default_path(url):
    assert len(_METADATA_HOSTS) == 5  # 新成员进清单时，这张表跟着长
    with pytest.raises(SecurityError):
        validate_url(url)


# ============================================================================
# 来源：test_two_host_lines_make_the_parser_read_the_first_one_like_the_os.py
# ============================================================================
# 同域名两行 hosts 记录，解析器读的是最后一行而 OS 读第一行 —— 黑洞钉位可被翻成元数据钉位。

@pytest.fixture
def hosts_parser(tmp_path, monkeypatch):
    sp = __import__("dhole_mcp.security", fromlist=["_hosts_file_pin"])

    def install(content: str):
        hosts = tmp_path / "hosts"
        hosts.write_text(content, encoding="utf-8")
        monkeypatch.setattr(sp, "_hosts_file_path", lambda: str(hosts))
        monkeypatch.setattr(sp, "_hosts_cache", None)
        return sp

    return install


def test_two_host_lines_make_the_parser_read_the_first_one_like_the_os(hosts_parser):
    sp = hosts_parser(
        "0.0.0.0 dup.evil.test\n"
        "169.254.169.254 dup.evil.test\n"
    )
    assert sp._hosts_file_pin("dup.evil.test") == "0.0.0.0"


def test_the_blackhole_pin_verdict_survives_a_second_line(hosts_parser):
    """0.0.0.0 钉位在前、元数据在后的文件，复验层把它读成黑洞而不是元数据。"""
    from dhole_mcp.security import _resolves_to_internal
    hosts_parser(
        "0.0.0.0 blackholed.tracker.test\n"
        "169.254.169.254 blackholed.tracker.test\n"
    )
    internal = _resolves_to_internal("blackholed.tracker.test")
    assert internal, "hosts 钉位 0.0.0.0 应被读成 internal（黑洞判定生效）"


def test_single_line_and_multi_alias_behavior_is_untouched(hosts_parser):
    sp = hosts_parser(
        "# comment line\n"
        "127.0.0.1   pinned.example  SECOND.example.\n"
        "0.0.0.0 ad.example # trailing comment\n"
        "malformed-line\n",
    )
    assert sp._hosts_file_pin("pinned.example") == "127.0.0.1"
    assert sp._hosts_file_pin("second.example") == "127.0.0.1"
    assert sp._hosts_file_pin("AD.EXAMPLE") == "0.0.0.0"
    assert sp._hosts_file_pin("not-pinned.example") is None


# ============================================================================
# 来源：test_a_malformed_auth_type_echoes_nothing_it_was_given.py
# ============================================================================
# cred_defaults 的错误面是全库唯一不过 redact 的 —— auth.type 原文无界回显，cookies 传错类型直接砸穿。

def test_a_malformed_auth_type_echoes_nothing_it_was_given():
    err = _identity_value_error("auth", {"type": "Bearer ALICE-TOKEN-abc123"})
    assert "ALICE-TOKEN" not in err
    assert err  # 仍是可行动的形状错误
    assert "bearer" in err and "basic" in err and "header" in err


def test_cookies_that_cannot_be_iterated_return_the_shape_error():
    err = _identity_value_error("cookies", 42)
    assert err.startswith("cookies:")
    assert "nothing usable" in err


@pytest.mark.asyncio
async def test_the_handler_returns_the_shape_error_not_a_transport_crash():
    """cookies=42 曾经砸穿 handler，落到 :7650 的 300 字符裸兜底。"""
    server = MasterFetchServer()
    r = await server.cred_defaults(set={"cookies": 42, "host": "api.example.com"})
    assert r.error
    # 评审 R02-102 之后这一扇的门口拒绝统一带 `invalid_request:`（R02-097 立的信封契约，
    # 从前这一扇是漏网的那一扇）。原句 `startswith("cookies:")` 守的是"回执开头就在点名被拒的
    # 那颗键、不是裸兜底 repr" —— 这份意思照守，只是名字跟着前缀往后挪了一格。
    assert r.error.startswith("invalid_request: cookies:"), r.error
    assert r.next_action  # 形状错误自带走向，不是裸兜底


@pytest.mark.asyncio
async def test_the_cred_defaults_error_face_passes_redact():
    """全库不变式：错误面全量过 redact —— cred_defaults 不再是唯一例外。"""
    server = MasterFetchServer()
    r = await server.cred_defaults(set={"auth": {"type": "sk-tinyfish-abcdef123456abcdef"}})
    assert r.error
    assert r.error == redact_api_key(r.error)
    assert "sk-tinyfish-abcdef123456abcdef" not in r.error
