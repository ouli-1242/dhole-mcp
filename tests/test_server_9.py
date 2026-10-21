"""test_server_9.py —— 由 9 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
import inspect
import pathlib
import pytest
from dhole_mcp import quality as quality_mod
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer
import json
import os
from dhole_mcp import paths
from dhole_mcp import context
from dhole_mcp import security
from dhole_mcp import feed as feed_mod
from dhole_mcp.server import _feed_batch_envelope
from dhole_mcp import identity
from dhole_mcp.server import ResponseModel


# ============================================================================
# 来源：test_the_way_forward_is_written_by_the_error_its_own_author.py
# ============================================================================
# 一句话服务三种形状：出路必须由写那句 `error` 的人交出来。

GENERIC_PHRASE = "the rename the error suggests"

#: OLE2 / Compound File Binary 魔数：`_sniff_cfb` 只读前 8 字节，后面填零即可。
_CFB_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512


def _parse(tmp_path, name: str, *, data: bytes | None = None,
           text: str | None = None):
    path = tmp_path / name
    if data is not None:
        path.write_bytes(data)
    else:
        path.write_text(text or "", encoding="utf-8")
    return asyncio.run(MasterFetchServer().doc_parse(file_path=str(path)))


# ─── 1 · 三种形状各由自己的作者说话 ───


@pytest.mark.parametrize("kind", ["cfb", "unsupported", "badjson"])
def test_the_verdict_positions_do_not_move(tmp_path, kind):
    r = {
        "cfb": lambda: _parse(tmp_path, "legacy.doc", data=_CFB_HEADER),
        "unsupported": lambda: _parse(tmp_path, "notes.log", text="一份普通文本"),
        "badjson": lambda: _parse(tmp_path, "broken.json", text="{not json at all"),
    }[kind]()
    assert r.status == 0, kind
    assert r.content_ok is not True, kind
    assert r.fetcher_used == "parse", kind
    assert r.error, kind


def test_the_cfb_way_forward_is_written_by_the_refusal_itself(tmp_path):
    r = _parse(tmp_path, "legacy.doc", data=_CFB_HEADER)
    assert "renaming it does not change the bytes" in r.error, r.error
    na = r.next_action or ""
    assert GENERIC_PHRASE not in na, (
        f"出路还在替本发 error 声称它建议过改名：{na!r}")
    assert "Convert it first" in na or "soffice" in na, (
        f"拒绝自己写了怎么转换，出路却没带上那一句：{na!r}")


def test_the_unsupported_extension_shares_its_author_wording(tmp_path):
    r = _parse(tmp_path, "notes.log", text="一份普通文本")
    assert "rename it to .txt" in r.error, r.error
    na = r.next_action or ""
    assert GENERIC_PHRASE not in na, na
    # 不是转述：parse 自己那句的关键词必须在出路里原样出现。
    assert ".txt" in na, f"出路把改名那句话说丢了：{na!r}"


def test_invalid_json_is_not_lectured_about_extensions(tmp_path):
    r = _parse(tmp_path, "broken.json", text="{not json at all")
    na = (r.next_action or "").lower()
    assert "extension" not in na and "rename" not in na, (
        f"坏 JSON 的出路在谈扩展名：{r.next_action!r}")
    assert "the file was found and read" in na, (
        f"通用句被删空了——调用方拿不到任何下一步：{r.next_action!r}")


# ─── 2 · 那句话在树里归零 ───


def test_the_paraphrase_has_been_erased_from_the_hint_author():
    src = inspect.getsource(quality_mod)
    assert GENERIC_PHRASE not in src, "quality.py 还在替别人的 error 说话"


def test_the_manual_carries_no_copy_of_the_paraphrase():
    root = (pathlib.Path(inspect.getsourcefile(server_mod)).resolve().parent
            / "skills" / "dhole-web")
    for path in sorted(root.rglob("*.md")):
        body = path.read_text(encoding="utf-8")
        assert GENERIC_PHRASE not in body, f"{path.name} 留着一份转述抄本"


# ─── 3 · 出路不是空字段：通用句仍在原位 ───


def test_the_fallback_sentence_still_exists_for_authors_less_failures(tmp_path):
    """没有作者的话时，通用句必须还在——但只说它真知道的那半。"""
    r = _parse(tmp_path, "broken.json", text="{not json at all")
    na = r.next_action or ""
    assert "nothing to fix about the path" in na.lower(), na
    assert "error" in na.lower(), "通用句仍要点名 error 才是那一发的判决"


# ============================================================================
# 来源：test_a_refused_path_is_not_described_as_a_file_that_was_read.py
# ============================================================================
# 被门挡住的路径，不许被描述成"文件读过了，是内容坏了"。
# 重名消歧：_parse → _parse__a_refused_path_is_not_de_207c

def _parse__a_refused_path_is_not_de_207c(**kw):
    return asyncio.run(MasterFetchServer().doc_parse(**kw))


def _windows_system_host_file():
    root = os.environ.get("WINDIR") or r"C:\Windows"
    return os.path.join(root, "System32", "drivers", "etc", "hosts")


def test_a_refused_path_says_the_path_was_refused():
    path = _windows_system_host_file()
    if os.name != "nt" or not os.path.exists(path):
        import pytest
        pytest.skip("受限系统目录的判据是 Windows 的形状")
    r = _parse__a_refused_path_is_not_de_207c(file_path=path)
    assert r.error.startswith("Access denied: path is in a restricted system directory")
    assert "PATH was refused" in r.next_action, r.next_action
    assert "Nothing to fix about the path" not in r.next_action, (
        "被拒的正是路径，却说路径没什么可修的")
    assert "found and read" not in r.next_action, (
        "一个字节都没读过，不能说我读过了")


def test_the_two_halves_of_the_receipt_agree():
    """diagnosis 与 next_action 必须给同一个下一步 —— 各说一半等于没说。"""
    path = _windows_system_host_file()
    if os.name != "nt" or not os.path.exists(path):
        import pytest
        pytest.skip("受限系统目录的判据是 Windows 的形状")
    r = _parse__a_refused_path_is_not_de_207c(file_path=path)
    recover = (r.diagnosis or {}).get("recover_with", "")
    assert "path" in recover and "path" in r.next_action.lower(), (recover, r.next_action)
    assert (r.diagnosis or {}).get("code") == "path_restricted"


def test_an_alternate_data_stream_refusal_names_the_stream(tmp_path):
    """:stream 那种形状走的是另一条拒绝，出路也不同（去掉流名，不是换目录）。

    流要**先存在**才谈得上被拒：不存在的流在路径解析那一步就 answered
    "File not found"（那也是实话，不是这条判据要测的形状）。
    """
    import pytest
    if os.name != "nt":
        pytest.skip("NTFS 备用数据流")
    marker = tmp_path / "report.txt"
    marker.write_text("正文", encoding="utf-8")
    stream_path = str(marker) + ":hidden.json"
    try:
        with open(stream_path, "w", encoding="utf-8") as f:
            f.write("{\"planted\": 1}")
    except OSError as e:                      # 非 NTFS / 卷不支持流
        pytest.skip(f"造不出备用数据流：{e}")
    r = _parse__a_refused_path_is_not_de_207c(file_path=stream_path)
    assert r.error.startswith("Access denied: path names an NTFS alternate data stream"), \
        r.error
    assert "stream" in r.next_action, r.next_action
    assert "Nothing to fix about the path" not in r.next_action


def test_a_content_failure_keeps_the_content_advice(tmp_path):
    """反向：那四种**内容**失败原本的出路一个字不许被这次修补改掉。"""
    bad = tmp_path / "broken.json"
    bad.write_text("{\"a\": ,,,}", encoding="utf-8")
    r = _parse__a_refused_path_is_not_de_207c(file_path=str(bad))
    assert r.error and "not_found" not in (r.error.lower()[:30])
    assert "found and read" in r.next_action, (
        f"内容失败被送到路径建议那边去了：{r.next_action[:160]}")
    assert "PATH was refused" not in r.next_action


def test_a_missing_file_still_names_the_paths_tried(tmp_path):
    """缺失那一支的正面锁：逐字列出被找过哪几处，且不滑进「路径被拒」。

    原本这一格只钉 `"cwd=" in next_action`，而且传的是绝对写法 —— R02-062 之后那句出路
    只有一位作者（server 按 `os.path.isabs` 写进 `error`），所以这一格改钉两件更硬的事：
    两种形状都还在这一支里（`error` 逐字列出被找过的地方）、相对那一发的 `cwd=` 一字没丢，
    而 next_action 不许把缺失说成拒绝。负向锁（"PATH was refused" 不在）原样保留。
    """
    ab = _parse__a_refused_path_is_not_de_207c(file_path=str(tmp_path / "nope.pdf"))
    rel = _parse__a_refused_path_is_not_de_207c(file_path="r02-062-refused-lock.txt")

    for r in (ab, rel):
        assert r.error.startswith("File not found"), r.error
        assert "nope.pdf" in r.error or "r02-062-refused-lock.txt" in r.error, r.error
        assert "PATH was refused" not in r.next_action, r.next_action
        assert "error" in r.next_action.lower(), r.next_action
    assert "cwd=" in rel.error, rel.error
    # 绝对那一发不许留着一扇打不开的门当出路：它必须自己说清 `cwd=` 在这发上不生效
    assert "cwd=" not in ab.error or "does nothing" in ab.error, ab.error


def test_the_guard_is_not_a_knob():
    """拒绝得说清这是门不是旋钮：没有任何参数能开它。"""
    path = _windows_system_host_file()
    if os.name != "nt" or not os.path.exists(path):
        import pytest
        pytest.skip("受限系统目录的判据是 Windows 的形状")
    r = _parse__a_refused_path_is_not_de_207c(file_path=path, cwd=os.path.dirname(path))
    assert "no argument opens it" in r.next_action, r.next_action
    # 换个参数再来一次，答案必须一样（这不是能被绕过的门）
    again = _parse__a_refused_path_is_not_de_207c(file_path=path)
    assert again.error == r.error
    assert json.dumps(again.diagnosis, sort_keys=True) == json.dumps(r.diagnosis,
                                                                    sort_keys=True)


# ============================================================================
# 来源：test_an_identity_in_a_path_is_masked_whichever_root_it_lives_on.py
# ============================================================================
# 机器身份住在根外面，也得遮（评审 R02-090 · P2）。
# 重名消歧：_parse → _parse__an_identity_in_a_path_is_4392

MASKED_USER = "<user>"
USER = os.path.basename(os.path.expanduser("~"))


def _mask(text: str) -> str:
    return paths.mask_machine_paths(text)


@pytest.fixture
def a_file_under_a_foreign_root(tmp_path):
    """一份放在「既不是 home 也不是 cwd」的根下面、且目录名里带着用户名的文件。

    名字照 pytest 自己的拼法来（`pytest-of-<user>` 就是这个形状），这样这一格量的
    是真实世界里会出现的目录，不是一个为了测试编的字符串。
    """
    d = tmp_path / f"probe-of-{USER}"
    d.mkdir()
    good = d / "report.txt"
    good.write_text("第一行\n第二行\n", encoding="utf-8")
    return good


def _parse__an_identity_in_a_path_is_4392(**kw):
    return asyncio.run(MasterFetchServer().doc_parse(**kw))


class TestTheIdentityIsMaskedWhicheverRootItLivesOn:

    def test_a_username_shaped_segment_is_masked_in_a_plain_string(self):
        out = _mask(f"/var/tmp/probe-of-{USER}/report.txt")
        assert USER not in out, out
        assert MASKED_USER in out, out

    def test_both_spellings_of_the_same_root_are_masked(self):
        win = _mask(f"D:\\Cache\\temp\\probe-of-{USER}\\gone.txt")
        posix = _mask(f"D:/Cache/temp/probe-of-{USER}/gone.txt")
        assert USER not in win + posix, (win, posix)
        assert win.replace("\\", "/").replace(MASKED_USER, "X") == \
               posix.replace(MASKED_USER, "X"), (win, posix)

    def test_the_mask_is_case_insensitive_because_windows_respells_it(self):
        out = _mask(f"C:/temp/probe-OF-{USER.upper()}/x.txt")
        assert USER.lower() not in out.lower(), out

    def test_the_receipt_from_the_parse_door_carries_no_identity(self, a_file_under_a_foreign_root):
        """成功那一格（`url`）—— D11 当年修的就是这一格，而它只遮了两个根。"""
        r = _parse__an_identity_in_a_path_is_4392(file_path=str(a_file_under_a_foreign_root))
        assert r.status == 200, r.error
        assert USER not in r.url, r.url
        assert r.url.endswith(f"{MASKED_USER}/report.txt"), r.url

    def test_the_failure_sentence_carries_no_identity(self, tmp_path):
        d = tmp_path / f"probe-of-{USER}"
        d.mkdir()
        r = _parse__an_identity_in_a_path_is_4392(file_path=str(d / "gone.txt"))
        assert r.error.startswith("File not found"), r.error
        assert USER not in r.error, r.error

    def test_the_masked_url_is_still_a_usable_uri(self, a_file_under_a_foreign_root):
        """遮法不许把 URI 弄坏：`quality` 还要拿 path 判扩展名与登录墙信号。"""
        from urllib.parse import urlparse
        r = _parse__an_identity_in_a_path_is_4392(file_path=str(a_file_under_a_foreign_root))
        parsed = urlparse(r.url)
        assert parsed.scheme == "file", r.url
        assert parsed.path.endswith("report.txt"), r.url


class TestNotOvermasking:
    """反向守卫：越遮越多不是修法。调用方自己写的路径要原样读得回来。"""

    def test_a_foreign_path_without_an_identity_is_returned_verbatim(self):
        text = "E:/somewhere/else/report.csv"
        assert _mask(text) == text, _mask(text)

    def test_a_directory_that_merely_starts_with_the_username_is_not_folded(self):
        """与 `_root_boundary` 同一条理由：`<user>project` 是另一颗目录，不是身份本身。"""
        text = f"/tmp/{USER}project/report.txt"
        assert _mask(text) == text, _mask(text)

    def test_a_file_extension_shaped_tail_is_not_folded(self):
        """`probe.<user>` 里那截是扩展名形状的另一样东西，不许顺手折掉。"""
        text = f"/tmp/data.{USER}x.gz"
        assert _mask(text) == text, _mask(text)

    def test_text_without_any_machine_shape_is_untouched(self):
        assert _mask("no paths here at all") == "no paths here at all"

    def test_an_empty_string_stays_empty(self):
        assert _mask("") == ""


class TestOneAuthorForTheRule:

    def test_the_identity_rule_lives_in_the_masker(self):
        """判据住在遮蔽器里，不在六个调用点各数一遍（F77/F80 同一条理由）。"""
        import inspect
        from dhole_mcp import parse as parse_mod
        src = inspect.getsource(parse_mod)
        assert "getpwuid" not in src and "os.environ[\"USERNAME\"]" not in src, (
            "遮蔽身份的第二位作者出现了")

    def test_the_two_roots_still_mask_as_before(self):
        """F81 不许弄坏已有的两格：home → `~`、cwd → `<server cwd>`。"""
        home = os.path.expanduser("~")
        assert paths.MASKED_HOME in _mask(f"{home}/report.csv")
        assert paths.MASKED_CWD in _mask(f"{os.getcwd()}/report.csv")
        assert home not in _mask(f"{home}/report.csv")


# ============================================================================
# 来源：test_a_shared_argument_guard_speaks_the_door_it_is_facing.py
# ============================================================================
# 共用的陌生参数闸要说本门的动词，而且要说在行首（评审 R02-110 · R02-111）。

OK_URL = 'https://example.com/'
FOREIGN = ('fetched', 'sent', 'run', 'searched', 'stored or forgotten')


async def _face(tool: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    return json.loads(''.join(getattr(b, 'text', '') for b in res.content))


def _clause_for(door: str) -> str:
    # 期望句子由**那一位作者**算出来：本文件不许手数动词（手数的判据会和文案一起漂）
    return context.nothing_went_out(door)


class TestTheSharedGuardSpeaksItsOwnDoor:

    @pytest.mark.asyncio
    @pytest.mark.parametrize('door,args', [
        ('web_read', {'url': OK_URL, 'options': {'timeout': 5}}),
        ('feed_read', {'urls': [OK_URL], 'options': {'timeout': 5}}),
        ('web_act', {'url': OK_URL, 'actions': [{'read': {}}], 'options': {'x': 1}}),
        ('web_crawl', {'url': OK_URL, 'options': {'depth': 2}}),
    ])
    async def test_the_options_bag_refusal_leads_with_this_door(self, door, args):
        d = await _face(door, args)
        na = d.get('next_action') or ''
        assert d.get('error', '').startswith('invalid_request:'), d.get('error')
        assert na.startswith(_clause_for(door)), f'{door} 的袋子拒绝行首不是本门的话：{na[:120]}'
        # 比对要**去掉句点**：`nothing_went_out()` 交的是整句（带 .），而下面按片段找动词。
        # 第一版没剥句点，于是每扇把自己门的动词也当成了外来动词 —— 三格假红，产品那边其实是对的。
        own = _clause_for(door).lower().rstrip('.')
        wrong = [v for v in FOREIGN if f'nothing was {v}' in na.lower()
                 and f'nothing was {v}' != own]
        assert not wrong, f'{door} 听见了别门的动词 {wrong}：{na[:160]}'

    @pytest.mark.asyncio
    @pytest.mark.parametrize('door,args', [
        ('web_read', {'url': OK_URL, 'actions': [{'read': {}}]}),
        ('web_read', {'url': OK_URL, 'body': 'x'}),
        ('feed_read', {'urls': [OK_URL], 'method': 'POST'}),
    ])
    async def test_a_moved_key_is_pointed_elsewhere_but_declares_here(self, door, args):
        d = await _face(door, args)
        na = d.get('next_action') or ''
        assert _clause_for(door) in na, f'{door} 的搬家键没说本门的话：{na[:160]}'
        assert na.startswith(_clause_for(door)), f'{door} 的门话不在行首：{na[:160]}'

    @pytest.mark.asyncio
    async def test_the_action_door_names_its_own_verb(self):
        """`web_act` 缺 `actions`：行动门不能替写门申报（server.py:6548 的实锤）。"""
        d = await _face('web_act', {'url': OK_URL})
        na = d.get('next_action') or ''
        assert na.startswith(_clause_for('web_act')), na[:180]
        assert 'nothing was sent' not in na.lower(), f'行动门说着写门的话：{na[:180]}'

    @pytest.mark.asyncio
    @pytest.mark.parametrize('args', [{'url': OK_URL, 'method': 'GET'}, {'url': OK_URL}])
    async def test_the_write_door_leads_with_its_own_clause(self, args):
        """`web_request` 两句自己的形状拒绝：动词本来就对，位置要从句尾搬到行首。"""
        d = await _face('web_request', args)
        na = d.get('next_action') or ''
        assert na.startswith(_clause_for('web_request')), f'门话缀在尾，只读首句的人看不见：{na[:180]}'


class TestTheRulerWasNotLoosened:
    """改文案不许把别处已有的对称拆掉。"""

    @pytest.mark.asyncio
    async def test_a_plain_unknown_key_still_speaks_its_own_door(self):
        for door, args in (('web_read', {'url': OK_URL, 'bogus_key': 1}),
                           ('web_search', {'query': 'dhole', 'bogus_key': 1}),
                           ('cred_defaults', {'bogus_key': 1}),
                           ('sys_state', {'bogus_key': 1})):
            d = await _face(door, args)
            na = d.get('next_action') or ''
            assert na.startswith(_clause_for(door)), (door, na[:140])

    def test_no_door_verb_is_welded_into_the_shared_guard(self):
        """源码形状：共用的陌生参数闸里不许再出现写死的某门动词。

        `_reject_unknown_args` 是**模块级**函数（server.py:4456），不是 MasterFetchServer 的方法 ——
        第一版按方法去取源码，AttributeError 就是那颗判据自己在猜形状。
        """
        src = inspect.getsource(server_mod._reject_unknown_args)
        for welded in ('Nothing was sent.', 'Nothing was fetched.', 'Nothing was run.'):
            assert welded not in src, f'共用闸里又焊进了 {welded!r} —— 那是又一位作者'
        assert 'nothing_went_out' in src, '共用的陌生参数闸不再从本门作者那儿取话了'


# ============================================================================
# 来源：test_one_judge_decides_which_family_a_url_refusal_is.py
# ============================================================================
# 一句守卫拒绝属于哪一族，只有一个裁判（评审 R02-117 · F100）。
# 重名消歧：_face → _face__one_judge_decides_which__e3bb

GCE = 'http://metadata.google.internal/x'
LOCAL = 'http://localhost:9/x'
META_BARE = 'http://metadata/x'
REBIND = 'http://x.nip.io/y'
PRIVATE_IP = 'http://127.0.0.1:9/ok'


async def _face__one_judge_decides_which__e3bb(tool: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    return json.loads(''.join(getattr(b, 'text', '') for b in res.content))


class TestTheClassifierIsTheOnlyJudge:

    @pytest.mark.parametrize('msg,family', [
        ('URL targets internal service: metadata.google.internal', 'permanent'),
        ('URL targets internal service: localhost', 'private'),
        ('URL targets internal service: 0.0.0.0', 'private'),
        ('URL targets internal cloud metadata endpoint: metadata', 'permanent'),
        ('URL uses DNS rebinding service: 1.2.3.4.nip.io', 'permanent'),
        ('URL targets internal/private IP (127.0.0.1 in 127.0.0.0/8)', 'private'),
        ('URL hostname x.com resolves to internal/private IP (10.0.0.1). whatever', 'resolver'),
        ('Timeout exceeds maximum of 120000ms (2 minutes)', 'timeout'),
        ("URL scheme 'ftp' is not allowed", 'shape'),
        ('URL must be a non-empty string', 'shape'),
    ])
    def test_each_sentence_gets_one_family(self, msg, family):
        assert security.url_refusal_family(msg) == family, msg

    def test_the_localhost_half_does_not_move(self):
        """这颗是本把的护栏：一句话跨两族，搬前缀会连 `localhost` 一起搬走。

        `localhost` 的出路**必须**继续指出 `allow_private` 那道门 —— 那一支真有门。
        """
        assert security.url_refusal_family('URL targets internal service: localhost') == 'private'
        assert 'allow_private' in security.PRIVATE_ADDRESS_RECOVERY


class TestTheWireReadsTheSameVerdict:

    @pytest.mark.asyncio
    @pytest.mark.parametrize('door,args', [
        ('web_read', {'url': GCE}),
        ('feed_read', {'urls': [GCE]}),
        ('web_discover', {'url': GCE}),
        ('web_crawl', {'url': GCE}),
    ])
    async def test_the_metadata_name_gets_the_boundary_way_out(self, door, args):
        d = await _face__one_judge_decides_which__e3bb(door, args)
        na = d.get('next_action') or ''
        assert security.PERMANENT_URL_RECOVERY[:44] in na, (
            f'{door} 还在把元数据名派去私网那句：{na[:150]}')
        assert 'allow_private' not in na, (
            f'{door} 在教一道不存在的门（守卫自己写着永不开元数据）：{na[:150]}')

    @pytest.mark.asyncio
    @pytest.mark.parametrize('door,args', [
        ('web_read', {'url': LOCAL}),
        ('feed_read', {'urls': [LOCAL]}),
    ])
    async def test_localhost_keeps_the_door_it_really_has(self, door, args):
        """正控制：`localhost` 那一半一个字不许变 —— 它的出路真的有门。"""
        d = await _face__one_judge_decides_which__e3bb(door, args)
        na = d.get('next_action') or ''
        assert security.PRIVATE_ADDRESS_RECOVERY[:44] in na, na[:150]
        assert 'allow_private' in na, na[:150]

    @pytest.mark.asyncio
    async def test_the_permanent_names_stay_permanent(self):
        for args in ({'url': META_BARE}, {'url': REBIND}):
            d = await _face__one_judge_decides_which__e3bb('web_read', args)
            assert security.PERMANENT_URL_RECOVERY[:44] in (d.get('next_action') or ''), args


class TestNoSecondJudgeSurvives:

    def test_the_dispatch_sites_read_the_verdict_not_the_prefix_lists(self):
        """源码形状：装配点自己扫前缀名单的那两位旧裁判要退休（句子本体仍住守卫侧）。"""
        src = inspect.getsource(MasterFetchServer._dispatch)
        assert 'PRIVATE_ADDRESS_REFUSAL_PREFIXES' not in src, (
            '门侧又在扫私网前缀表 —— 那正是 `localhost` 与元数据名被撞成一族的形状')
        assert 'url_refusal_family' in src, '门侧不再读那颗判决'

    def test_the_crawl_engine_reads_the_verdict_too(self):
        from dhole_mcp import crawl
        src = inspect.getsource(crawl)
        seg = src[src.index('def _crawl_url_guard_face'):] if 'def _crawl_url_guard_face' in src else src
        assert 'PRIVATE_ADDRESS_REFUSAL_PREFIXES' not in seg or 'url_refusal_family' in seg


# ============================================================================
# 来源：test_a_state_door_declares_its_own_sentence.py
# ============================================================================
# `sys_state` 的门话要有作者，两句都算（评审 R02-116 · F98）。
# 重名消歧：_face → _face__a_state_door_declares_it_c409

BOGUS = 'r190_bogus_key'


async def _face__a_state_door_declares_it_c409(tool: str, args: dict) -> dict:
    import json

    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport
    from dhole_mcp.server import MasterFetchServer

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    return json.loads(''.join(getattr(b, 'text', '') for b in res.content))


class TestTheDoorIsClaimed:

    def test_sys_state_is_in_the_roster(self):
        """在册 = 这扇的句子有人写过。默认支只该兜"没人认领的门"。"""
        assert 'sys_state' in context._DOOR_KIND, (
            'sys_state 的门话仍由 `_UNDECLARED_DOOR_WAY_OUT` 代答 —— 它的形状在 F97 已经归了自己，'
            '句子不能再住在"没有作者"那一支')

    def test_registering_did_not_change_what_the_caller_hears(self):
        """登记不是改口：这句话与表外默认支**逐字相同**，变的只是它有了作者。"""
        assert context.nothing_went_out('sys_state') == 'Nothing was fetched or changed.', (
            '在册之后调用方听到的句子变了 —— 这一把的判决是"两句都留着、内容一字不改"')

    @pytest.mark.asyncio
    @pytest.mark.parametrize('args', [{BOGUS: 1}, {'show': 7}, {'confirm': 'one'}])
    async def test_the_parameter_faces_lead_with_its_own_declared_sentence(self, args):
        d = await _face__a_state_door_declares_it_c409('sys_state', args)
        assert (d.get('next_action') or '').startswith(context.nothing_went_out('sys_state')), d


class TestTheErasureFaceKeepsItsSentenceWithAnAuthor:

    @pytest.mark.asyncio
    async def test_a_bad_state_class_still_says_it_erased_nothing(self):
        d = await _face__a_state_door_declares_it_c409('sys_state', {'clear': ['nosuch_class']})
        na = d.get('next_action') or ''
        assert na.startswith(context.nothing_erased()), (
            f'erasure 面的申报不再由作者装配：{na[:120]}')
        assert 'clear= takes a state class' in na, f'出路被顺手剪了：{na[:160]}'

    def test_the_declaration_is_no_longer_welded_in_server(self):
        """源码形状：申报位交给 `context`，server 里那句字面量不许还留着。"""
        src = inspect.getsource(server_mod.MasterFetchServer)
        assert 'Nothing was erased. clear=' not in src, (
            'erasure 面的申报又焊回文案里了 —— 那一处就是"同一扇两种作者"的现场')
        assert 'nothing_erased' in src, 'erasure 面不再从作者那儿取话了'

    def test_the_message_field_is_not_mass_replaced(self):
        """反向锁：其余三处 `Nothing was erased` 住的不是申报位，这一把不许顺手合并。

        第一版这一格数的是**带引号的整颗字面量** `"Nothing was erased."`，而三处里只有一处
        是那个形状（`message = "Nothing was erased."`）；另两处一个嵌在 config 那句正文里、
        一个是 `Nothing was erased by this call.` —— 于是判据自己报红（A 层，尺子的错）。
        改成数子串：**三处正文都还在**（少一处就是有人把它们一起"归给作者"了）。
        """
        src = inspect.getsource(server_mod.MasterFetchServer)
        assert src.count('Nothing was erased') >= 3, (
            f'正文里那三处只剩 {src.count("Nothing was erased")} 处 —— 它们说的是"这一发没擦掉什么"，'
            '属正文/错误文案，不属申报位')
        assert 'message = "Nothing was erased."' in src, (
            '`message` 那颗正文被换掉了：回执正文与申报句是两个格子，这一把只动后者')


class TestTheOtherDoorsAreNotTouched:

    @pytest.mark.parametrize('door,expected', [
        ('cred_defaults', 'Nothing was stored or forgotten.'),
        ('web_read', 'Nothing was fetched.'),
        ('web_act', 'Nothing was run.'),
        ('web_search', 'Nothing was searched - no engine was asked.'),
    ])
    def test_a_neighbour_sentence_does_not_move(self, door, expected):
        assert context.nothing_went_out(door) == expected, door

    def test_sys_guide_moved_on_purpose(self):
        """【原判据搬家 · F99】这一颗从前在上面的名单里，钉的是 `sys_guide` 听默认支。

        F98 把"不许由没人认领的默认支代答"立成通则，F99 就是拿它办 `sys_guide`：它从此有
        自己的句子。所以"别门不许动"的名单里它不再算别门 —— 留着那条断言就等于让上一把的
        反向锁否决这一把的修法。钉的方向不变，只是这扇换了阵营。
        """
        assert context.nothing_went_out('sys_guide') == 'Nothing was served from the handbook.'


# ============================================================================
# 来源：test_a_guide_door_refuses_in_prose_too.py
# ============================================================================
# `sys_guide` 的被拒要说它自己的话，不再借抓取的壳（评审 R02-115 · F99）。
# 重名消歧：BOGUS → BOGUS__a_guide_door_refuses_in__41f6

BOGUS__a_guide_door_refuses_in__41f6 = 'r192_bogus_key'


async def _raw(tool: str, args: dict) -> str:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport
    from dhole_mcp.server import MasterFetchServer

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    return ''.join(getattr(b, 'text', '') for b in res.content)


class TestTheGuideDoorStopsBorrowingTheFetchShell:

    @pytest.mark.asyncio
    @pytest.mark.parametrize('args', [{BOGUS__a_guide_door_refuses_in__41f6: 1}, {'topic': 7}])
    async def test_an_argument_refusal_arrives_in_prose(self, args):
        text = await _raw('sys_guide', args)
        with pytest.raises(json.JSONDecodeError):
            json.loads(text)
        for cell in ('content_ok', 'duration_ms', 'fetcher_used', '"url"'):
            assert cell not in text, f'被拒回执还带着抓取壳的 {cell}：{text[:160]}'

    @pytest.mark.asyncio
    async def test_the_unknown_argument_prose_carries_the_supported_roster(self):
        """陌生参数那一面：能照着改的支持列表必须在（R02-095 那一族的契约）。"""
        text = await _raw('sys_guide', {BOGUS__a_guide_door_refuses_in__41f6: 1})
        assert 'invalid_request:' in text, text[:160]
        assert BOGUS__a_guide_door_refuses_in__41f6 in text, f'被拒的那颗键没被点名：{text[:200]}'
        assert 'Supported' in text, f'支持列表被剪：{text[:240]}'

    @pytest.mark.asyncio
    async def test_the_wrong_type_prose_names_the_shape_instead(self):
        # 【判据自己先写窄了一颗 · F99】这一族从前也要求过 "Supported"，而类型错的句子
        # 本来就不该列名册：它要说的是这颗该写成什么（真机 r193：
        # `topic must be a string, got int (7). Pass topic as text.`）。
        # 要求名册是我把陌生参数那一面的出路套到另一面上 —— 改的是判据，不是产品。
        text = await _raw('sys_guide', {'topic': 7})
        assert 'invalid_request:' in text, text[:160]
        assert 'topic' in text, f'没点名是哪颗参数：{text[:200]}'
        assert 'must be a string' in text, f'形状那一句不见了：{text[:220]}'

    @pytest.mark.asyncio
    async def test_the_declaration_leads_the_way_out_line(self):
        """行首契约对散文门同样成立：只读第一句的人不该听见别的门的话。"""
        text = await _raw('sys_guide', {BOGUS__a_guide_door_refuses_in__41f6: 1})
        clause = context.nothing_went_out('sys_guide')
        lines = [ln for ln in text.splitlines() if ln.strip()]
        assert any(ln.startswith(clause) for ln in lines), (
            f'本门申报不在任何一行的行首：{lines[:4]}')

    def test_the_door_is_claimed_not_defaulted(self):
        """F98 那条通则的续：在册，且句子不是"没人写过的那句"。"""
        assert 'sys_guide' in context._DOOR_KIND, '仍由 `_UNDECLARED_DOOR_WAY_OUT` 代答'
        assert context.nothing_went_out('sys_guide') != context._UNDECLARED_DOOR_WAY_OUT


class TestTheRestOfTheDoorIsNotRewritten:

    @pytest.mark.asyncio
    async def test_the_unknown_topic_face_is_untouched(self):
        """这一面本来就是散文，也是这把改形的**样板**：一字不许跟着动。"""
        text = await _raw('sys_guide', {'topic': 'nosuch_topic'})
        assert text.startswith('sys_guide: topics'), text[:120]
        assert "unknown topic 'nosuch_topic'" in text
        assert text.index('aliases') > 0, '那份 topic 名单是这扇给自己开的路，不许缩'

    @pytest.mark.asyncio
    async def test_an_argument_refusal_shares_the_prose_header(self):
        """两种失败并成一种语言：抬头与 unknown-topic 那一面同一个。"""
        text = await _raw('sys_guide', {BOGUS__a_guide_door_refuses_in__41f6: 1})
        assert text.startswith('sys_guide:'), f'抬头不是这扇自己的那种：{text[:100]}'

    @pytest.mark.asyncio
    async def test_the_fetch_family_still_answers_in_json(self):
        d = json.loads(await _raw('web_read', {'url': 'https://example.com/', BOGUS__a_guide_door_refuses_in__41f6: 1}))
        for field in ('url', 'status', 'content_ok', 'next_action', 'error'):
            assert field in d, f'抓取那一族被顺手收窄了：{sorted(d)}'


# ============================================================================
# 来源：test_a_skipped_blank_url_names_itself.py
# ============================================================================
# 被滤掉的空白 URL 要**报得出是哪几颗**：数对了名字仍缺（R02-053）。

FEED = ("<?xml version='1.0'?><rss version='2.0'><channel><title>Blog</title>"
        "<item><title>One</title><link>https://blog.test/one</link>"
        "<pubDate>Tue, 14 Oct 2026 09:00:00 GMT</pubDate></item></channel></rss>")
GOOD = "https://blog.test/feed.xml"


@pytest.fixture()
def stub(monkeypatch):
    async def fake_grab(url, timeout, headers=None):
        return FEED.encode(), 200, "utf-8", "", {"content-type": "application/rss+xml"}

    monkeypatch.setattr(feed_mod, "_grab", fake_grab)


def _payload(args: dict) -> dict:
    out = asyncio.run(MasterFetchServer()._dispatch("feed_read", args))
    return out[1] if isinstance(out, tuple) and len(out) == 2 else {}


# ─── 1 · 有空白项：标量 + 点名位置 ──────────────────────────────────────────

def test_blank_entries_are_counted_and_positioned(stub):
    p = _payload({"urls": ["", GOOD, "   "], "max_items": 3})
    assert len(p["feeds"]) == 1, p["feeds"]              # 不许为跳过的那两颗编行
    assert p["skipped_blank_urls"] == 2, sorted(p)
    s = p["summary"]
    assert "positions 1, 3" in s, s
    # 算式锁：点名的、去取的、跳过的三颗数必须能对上
    assert p["skipped_blank_urls"] + len(p["feeds"]) == 3, s


def test_the_asked_number_keeps_counting_what_the_caller_named(stub):
    """`3 URLs asked` 这一格是 R02-F32 修好的，本刀不许把它改成「取了几颗」。"""
    p = _payload({"urls": [GOOD, "", ""], "max_items": 3})
    assert "1 URLs asked" not in p["summary"], p["summary"]
    assert p["summary"].startswith("3 URLs asked"), p["summary"]


def test_a_single_blank_is_named_with_one_position(stub):
    p = _payload({"urls": [GOOD, ""], "max_items": 3})
    assert p["skipped_blank_urls"] == 1, sorted(p)
    assert "position 2" in p["summary"], p["summary"]


# ─── 2 · 沉默锁：没空白项就不许多说 ─────────────────────────────────────────

def test_a_clean_batch_says_nothing_about_blanks(stub):
    p = _payload({"urls": [GOOD, "https://blog.test/feed2.xml"], "max_items": 3})
    assert "skipped_blank_urls" not in p, sorted(p)
    assert "blank" not in p["summary"], p["summary"]


def test_envelope_author_omits_the_scalar_when_nothing_was_skipped():
    env = _feed_batch_envelope(2, [{"source_url": "x", "items": [1]}], blank_positions=[])
    assert "skipped_blank_urls" not in env, sorted(env)
    assert "blank" not in env["summary"], env["summary"]
    env2 = _feed_batch_envelope(3, [{"source_url": "x", "items": [1]}], blank_positions=[1, 3])
    assert env2["skipped_blank_urls"] == 2, sorted(env2)
    assert "positions 1, 3" in env2["summary"], env2["summary"]


# ─── 3 · 全空白那一支：拒绝也要说得出丢了几颗，原话保留 ────────────────────

def test_the_all_blank_refusal_still_refuses_and_says_the_number(stub):
    """拒绝那一支走的不是异常而是同一套信封（R02-F32 立的形状），所以这里读回执的三个落点。"""
    p = _payload({"urls": ["", "   ", "\t"]})
    said = " ".join(str(p.get(k) or "") for k in ("error", "summary", "next_action"))
    assert "requires at least one URL" in said, p
    assert "3" in said, p


# ─── 4 · 结构锁：滤与报同一位作者，内联那条不许留下 ─────────────────────────

def test_the_filter_and_the_report_share_one_author():
    from dhole_mcp import server as S
    src = inspect.getsource(S)
    assert "if u and u.strip()" not in src, "方法体里那条内联滤法必须换成共用的那一位作者"
    assert src.count("_blank_url_split(") >= 3, "定义 + 滤那一处 + 报那一处，缺一处就是两处各数一遍"
    kept, blank = S._blank_url_split(["", GOOD, "  ", None])
    assert kept == [GOOD] and blank == [1, 3, 4], (kept, blank)


# ============================================================================
# 来源：test_cache_fingerprint_includes_stored_identity.py
# ============================================================================
# 存量身份必须参与缓存指纹 —— §3-17 搬家时断掉的那根管子。

URL = "https://staging.example.com/admin"
HOST = "staging.example.com"


def _counting_fetch(monkeypatch) -> list:
    """`_force_fetch` 的桩：记下每次真取，回的正文带序号以便区分新旧。

    桩打在这里而不是更深的层上，与 `test_cred_defaults_b5` 同一裁决：这是档位参数
    到齐的最后一站。配套地，下面的读全部钉 `force_fetcher="http"`——普通梯子走
    `_auto_escalate`，不经过这颗方法；而缓存查找坐在层选择**之前**，钉层不改变
    被测的行为（谁进缓存、谁被命中），只把路径收窄成确定的一根。
    """
    calls: list = []

    async def fake(self, url, force_fetcher=None, extraction_type="markdown",
                   css_selector=None, cache_ttl=3600, offset=0,
                   max_chars=40000, **kwargs):
        calls.append(url)
        result = ResponseModel(url=url, status=200,
                               content=[f"body-{len(calls)}"],
                               fetcher_used=force_fetcher or "http")
        # 真 _force_fetch 的收尾就是 _finalize_result（标注质量 → 写缓存 → 切块）；
        # 桩不接上它，写缓存就跟着桩一起消失了，第二次读永远 MISS。
        return await self._finalize_result(result, url, extraction_type,
                                           css_selector, cache_ttl, offset,
                                           max_chars)

    monkeypatch.setattr(MasterFetchServer, "_force_fetch", fake)
    return calls


def _read(url: str = URL) -> ResponseModel:
    return asyncio.run(MasterFetchServer().web_read(url, force_fetcher="http"))


def test_anonymous_reads_still_share_the_cache_entry(monkeypatch):
    calls = _counting_fetch(monkeypatch)
    _read()
    second = _read()
    assert len(calls) == 1, "第二次读应命中缓存"
    assert "body-1" in second.content


def test_stored_auth_forces_a_refetch_and_names_itself(monkeypatch):
    calls = _counting_fetch(monkeypatch)
    _read()
    identity.set_value("auth", {"type": "basic", "username": "u", "password": "p"},
                       scope=HOST)
    out = _read()
    assert len(calls) == 2, "存了身份后同 URL 必须重取，不许端出匿名缓存"
    assert "body-2" in out.content
    assert "auth" in (out.identity_applied or ""), out.identity_applied


def test_unset_returns_to_the_anonymous_entry(monkeypatch):
    """身份的每个取值各占一个缓存槽：unset 之后匿名那份仍然有效。"""
    calls = _counting_fetch(monkeypatch)
    _read()
    identity.set_value("auth", {"type": "basic", "username": "u", "password": "p"},
                       scope=HOST)
    _read()
    identity.unset(["auth"])
    out = _read()
    assert len(calls) == 2, "unset 后应命中匿名的旧槽，而不是第三次真取"
    assert "body-1" in out.content


def test_a_credential_for_another_host_does_not_open_this_cache_slot(monkeypatch):
    """host 作用域是身份的门，也是缓存槽的门 —— 两者必须同一判定。"""
    calls = _counting_fetch(monkeypatch)
    _read()
    identity.set_value("auth", {"type": "basic", "username": "u", "password": "p"},
                       scope="other.example.org")
    out = _read()
    assert len(calls) == 1, "别台的凭据不该让这台重取"
    assert "body-1" in out.content


def test_stored_proxy_counts_as_identity_too(monkeypatch):
    calls = _counting_fetch(monkeypatch)
    _read()
    identity.set_value("proxy", "http://127.0.0.1:7890")
    out = _read()
    assert len(calls) == 2, "存量代理改变出口，出口可能改变内容——必须重取"
    assert "body-2" in out.content
    identity.unset(["proxy"])
    out = _read()
    assert len(calls) == 2, "撤掉代理后回到直连的旧槽"
    assert "body-1" in out.content
