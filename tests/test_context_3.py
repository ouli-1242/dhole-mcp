"""test_context_3.py —— 由 4 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import re
import pytest
from dhole_mcp import wire
from dhole_mcp.context import _FOCUS
from dhole_mcp.focus import MAX_BLOCK_CHARS, _CLAUSE_BREAKS, _SENTENCE_BREAKS, _cap_block, _is_code, _is_table, _split_blocks, focus_content
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import _apply_chunking
import inspect
import json
from dhole_mcp import context, crawl as crawl_mod, errors, quality, security
from dhole_mcp.context import nothing_went_out
from dhole_mcp.server import MasterFetchServer
import asyncio
from pathlib import Path
from types import SimpleNamespace
from dhole_mcp import browser as browser_mod
from dhole_mcp import context as ctx
from dhole_mcp import server as server_mod
from dhole_mcp.context import call_deadline, report_progress, report_stage, set_mcp_session, stage_message
import ast
from dhole_mcp.context import tool_scope


# ============================================================================
# 来源：test_a_page_without_blank_lines_is_still_made_of_blocks.py
# ============================================================================
# 没有空行的长文档也是块拼起来的；切得更细之后，块的数目要一起交出来。

ANSWER = "会话 cookie 的存活时间由 DHOLE_COOKIE_TTL_S 决定，默认 3000 秒。"
FILLER = "第 {i} 段讲的是别的事：版面、字体回退，以及一些与本题无关的说明。"
BOUNDARIES = _SENTENCE_BREAKS + _CLAUSE_BREAKS


def _dense_doc(paras: int = 24) -> str:
    """一节一段、节与节之间只有**单换行**的一页 —— 旧切法在这里只得到一块。"""
    mid = paras // 2
    body = [f"## 第 {i} 节\n" + (ANSWER if i == mid else FILLER.format(i=i))
            for i in range(paras)]
    return "\n".join(body)


def _chunked(text: str, *, focus, max_chars: int = 4_000,
             offset: int = 0) -> ResponseModel:
    result = ResponseModel(url="https://e.test/dense", status=200,
                           content=[text], extracted_type="markdown")
    token = _FOCUS.set(focus)
    try:
        return _apply_chunking(result.model_copy(), max_chars=max_chars, offset=offset)
    finally:
        _FOCUS.reset(token)


def _lines(blocks: list[str]) -> list[str]:
    return [ln for b in blocks for ln in b.splitlines() if ln.strip()]


# 八种形状。`CAPPED` 里的那一种会被长度上限在一行**中间**断开（它整行只有一个界都找
# 不到），所以行级不变量对它不成立 —— 对它成立的是字符级的那一条。
CAPPED = {"one_line_with_no_boundary"}
SHAPES = {
    "sections_single_newlines": _dense_doc(),
    "prose_single_newlines": "\n".join(FILLER.format(i=i) for i in range(30)),
    "headings_only": "\n".join(f"# 第 {i} 节" for i in range(8)),
    "list_run": "\n".join(["步骤如下：", "- 安装", "- 重启", "- 验证",
                           "", "下一步讲配置。", "先设 DHOLE_HOME，再起宿主。"]),
    "table": ("| 列 | 值 | 备注 |\n| --- | --- | --- |\n"
              + "\n".join(f"| 第{i}行 | {i} | 备注内容 |" for i in range(40))),
    "fence": ("```python\n"
              + "\n".join(f"x_{i} = {i}  # 注释" for i in range(40))
              + "\n```\n后面还有一句。"),
    "rule": "上一节收尾。\n---\n下一节开始，讲分页。",
    "one_line_with_no_boundary": "字" * (MAX_BLOCK_CHARS * 3 + 17),
}


# ─── 1 · 无空行的一页也切得动 ────────────────────────────────────

def test_a_page_without_blank_lines_is_not_one_block():
    doc = _dense_doc()
    assert "\n\n" not in doc, "夹具里有空行就不是这一条要的形状了"
    assert len(_split_blocks(doc)) > 1, (
        "只认空行的话这里就是一整块 —— 3.5 要修的正是这一格")


def test_focus_cuts_a_dense_page_instead_of_apologising():
    doc = _dense_doc()
    stats: dict = {}
    out = focus_content(doc, "cookie 存活时间", stats=stats)
    assert not stats.get("ignored"), f"这一页现在切得动，道歉不该还在：{stats}"
    assert ANSWER in out, "省 token 不许以丢掉答案为代价"
    assert len(out) < len(doc) / 2, (
        f"切完还留下 {len(out)}/{len(doc)} 字：那一页在打分器眼里仍是一块")


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_no_written_character_disappears_from_the_finer_cut(name):
    """切得更细的代价不能是"悄悄少了几行"：把块拼回去，一个字都不差。"""
    text = SHAPES[name]
    joined = "".join(_split_blocks(text)).replace("\n", "")
    assert joined == text.replace("\n", "")


@pytest.mark.parametrize("name", sorted(set(SHAPES) - CAPPED))
def test_every_written_line_survives_the_finer_cut(name):
    """不被长度上限打断的那些形状里：原文每一行按原顺序落在恰好一块里。"""
    text = SHAPES[name]
    assert _lines(_split_blocks(text)) == [ln for ln in text.splitlines() if ln.strip()]


# ─── 2 · 上限与断点 ──────────────────────────────────────────────

def test_the_cap_returns_its_block_character_for_character():
    block = "第一句话说明一个配置项。" * 1_200
    assert len(block) > 5 * MAX_BLOCK_CHARS
    parts = _cap_block(block)
    assert len(parts) > 1, "超长的一块没有断开，上限就只是文档里的一个数"
    assert "".join(parts) == block, (
        "断点若落在标点之前，`。` 就被吃掉了 —— 那是丢内容，不是切块")


def test_a_cut_part_ends_on_a_boundary_not_mid_sentence():
    parts = _cap_block("段落 0 讲 alpha, beta; gamma - delta。下一句说明配置项。" * 400)
    assert len(parts) > 2, "没断成几块，这一条就没有对照"
    for part in parts[:-1]:
        assert len(part) <= MAX_BLOCK_CHARS
        assert part.endswith(BOUNDARIES), f"断点不在界上：…{part[-12:]!r}"


def test_a_run_with_no_boundary_anywhere_still_respects_the_cap():
    """一个界都找不到时，宁可硬切也不留一块 100k：BM25 的平均文档长度会被一个巨块拉平。"""
    whole = "字" * (MAX_BLOCK_CHARS * 2 + 5)
    parts = _cap_block(whole)
    assert [len(p) for p in parts] == [MAX_BLOCK_CHARS, MAX_BLOCK_CHARS, 5]
    assert "".join(parts) == whole


def test_no_prose_block_outlives_the_cap():
    text = "\n".join(FILLER.format(i=i) for i in range(400))
    blocks = _split_blocks(text)
    assert len(text) > 5 * MAX_BLOCK_CHARS, "夹具不够长，这一条就没有对照"
    assert len(blocks) > 1, "整页一块：上限没有起作用"
    for block in blocks:
        assert len(block) <= MAX_BLOCK_CHARS


# ─── 3 · 结构性正文不按长度处置 ─────────────────────────────────

def test_a_code_fence_is_never_cut_from_its_middle():
    body = "\n".join(f"    line_{i} = {i}  # 注释" for i in range(700))
    blocks = _split_blocks(f"```python\n{body}\n```\n")
    assert len(blocks) == 1, f"围栏被长度断了：{len(blocks)} 块"
    assert len(blocks[0]) > MAX_BLOCK_CHARS, "夹具短到不需要断，这一条就没有内容"
    assert _is_code(blocks[0])


def test_a_heading_inside_a_fence_stays_inside_the_fence():
    text = "```md\n# 这行像标题\n- 这行像列表\n| a | b |\n```\n围栏外的一句。"
    blocks = _split_blocks(text)
    assert _is_code(blocks[0]), "围栏里的形状被当成了形状，代码块就被拆开了"


def test_a_wide_table_is_never_cut_into_a_fake_one():
    rows = "\n".join(f"| 第{i}行 | 值{i} | 备注内容内容内容 |" for i in range(400))
    blocks = _split_blocks(f"| 列 | 值 | 备注 |\n| --- | --- | --- |\n{rows}")
    assert len(blocks) == 1, f"表被断成 {len(blocks)} 块"
    assert len(blocks[0]) > MAX_BLOCK_CHARS
    assert _is_table(blocks[0])


def test_a_table_is_its_own_block_not_a_paragraph_appendage():
    blocks = _split_blocks("先看这张表：\n| 列 | 值 |\n| --- | --- |\n| a | 1 |")
    assert blocks[-1].splitlines()[0] == "| 列 | 值 |", (
        "表头没有另起一块，`_is_table` 就读不到这张表")


# ─── 4 · 五种界各归一位 ─────────────────────────────────────────

def test_a_heading_keeps_the_paragraph_under_it():
    blocks = _split_blocks("# 安装\n先运行 pip install dhole-mcp，再重启宿主。")
    assert len(blocks) == 1
    assert blocks[0].startswith("# 安装"), "标题与它引出的正文是一块，这才是「这一块讲什么」"


def test_a_run_of_list_items_is_one_block():
    run = next(b for b in _split_blocks(SHAPES["list_run"]) if b.startswith("- 安装"))
    assert run.splitlines() == ["- 安装", "- 重启", "- 验证"], (
        "连续的条目讲的是一件事，把它们拆开就是三条都不完整")


def test_a_horizontal_rule_starts_a_block():
    blocks = _split_blocks(SHAPES["rule"])
    assert blocks[1].splitlines()[0] == "---", "水平线是界，不是上一段的续行"


def test_a_long_document_without_blank_lines_becomes_rankable():
    """81k 字那一份的真实形状：分节标题 + 单换行正文。旧切法一块，新切法几十块。"""
    text = "\n".join(f"## 第 {i} 节\n" + FILLER.format(i=i) * 3 for i in range(60))
    blocks = _split_blocks(text)
    assert len(text) > 5_000
    assert len(blocks) >= 20, f"只有 {len(blocks)} 块：这一页在打分器眼里还是一整块"
    assert _lines(blocks) == [ln for ln in text.splitlines() if ln.strip()]


# ─── 5 · 块账（3.6）────────────────────────────────────────────

def test_the_ledger_matches_the_count_written_in_the_body():
    stats: dict = {}
    out = focus_content(_dense_doc(), "cookie 存活时间", stats=stats)
    said = re.search(r"showing (\d+) of (\d+) blocks", out)
    assert said, out[:160]
    assert (int(said.group(1)), int(said.group(2))) == (
        stats["blocks_kept"], stats["blocks_total"]), (
        "头部说的数与账本不是同一份 —— 那就是两个作者各数一遍")


def test_best_mode_counts_the_block_it_kept():
    stats: dict = {}
    out = focus_content(_dense_doc(), "cookie 存活时间", mode="best", stats=stats)
    assert int(re.search(r"top of (\d+) by", out).group(1)) == stats["blocks_total"]
    body = out.split("\n\n", 1)[1]
    assert len([b for b in body.split("\n\n") if b.strip()]) == stats["blocks_kept"], (
        "账上的块数与实际交回的块数不符")


def test_a_no_op_focus_still_reports_a_ledger():
    """整页原样留下=一块都没丢，不是"没数" —— 这一格空白会被读成过滤器坏了。"""
    stats: dict = {}
    one = "整页只有一块正文，没有空行，也没有能当界用的形状。"
    assert focus_content(one, "正文", stats=stats) == one
    assert "one block" in stats["ignored"], stats
    assert (stats["blocks_total"], stats["blocks_kept"]) == (1, 1)


def test_the_ledger_is_on_the_wire_only_when_focus_ran():
    doc = _dense_doc()
    dumped = wire._wire_dump(_chunked(doc, focus=None))
    assert "focus_blocks_total" not in dumped
    assert "focus_blocks_kept" not in dumped, "没跑 focus 的一发在付块账的字节"

    loud = _chunked(doc, focus="cookie 存活时间")
    assert loud.focus_blocks_total > 1
    assert 0 < loud.focus_blocks_kept < loud.focus_blocks_total


def test_a_continuation_page_still_carries_the_ledger():
    """头部那句住在第 0 片里，翻页之后就没了 —— 数不能跟着没。"""
    doc = _dense_doc(40)
    stats: dict = {}
    focus_content(doc, "版面 字体", stats=stats)
    out = _chunked(doc, focus="版面 字体", max_chars=60, offset=60)
    assert "[Focus:" not in out.content[0], "夹具没把头部切走，这一条就没有对照"
    assert out.focus_blocks_total == stats["blocks_total"]
    assert out.focus_blocks_kept == stats["blocks_kept"], (
        "信封上的块数与打分器数出来的不一致 —— 又是两个作者各数一遍")


def test_the_no_more_content_branch_carries_the_ledger_too():
    first = _chunked(_dense_doc(), focus="cookie 存活时间", max_chars=10, offset=0)
    out = _chunked(_dense_doc(), focus="cookie 存活时间", max_chars=10, offset=10 ** 6)
    assert out.content == ["[No more content.]"], out.content
    assert (out.focus_blocks_total, out.focus_blocks_kept) == (
        first.focus_blocks_total, first.focus_blocks_kept), (
        "同一页、同一个 focus 词的两发交出两个块数 —— 那本书就不是同一本了")


# ============================================================================
# 来源：test_a_url_shape_refusal_speaks_from_its_own_door.py
# ============================================================================
# URL 那一族的拒绝要说本门的话：三条路（形状 / 地址 / 设计边界）收成同一位作者（评审 R02-098 · R02-100）。

GENERIC_UNKNOWN = "Fetch failed. Check the error field"
FTP = "ftp://example.com/"
BS = chr(92)
LOOP = "http://127.0.0.1:8399/"
META = "http://169.254.169.254/latest/meta-data/"
CLAUSE_RE = re.compile(r"[Nn]othing was [a-zA-Z ,\-]+?\.")

# 三族 × 每一扇。args 一律带 confirm，免得被别的闸抢答（B-41 那族教训）。
SHAPE = [
    ("web_read", {"url": FTP}),
    ("web_request", {"url": FTP, "method": "POST", "body": {"a": 1}, "confirm": True}),
    ("web_act", {"url": FTP, "actions": [{"click": {"selector": "b"}}], "confirm": True}),
    ("web_discover", {"url": FTP}),
    ("web_crawl", {"url": FTP}),
    ("feed_read", {"urls": ["ftp://example.com/f.rss"]}),
    ("web_read", {"url": ""}),
    ("web_discover", {"url": ""}),
    ("web_crawl", {"url": ""}),
    ("web_read", {"url": "   "}),
    ("web_read", {"url": "https://example.com" + BS + "a"}),
    ("web_request", {"url": "https://example.com" + BS + "a", "method": "POST",
                     "body": {"a": 1}, "confirm": True}),
    ("web_discover", {"url": "https://example.com" + BS + "a"}),
    ("web_crawl", {"url": "https://example.com" + BS + "a"}),
]
ADDRESS = [
    ("web_read", {"url": LOOP}),
    ("web_request", {"url": LOOP, "method": "POST", "body": {"a": 1}, "confirm": True}),
    ("web_act", {"url": LOOP, "actions": [{"click": {"selector": "b"}}], "confirm": True}),
    ("web_discover", {"url": LOOP}),
    ("web_crawl", {"url": LOOP}),
    ("feed_read", {"urls": [LOOP]}),
]
BOUNDARY = [
    ("web_read", {"url": META}),
    ("web_request", {"url": META, "method": "POST", "body": {"a": 1}, "confirm": True}),
    ("web_act", {"url": META, "actions": [{"click": {"selector": "b"}}], "confirm": True}),
    ("web_discover", {"url": META}),
    ("web_crawl", {"url": META}),
    ("feed_read", {"urls": [META]}),
]
def _tag(group, cases):
    return [(group, door, args) for door, args in cases]


T_SHAPE, T_ADDRESS, T_BOUNDARY = (_tag("shape", SHAPE), _tag("address", ADDRESS),
                                  _tag("boundary", BOUNDARY))
ALL = T_SHAPE + T_ADDRESS + T_BOUNDARY


async def _face(door: str, args: dict) -> dict:
    out = await MasterFetchServer()._dispatch(door, args)
    rows = out[1] if isinstance(out, tuple) else out
    text = rows[0].text if not isinstance(rows, dict) else json.dumps(rows)
    d = json.loads(text)
    assert d.get("error"), f"这一形状本该被拒，却回了没有 error 的包: {d}"
    return d


def _ids(cases):
    out = []
    for g, d, a in cases:
        u = a.get("url") or (a.get("urls") or [""])[0] or "<空>"
        out.append(pytest.param(d, a, id=f"{g}-{d}-{str(u)[:22]!r}"))
    return out


class TestEveryDoorRefusalSpeaksItsOwnVerb:
    """门话是拒绝信封的属性，不是某一份文案里的赠品。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door,args", _ids(ALL))
    async def test_the_refusal_is_named_as_an_input_refusal(self, door, args):
        d = await _face(door, args)
        assert d["error"].startswith("invalid_request:"), d["error"][:160]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door,args", _ids(ALL))
    async def test_the_way_out_starts_with_this_door_s_clause(self, door, args):
        na = (await _face(door, args)).get("next_action") or ""
        want = nothing_went_out(door)
        assert na.startswith(want), f"{door}: 行首不是本门那句 → {na[:200]}"
        assert len(CLAUSE_RE.findall(na)) == 1, f"两遍等于两位作者: {na[:200]}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door,args", _ids(ALL))
    async def test_a_named_refusal_does_not_offer_a_generic_retry(self, door, args):
        na = (await _face(door, args)).get("next_action") or ""
        assert GENERIC_UNKNOWN not in na, f"{door} 还在把具名拒绝推给兜底句: {na[:160]}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door,args", _ids(T_ADDRESS + T_BOUNDARY))
    async def test_an_address_refusal_does_not_blame_the_spelling(self, door, args):
        na = (await _face(door, args)).get("next_action") or ""
        assert "absolute http(s) URL" not in na, f"{door}: 地址被拒却要读者改拼法 → {na[:200]}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door,args", _ids(T_SHAPE))
    async def test_a_shape_refusal_names_the_spelling_it_rejected(self, door, args):
        na = (await _face(door, args)).get("next_action") or ""
        assert ("http(s)" in na or "url=" in na), f"{door}: 出路没点名 url → {na[:200]}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door,args", _ids(T_SHAPE))
    async def test_the_shape_template_names_this_door_not_the_engine(self, door, args):
        """`discover` 借 `crawl` 引擎走这条路：模板里那个门名必须是调用方那一扇。

        真机 `r119` 的 S4/S5 从前根本没有这句（出路是通用兜底句），所以这条判据是**新句子
        的配套守卫**：装配点若把引擎名写死，`web_discover` 的读者会听见 `web_crawl` 的名字 ——
        那正是 R02-083 量到并修过的形状，不许在新增的路上复发（P5 复验 `f86_verify.json`
        逐格读到 `web_discover` / `web_crawl` 各自的名字）。
        """
        na = (await _face(door, args)).get("next_action") or ""
        m = re.search(r"(\w+) takes an absolute http\(s\) URL", na)
        if m is None:
            pytest.skip(f"{door} 这一格走的是自己那份出路（没有共用模板）")
        assert m.group(1) == door, f"{door} 的出路写着 {m.group(1)} takes…"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("door,args", _ids([c for c in ALL if c[0] in ("shape", "boundary")
                                                and c[1] in ("web_discover", "web_crawl")]))
    async def test_the_map_doors_stop_at_the_door(self, door, args):
        d = await _face(door, args)
        # 缺 url 那一格由装配层先答（信封是 ResponseModel，压根没有 pages 这一栏），
        # 形状/边界那一格由引擎答（CrawlResponseModel）。两支都算"停在门口"。
        stopped = ((d.get("pages") == [] and d.get("pages_crawled", 0) == 0)
                   or d.get("fetcher_used") == "none")
        assert stopped, f"{door} 的具名拒绝没有停在门口: " \
                        f"pages={d.get('pages_crawled')} fetcher={d.get('fetcher_used')}"


class TestTheThreeSharedTextsCarryNoVerb:
    """共用文案只留本体：焊在正文里的动词会被读到别的门上（R02-100）。"""

    def test_the_shared_bodies_speak_no_door_s_verb(self):
        for name in ("PRIVATE_ADDRESS_RECOVERY", "PERMANENT_URL_RECOVERY",
                     "URL_SHAPE_RECOVERY", "TIMEOUT_CEILING_RECOVERY"):
            body = getattr(security, name, None)
            assert isinstance(body, str), f"缺这份共用文案: {name}"
            assert not re.search(r"nothing was \w+", body, re.I), (
                f"security.{name} 的正文里焊了一句门话")

    def test_the_shape_body_names_the_argument_it_rejects(self):
        assert "http(s)" in security.URL_SHAPE_RECOVERY
        assert "{tool}" in security.URL_SHAPE_RECOVERY, "拼法建议要说本扇的名字，名字由调用方交进来"

    def test_the_boundary_body_still_offers_no_waiver(self):
        # 设计边界没有出路可指：这句话不许被修文案时顺手加上一扇开得到的门（§12 不许放宽闸）。
        assert "allow_private" not in security.PERMANENT_URL_RECOVERY
        assert "design boundary" in security.PERMANENT_URL_RECOVERY

    def test_the_generic_hint_survives_for_real_fetch_failures(self):
        # 兜底句本身没错，错的是具名拒绝走到它。网络类失败照旧拿它。
        _, hint = errors.classify_network_error("Connection refused by target host")
        assert "refused" in (hint or "").lower() or hint


class TestOneAuthorForTheVerb:
    """**共用文案**里不许焊门话：句子的本体一份写，动词由装配点按门取。

    这一把尺量的范围是"模块级字符串常量"（`PRIVATE_ADDRESS_RECOVERY` 那种被几扇门共用的
    文案）—— 那是本轮量到的形状（R02-100），也是静态可判的形状。函数内部的字面量不在这里
    判：仓库里已有十几处按门写对的句子（`web_read` 的 `Nothing was fetched. Rename it …`），
    把它们一次性改写成装配调用是另一件事的半径（§12 最小影响），而且静态扫函数体还会把
    docstring 一起扫进来 —— 量具自己先红在错的原因上（本轮就踩过一次，见 §㉞）。
    """

    WELDED = re.compile(r"(^|[\s;\"'])(Nothing was (sent|fetched|run)"
                       r"|and nothing was (sent|fetched|run))")

    def _welded_constants(self, mod):
        return [(n, v[:70]) for n, v in vars(mod).items()
                if isinstance(v, str) and self.WELDED.search(v)]

    @pytest.mark.parametrize("mod", [quality, crawl_mod, security, errors,
                                     pytest.param(__import__("dhole_mcp.server",
                                                              fromlist=["x"]),
                                                  id="dhole_mcp.server")])
    def test_no_shared_text_writes_another_door_s_verb(self, mod):
        assert self._welded_constants(mod) == [], (
            f"{mod.__name__} 里还有共用文案焊着门话")

    def test_the_ruler_does_flag_a_welded_constant(self):
        # 反向守卫：把修前那份 `PRIVATE_ADDRESS_RECOVERY` 的开头喂给同一把尺，必须量出红。
        pre_fix = ("PRIVATE_X = 'The URL is well-formed - what is refused is the address "
                   "it points at, and nothing was sent.'")
        assert self.WELDED.search(pre_fix), "这把尺子已经读不出焊住的动词了"

    def test_the_author_is_the_only_one(self):
        src = inspect.getsource(context.nothing_went_out)
        for verb in ("Nothing was sent", "Nothing was fetched", "Nothing was run"):
            assert verb in src, f"{verb} 不在唯一那位作者手里"


class TestTheOldReadingsStillFail:
    """反向守卫：三句修前的原话拿到同一把尺子下，必须量出红。"""

    PRE_FIX = [
        # r119 S1（形状 · 无前缀缺失但无门话）· S4（discover 裸句 + 通用句）
        ("shape · web_read", "Correct the argument and call again - no request was made. "
                             "web_read takes an absolute http(s) URL (e.g. https://example.com)."),
        ("shape · web_discover", GENERIC_UNKNOWN + " for details; try a different source "
                                "or retry with different parameters."),
        ("address · web_read", "The URL is well-formed - what is refused is the address it "
                               "points at, and nothing was sent. If you meant a public site, "
                               "search for the page instead."),
        ("boundary · web_act", "No argument fixes this - it is a design boundary: dhole never "
                               "fetches cloud-metadata endpoints (SSRF guard), whatever the "
                               "spelling. Nothing was sent; if you meant a public site, "
                               "the URL itself is wrong."),
    ]

    @pytest.mark.parametrize("label,way", PRE_FIX, ids=[p[0] for p in PRE_FIX])
    def test_the_ruler_rejects_the_pre_fix_sentence(self, label, way):
        door = label.split("·")[1].strip()
        assert not way.startswith(nothing_went_out(door)), f"{label} 修前就该红"
        if door in ("web_read", "web_act"):
            assert not self._own_verb_only(way, door), f"{label} 没有把别人的动词判为错"

    @staticmethod
    def _own_verb_only(way: str, door: str) -> bool:
        """本门那句话在场、且没有别的门的动词 —— 修后的地址/边界族应当如此。"""
        verbs = set(re.findall(r"nothing was (\w+)", way, re.I))
        return {nothing_went_out(door).split()[2].rstrip(".;").lower()} == verbs


# ============================================================================
# 来源：test_the_long_waits_say_what_they_are_waiting_for.py
# ============================================================================
# 三类等最久的东西必须在自己开始的时候说一句话 —— 静默不是"还在跑"，是"没消息"。

REPO = Path(__file__).resolve().parents[1]
BROWSER_SRC = (REPO / "src" / "dhole_mcp" / "browser.py").read_text(encoding="utf-8")
SERVER_SRC = (REPO / "src" / "dhole_mcp" / "server.py").read_text(encoding="utf-8")


class _Session:
    def __init__(self, raises=None, log_raises=None):
        self.sent = []
        self.logs = []
        self._raises = raises
        self._log_raises = log_raises

    async def report_progress(self, progress, total=None, message=None):
        if self._raises is not None:
            raise self._raises
        self.sent.append((progress, total, message))

    async def send_log_message(self, level, data, logger=None,
                               related_request_id=None):
        if self._log_raises is not None:
            raise self._log_raises
        self.logs.append((level, data, logger))


@pytest.fixture
def session():
    s = _Session()
    set_mcp_session(s)
    try:
        yield s
    finally:
        set_mcp_session(None)


@pytest.fixture
def clock(monkeypatch):
    now = [1_000.0]
    monkeypatch.setattr(ctx, "time", SimpleNamespace(time=lambda: now[0]))
    return now


# ─── 1 · 那句话怎么拼 ───────────────────────────────────────────

def test_no_budget_in_scope_means_the_remaining_clause_is_absent(clock):
    assert ctx._CALL_DEADLINE.get() is None, "上一条用例漏了摘那颗 ContextVar"
    line = stage_message("starting a browser")
    assert line == "starting a browser", line
    assert "left" not in line, "没人点过预算，那句'还剩多少'是编的"


def test_the_remaining_comes_from_the_calls_own_deadline(clock):
    with call_deadline(30):
        assert stage_message("starting a browser") == (
            "starting a browser | 30000ms left in this call's budget")


def test_spent_is_said_only_when_the_site_knows_it(clock):
    with call_deadline(30):
        assert stage_message("tier jump", 1204.4) == (
            "tier jump | spent 1204ms | 30000ms left in this call's budget")
    assert stage_message("tier jump") == "tier jump", "不知道花了多少就说 0ms 是一种谎"


def test_the_first_use_download_declines_to_quote_a_remaining(clock):
    """`show_left=False` 不是关掉算术，是关掉一次误导（判据 3）。"""
    with call_deadline(0.2):
        assert "left" not in stage_message("downloading chromium", show_left=False)
        # 同一颗时钟下别的句子照报剩余 —— 关掉的是这一处，不是那半句本身
        assert "200ms left" in stage_message("downloading chromium")


# ─── 2 · 送不出去的时候，抓取照跑 ────────────────────────────────

def test_no_session_sends_nothing_and_says_so():
    set_mcp_session(None)
    assert asyncio.run(report_stage("anything")) is False


def test_a_host_that_does_not_want_progress_is_not_re_implemented_here():
    """SDK 的 `report_progress` 自己就是"没带 progressToken 时 no-op"（实测）。

    这一条守的是**这里不多判一遍**：判了就有第二处会写错的地方，而错的方式是"这一格
    今天没人看见"——正是这一刀要消灭的那种静默。查的是 AST 里的名字，不是源码文本 ——
    docstring 本来就该把这件事讲清楚，用文本判会把讲道理的那几行判成违例。
    """
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(report_progress))
    read = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    read |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    banned = {"capabilities", "progressToken", "progress_token", "experimental"}
    assert not (read & banned), f"这里又判了一遍宿主能力：{sorted(read & banned)}"


def test_a_broken_back_channel_costs_one_sentence_not_the_call(session):
    """progress 那一条炸了不许冒到抓取上；另一条照发（判据 4 + 6）。"""
    session._raises = RuntimeError("pipe closed")
    assert asyncio.run(report_stage("starting a browser")) is True
    assert len(session.logs) == 1, session.logs


# ─── 2b · 双通道：一条宿主不看的通道不许成为唯一的出口 ─────────────

def test_the_same_sentence_rides_both_channels(session):
    """progress 与 logging 的支持面不重合：只看 logging 的宿主在 progress 上是全黑的。"""
    assert asyncio.run(report_stage("starting a browser", 1500)) is True
    assert len(session.sent) == 1 and len(session.logs) == 1, (session.sent, session.logs)
    progress_message = session.sent[0][2]
    level, log_data, logger = session.logs[0]
    assert log_data == progress_message, (
        "两条通道说的是两句话：读哪一条的人都以为自己看见了全部")
    assert level == "info" and logger == "dhole.stage", (level, logger)


def test_a_host_without_the_logging_channel_still_gets_progress(session):
    session._log_raises = RuntimeError("no such capability")
    assert asyncio.run(report_stage("waiting for the network to go quiet")) is True
    assert len(session.sent) == 1, session.sent


def test_a_host_without_the_progress_channel_still_gets_the_log_line(session):
    session._raises = RuntimeError("no progressToken")
    assert asyncio.run(report_stage("waiting for the network to go quiet")) is True
    assert len(session.logs) == 1, session.logs


def test_a_cancelled_call_is_not_swallowed_as_a_missed_message(session):
    session._raises = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(report_stage("starting a browser"))


def test_the_stage_line_reaches_the_session(session):
    assert asyncio.run(report_stage("starting a browser", 2500)) is True
    assert len(session.sent) == 1
    progress, total, message = session.sent[0]
    assert progress == 2.5 and total is None, (
        "这类等待没有分母；报一个 total 就是编一个百分比")
    assert message.startswith("starting a browser | spent 2500ms")


# ─── 3 · 接线：三处最慢的等待真的在开始的那一刻发 ──────────────────

def test_the_tier_jump_speaks_before_the_browser_starts():
    # 先把要判的数取成小对象：把整份源码留在断言表达式里，红了会打印一整个模块
    jumps = "escalating to the JS tier" in SERVER_SRC
    emits = SERVER_SRC.count("await report_stage(")
    assert jumps, "http→stealthy 那一跃没发：它是最长的一次静默的入口"
    assert emits >= 2, f"server 侧只剩 {emits} 处发射：首用下载那一发（分钟级）呢？"


def test_the_first_use_install_does_not_quote_a_remaining_it_has_already_spent():
    ok = ("downloading it now" in SERVER_SRC) and ("show_left=False" in SERVER_SRC)
    assert ok, ("那段时间事后由 `_apply_install_shift` 还给预算 —— 当场报剩余会报出一个"
                "假的 0，把'我要开始一场几分钟的下载'说成'这次调用已经没时间了'")


def test_the_browser_process_launch_speaks():
    # 两处静默各自的句子必须是它自己那句 —— 只数发射次数会放过"两处都发同一句话"
    launches = "browser process (a few seconds)" in BROWSER_SRC
    assert launches, "起进程那一句没说出它在等什么（launch_persistent_context 之前）"
    stages = [line for line in BROWSER_SRC.splitlines() if "await report_stage(" in line]
    assert len(stages) >= 2, f"browser 侧只剩 {len(stages)} 处发射：等 network-idle 那一处呢"


def test_the_idle_wait_names_its_own_cap():
    """帽的属主是 `SETTLE_IDLE_MS`，句子里不许再有第二颗 5000。"""
    assert "SETTLE_IDLE_MS}ms for its network" in BROWSER_SRC, (
        "那句等待话把帽写成了字面量 —— 帽改了话不会跟着改，就是下一句谎")


def test_the_emitters_import_the_one_sentence_author():
    """两处发射用的都是 `context` 那一句 —— 没有谁在本地重拼"还剩多少"。

    判的是**那句话的形状只有一处生产**：`left in this call's budget` 这半句出现在别的
    模块源码里，就意味着有人自己抄了一遍算术。
    """
    assert server_mod.report_stage is ctx.report_stage
    assert "from dhole_mcp.context import report_stage" in BROWSER_SRC
    for name, module_src in (("server.py", SERVER_SRC), ("browser.py", BROWSER_SRC)):
        assert "left in this call's budget" not in module_src, (
            f"{name} 自己在拼那半句剩余：又一位作者")


# ─── 4 · 行为：等 network-idle 那处真的发出去了 ───────────────────

class _IdlePage:
    def __init__(self):
        self.states = []

    async def wait_for_load_state(self, state=None, timeout=None):
        self.states.append((state, timeout))

    async def evaluate(self, script, arg=None):
        # `_looks_unrendered` 要的读数：正文 0 字而页上挂着 script —— 判成"还没长出来"
        return {"text": 0, "nodes": 3, "scripts": 1}


def test_the_unrendered_page_wait_announces_itself_before_it_starts(session):
    """这一格是量出来的：`/captcha` 那 61.5 秒里有 5 秒属于这里，而它一句都没发过。"""
    page = _IdlePage()
    holder = browser_mod.BrowserSession.__new__(browser_mod.BrowserSession)
    got = asyncio.run(holder._wait_for_stability(page))
    assert got is True, "这一等没走：夹具的读数不再算'还没渲染'了，用例要跟着换"
    assert ("networkidle", browser_mod.SETTLE_IDLE_MS) in page.states, page.states
    assert [p for p, _t, _m in session.sent] == [0.0], session.sent
    message = session.sent[0][2]
    assert "network to go quiet" in message, message
    # 话先于等待：发的时刻在 `wait_for_load_state` 之前，否则这句话要等静默结束才到
    assert session.sent[0][2].startswith("the page rendered nothing yet"), message


def test_the_rendered_page_stays_silent(session):
    """反面守卫：页面正常渲染时一句都不发 —— 这条通道不是日志洪水。"""
    class _RenderedPage(_IdlePage):
        async def evaluate(self, script, arg=None):
            return {"text": 4_200, "nodes": 120, "scripts": 0}

    page = _RenderedPage()
    holder = browser_mod.BrowserSession.__new__(browser_mod.BrowserSession)
    got = asyncio.run(holder._wait_for_stability(page))
    assert got is False
    assert session.sent == [], session.sent
    assert ("networkidle", browser_mod.SETTLE_IDLE_MS) not in page.states, page.states


# ============================================================================
# 来源：test_a_name_this_server_has_not_still_answers_with_an_envelope.py
# ============================================================================
# 一扇不存在的门也要用设计过的信封说话（评审 R02-130 的 unknown-tool 半边 · F123）。

DEFAULT_WORD = "Nothing was fetched or changed."
SRC = Path(__file__).resolve().parent.parent / "src" / "dhole_mcp"

ROSTER = sorted(server_mod._TOP_LEVEL_ARGS)


async def _wire(name: str, args: dict) -> tuple[str, bool]:
    """真 SDK 回路：InMemoryTransport 包住的正是 stdio/HTTP 跑的同一个 handler。"""
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(name, args)
    return "".join(getattr(b, "text", "") for b in res.content), bool(res.is_error)


def _envelope(name: str, args: dict | None = None) -> dict:
    text, is_error = _run(name, args or {})
    assert not is_error, f"设计过的拒绝不该以 transport 错误出去: {text[:200]}"
    payload = json.loads(text)
    assert isinstance(payload, dict), text[:200]
    return payload


def _run(name: str, args: dict) -> tuple[str, bool]:
    import asyncio

    return asyncio.run(_wire(name, args))


class TestTheUnknownNameStillAnswersWithAnEnvelope:
    """门名错的那一发，与参数错的那一发是同一种事：设计过的拒绝。"""

    def test_it_answers_json_with_the_three_envelope_pieces(self):
        payload = _envelope("no_such_tool_at_all")
        assert payload.get("error", "").startswith("Unknown tool"), payload
        assert payload.get("next_action"), payload
        assert payload.get("summary"), payload
        assert payload.get("diagnosis"), payload

    def test_it_declares_that_nothing_ran_at_the_head_of_the_way_out(self):
        way_out = _envelope("no_such_tool_at_all")["next_action"]
        assert way_out.startswith(DEFAULT_WORD), way_out

    def test_the_declaration_is_spoken_once(self):
        way_out = _envelope("no_such_tool_at_all")["next_action"]
        assert way_out.count(DEFAULT_WORD) == 1, way_out

    def test_the_name_it_gives_is_one_the_tables_author(self):
        payload = _envelope("no_such_tool_at_all")
        assert payload["diagnosis"]["code"] == "unknown_tool", payload
        assert payload["diagnosis"]["retryable"] is False, payload
        # 同一句话在表里也是这个名字：把手不是门自己编的
        assert quality.classify_error_text(payload["error"])[0] == "unknown_tool"

    def test_the_summary_is_the_one_the_label_author_writes(self):
        summary = _envelope("no_such_tool_at_all")["summary"]
        assert summary.startswith("invalid request"), summary

    def test_the_roster_arrives_whole(self):
        error = _envelope("no_such_tool_at_all")["error"]
        for name in ROSTER:
            assert f"'{name}'" in error, f"名册里少了 {name}: {error}"

    @pytest.mark.parametrize("retired", sorted(server_mod._REMOVED_TOOL_HINTS))
    def test_a_retired_name_reaches_the_door_it_moved_to(self, retired):
        """旧名那一发把"新家怎么写"整句带到 —— 兜底那一刀剪掉的正是这一截（r338）。"""
        hint = server_mod._REMOVED_TOOL_HINTS[retired]
        error = _envelope(retired)["error"]
        assert hint in error, f"出路被剪短了:\n  期待: {hint}\n  到场: {error[-220:]}"

    def test_no_door_runs_for_a_name_that_names_no_door(self, monkeypatch):
        ran = []
        for door in ("web_read", "web_search", "sys_state"):
            monkeypatch.setattr(MasterFetchServer, door,
                                _spy(ran, door), raising=False)
        _envelope("no_such_tool_at_all")
        assert ran == [], f"为了报个名字开始跑门了: {ran}"


def _spy(bucket, label):
    async def _fn(*a, **k):
        bucket.append(label)
        return None
    return _fn


class TestTheSwitchedOffDoorIsRefusedByTheSameAuthor:
    """`DHOLE_TOOLS` 关掉的那一扇是同一条边界的另一颗 raise —— 修一颗留一颗就是第二个名字。"""

    @pytest.fixture
    def only_read_enabled(self, monkeypatch):
        monkeypatch.setattr(server_mod, "_ENABLED_TOOLS", frozenset({"web_read"}))

    def test_it_names_the_switch_that_opens_it(self, only_read_enabled):
        payload = _envelope("sys_state", {"show": ["config"]})
        assert "not enabled in this server" in payload["error"], payload
        assert "DHOLE_TOOLS" in payload["error"], payload["error"]

    def test_it_uses_its_own_registered_name(self, only_read_enabled):
        payload = _envelope("sys_state", {"show": ["config"]})
        assert payload["diagnosis"]["code"] == "tool_not_enabled", payload
        assert payload["diagnosis"]["retryable"] is False, payload

    def test_a_switched_off_read_door_still_speaks_its_own_verb(self, only_read_enabled):
        """这一支钉的是"申报在抛出的那一刻按门取"：被关掉的 `web_read` 不许听到默认支那句。

        若在边界现取词（`tool_scope` 早已随异常退出），这扇只会说 `Nothing was fetched or
        changed.` —— 而同一扇 `web_read` 的陌生参数面说的是 `Nothing was fetched.`。
        """
        payload = _envelope("web_search", {"query": "widgets"})
        assert payload["next_action"].startswith("Nothing was searched"), payload["next_action"]

    def test_the_enabled_list_arrives_whole(self, only_read_enabled):
        payload = _envelope("sys_state", {"show": ["config"]})
        assert "'web_read'" in payload["error"], payload["error"]


class TestTheEnvelopeDoesNotBorrowADoorShell:
    """一扇不存在的门不能借任何一扇的壳说话（R02-103 那条法在边界上的样子）。"""

    FETCH_SHELL = ("content", "content_ok", "duration_ms", "fetcher_used",
                   "status", "url", "results", "pages", "feeds")

    @pytest.mark.parametrize("name,arguments", [
        ("no_such_tool_at_all", {}),
        ("web_resolve", {}),
    ])
    def test_a_name_that_named_no_door_carries_no_door_fields(self, name, arguments):
        payload = _envelope(name, arguments)
        borrowed = [k for k in self.FETCH_SHELL if k in payload]
        assert not borrowed, f"借了别人的壳: {borrowed} · {sorted(payload)}"

    def test_the_door_level_param_refusal_keeps_its_own_shape(self):
        """对照面：那一扇的参数面仍是四格 + 把手 —— 两副壳没被我合一副。"""
        payload = _envelope("sys_state", {"not_an_argument": 1})
        assert payload["diagnosis"]["code"] == "invalid_request", payload
        assert set(payload) <= {"message", "error", "next_action", "summary",
                                "diagnosis"}, sorted(payload)


class TestTheOldLocksAndTheFallback:
    """边界认识的这一族不许顺手把兜底掏空。"""

    def test_dispatch_still_raises_a_valueerror_for_a_name_it_does_not_have(self):
        import asyncio

        with pytest.raises(ValueError) as exc:
            asyncio.run(MasterFetchServer()._dispatch("no_such_tool_at_all", {}))
        assert "Unknown tool" in str(exc.value)

    def test_the_disabled_door_keeps_the_head_of_its_sentence(self, monkeypatch):
        import asyncio

        monkeypatch.setattr(server_mod, "_ENABLED_TOOLS", frozenset({"web_read"}))
        with pytest.raises(ValueError) as exc:
            asyncio.run(MasterFetchServer()._dispatch("sys_state", {"show": []}))
        assert str(exc.value).startswith("Tool 'sys_state' is not enabled"), exc.value

    def test_a_real_internal_error_still_uses_the_transport_fallback(self, monkeypatch):
        """兜底还在收它该收的那一类：门里抛出的非设计异常不许被这条分支吞掉。"""
        async def _boom(self, url="", **kw):
            raise RuntimeError("something inside the door broke")

        monkeypatch.setattr(MasterFetchServer, "web_read", _boom, raising=False)
        text, is_error = _run("web_read", {"url": "https://example.com/"})
        assert is_error is True, text[:200]
        assert json.loads(text) == {"error": "something inside the door broke"}

    def test_the_default_word_for_an_unclaimed_name_is_the_table_default(self):
        """跨门那张表对没被认领的门名交默认支 —— 这一支是本把取词的来源，不是新造的话。"""
        with tool_scope("no_such_tool_at_all"):
            assert nothing_went_out() == DEFAULT_WORD


class TestOneAuthorPerSentence:
    """那句名册话只许有一位作者；两颗 code 只许住在表里。"""

    def test_the_roster_sentence_is_written_exactly_once_in_src(self):
        holders = []
        for path in sorted(SRC.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.JoinedStr):
                    text = "".join(v.value for v in node.values
                                   if isinstance(v, ast.Constant)
                                   and isinstance(v.value, str))
                    if text.startswith("Unknown tool:"):
                        holders.append(f"{path.name}:{node.lineno}")
        assert len(holders) == 1, f"那句名册话有 {len(holders)} 位作者: {holders}"

    def test_the_boundary_does_not_hand_make_a_diagnosis(self):
        """按 AST 的关键字实参数，不按源码文本数 —— 注释里提到它不算写它（R02-088 那把尺子）。"""
        import textwrap

        tree = ast.parse(textwrap.dedent(
            inspect.getsource(server_mod.MasterFetchServer._mcp_server)))
        hand_made = [node.lineno for node in ast.walk(tree)
                     if isinstance(node, ast.keyword) and node.arg == "diagnosis"]
        assert not hand_made, f"边界上手搓了把手: {hand_made}"

    @pytest.mark.parametrize("code,table", [
        ("unknown_tool", {row[1] for row in quality._DIAGNOSIS_PREFIXES}),
        ("tool_not_enabled", {row[1] for row in quality._DIAGNOSIS_CONTAINS}),
    ])
    def test_each_new_name_is_registered_in_a_table(self, code, table):
        assert code in table, f"{code} 不在表里，等于门自己编了一个名字"

    def test_both_names_are_findable_in_the_manual(self):
        row = next(line for line in (SRC / "skills" / "dhole-web" / "references" /
                                     "fields.md").read_text(encoding="utf-8").splitlines()
                   if line.startswith("| `diagnosis`"))
        for code in ("unknown_tool", "tool_not_enabled"):
            assert code in row, f"调用方按 code 分支时查不到 {code}"
