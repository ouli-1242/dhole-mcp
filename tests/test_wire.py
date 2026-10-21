"""test_wire.py —— 由 4 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import json
import pytest
from dhole_mcp import ops
from dhole_mcp.server import DHOLE_INSTRUCTIONS, MasterFetchServer
from dhole_mcp.wire import tool_defs_digest, wire_face_digest
import pathlib
from dhole_mcp.wire import minimal_face
from dhole_mcp.models import BulkResponseModel, ResponseModel
from dhole_mcp.wire import _WIRE_OMIT
import asyncio
from pathlib import Path
from dhole_mcp.server import _extract_pdf_response


# ============================================================================
# 来源：test_the_lock_is_written_by_a_command_and_never_by_hand.py
# ============================================================================
# 锁由一扇命令写，不由手抄 —— 而那句"不许手抄"现在有人执行了。

LOCK = ops._tool_defs_lock_path()


@pytest.fixture
def sandbox_lock(tmp_path, monkeypatch):
    """把锁搬到临时目录：`--write` 的那次往返不许碰仓库里那份真的。"""
    copy = tmp_path / "tool_defs_lock.json"
    copy.write_text(LOCK.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(ops, "_tool_defs_lock_path", lambda: copy)
    return copy


def _want() -> tuple[str, str, int]:
    defs = MasterFetchServer._TOOL_DEFS
    return (tool_defs_digest(defs), wire_face_digest(defs, DHOLE_INSTRUCTIONS),
            len(defs))


# ─── 1 · 命令与文件当下 agrees ────────────────────────────────────

def test_the_lock_check_passes_on_a_clean_tree(capsys) -> None:
    assert ops._cmd_wire(["lock"]) == 0, capsys.readouterr().out
    assert "lock matches" in capsys.readouterr().out


def test_a_drifted_lock_is_named_not_guessed(capsys, sandbox_lock) -> None:
    """红了要说清是哪一半红的：`sha256` 与 `sha256_wire_face` 是两条独立的证据。

    把其中一格改脏，命令必须非零退出并点名那一格，而另一格不许被顺带动过 —— 一扇只说
    "不匹配"的门会把人送去重算整张脸。
    """
    data = json.loads(sandbox_lock.read_text(encoding="utf-8"))
    data["sha256_wire_face"] = "0" * 64
    sandbox_lock.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert ops._cmd_wire(["lock"]) == 1
    err = capsys.readouterr().err
    assert "sha256_wire_face" in err and "sha256," not in err, err
    assert "--write --reason" in err, "拒绝要给一条走得通的下一步"


# ─── 2 · 没有理由的改写不算改写 ───────────────────────────────────

def test_writing_the_lock_without_a_reason_is_refused(capsys, sandbox_lock) -> None:
    before = sandbox_lock.read_text(encoding="utf-8")
    assert ops._cmd_wire(["lock", "--write"]) == 2
    assert "reason" in capsys.readouterr().err.lower()
    assert sandbox_lock.read_text(encoding="utf-8") == before, "拒绝之前不许已经动过文件"


def test_both_spellings_of_the_reason_flag_arrive(sandbox_lock) -> None:
    """`--reason "…"` 与 `--reason=…` 是同一条路（字符串客户端与本仓库的 CLI 惯例）。"""
    assert ops._cmd_wire(["lock", "--write", "--reason", "刀A：一句真话"]) == 0
    data = json.loads(sandbox_lock.read_text(encoding="utf-8"))
    assert "刀A：一句真话" in data["comment"]
    assert ops._cmd_wire(["lock", "--write", "--reason=刀B：另一句"]) == 0
    data = json.loads(sandbox_lock.read_text(encoding="utf-8"))
    assert "刀B：另一句" in data["comment"]


# ─── 3 · 写出来的就是命令算出来的 ─────────────────────────────────

def test_a_write_repins_both_halves_and_keeps_the_history(sandbox_lock) -> None:
    """往返：脏文件经过一次 `--write` 之后，两半都等于命令自己的算术，旧 comment 不丢。

    这一条同时是 §4 的实质：写者只有一扇命令 —— 手抄要抄对，得先把命令跑一遍。
    """
    data = json.loads(sandbox_lock.read_text(encoding="utf-8"))
    old_comment = data["comment"]
    data["sha256"] = "f" * 64
    data["tools"] = 99
    sandbox_lock.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    assert ops._cmd_wire(["lock", "--write", "--reason", "把两半钉回今天的脸"]) == 0
    want_tools, want_face, want_count = _want()
    after = json.loads(sandbox_lock.read_text(encoding="utf-8"))
    assert after["sha256"] == want_tools
    assert after["sha256_wire_face"] == want_face
    assert after["tools"] == want_count
    assert after["comment"].endswith(old_comment), "comment 是这张面孔的变更史，不是缓冲区"
    assert "把两半钉回今天的脸" in after["comment"]
    # 写完必须是绿的第二遍（幂等）：不然 CI 会红一次才"同步"。
    assert ops._cmd_wire(["lock"]) == 0


def test_the_written_file_is_still_well_formed(sandbox_lock) -> None:
    """命令不许把锁写成一个别的形状（键集是 `test_tool_defs_hash_lock.py` 读的）。"""
    assert ops._cmd_wire(["lock", "--write", "--reason", "形状检查"]) == 0
    assert set(json.loads(sandbox_lock.read_text(encoding="utf-8"))) == {
        "comment", "tools", "sha256", "sha256_wire_face"}


# ─── 4 · 名字在登记表上 ──────────────────────────────────────────

def test_the_command_is_registered_where_a_human_finds_it() -> None:
    """一扇命令要出现在它被找的地方：`dhole --help` 的表与 argv 的登记名册。

    只写在源码里的运维面等于不存在（这一批的动机之一正是"余量=0 这件事只有测试知道"）。
    """
    import ast
    import inspect

    src = inspect.getsource(ops.main)
    reg = next(node for node in ast.walk(ast.parse(src))
               if isinstance(node, ast.Assign)
               and any(getattr(t, "id", "") == "_SUBCOMMANDS" for t in node.targets))
    assert "wire" in {e.value for e in reg.value.elts}, ast.unparse(reg)
    assert "dhole wire" in ops._help_epilog()


def test_the_ledger_prints_a_number_that_the_lock_guard_agrees_with(capsys) -> None:
    """账上那个数必须是今天 serve 的那张脸 —— 打印一份别的数是给下一次抬棘轮的人递错账。

    `CONNECT_CEILING` 属主是那条测试（把常数抄到这扇门上就是第二个作者），所以这里比的
    是**测量本身**：命令报的字符数等于 `wire.face_cost` 对默认面算出来的那个。
    """
    from dhole_mcp.wire import face_cost

    _n, chars = face_cost(MasterFetchServer._TOOL_DEFS, DHOLE_INSTRUCTIONS)
    assert ops._cmd_wire(["ledger"]) == 0
    out = capsys.readouterr().out
    assert f"{chars} chars" in out, (chars, out[:300])


def test_an_install_without_a_tests_dir_refuses_by_name(capsys, tmp_path,
                                                        monkeypatch) -> None:
    """装好的 wheel 里没有 `tests/`：要**具名**拒绝，不去新建一份看起来像锁的东西。"""
    monkeypatch.setattr(ops, "_tool_defs_lock_path",
                        lambda: tmp_path / "absent" / "tool_defs_lock.json")
    assert ops._cmd_wire(["lock"]) == 2
    assert "source repo" in capsys.readouterr().err


# ============================================================================
# 来源：test_tool_defs_hash_lock.py
# ============================================================================
# M10（G25 评审）：工具描述的哈希锁。
# 重名消歧：LOCK → LOCK__tool_defs_hash_lock

LOCK__tool_defs_hash_lock = pathlib.Path(__file__).resolve().parent / "tool_defs_lock.json"


def test_the_tool_defs_digest_matches_the_lock():
    lock = json.loads(LOCK__tool_defs_hash_lock.read_text(encoding="utf-8"))
    # 算术只有一个作者（`wire.tool_defs_digest`），写锁的人只有一个（`dhole wire lock
    # --write`）—— 这一处只当读者。从前这里自己算一遍是第三份作者，而三份作者里最容易
    # 漂的是序列化口径，口径一漂，比对就失去意义（口径本身由下面那条黄金值钉）。
    digest = tool_defs_digest(MasterFetchServer._TOOL_DEFS)
    assert digest == lock["sha256"], (
        "tools/list 的字节变了而锁没动（或反过来）。两种都可能是对的，但必须"
        "二选一地说出来：改了代码就跑 `dhole wire lock --write --reason \"…\"`（差额与"
        "理由一起进 comment）；没改代码就查依赖/构建链路里谁动了描述。")
    assert len(MasterFetchServer._TOOL_DEFS) == lock["tools"], (
        "工具颗数与锁不一致 —— 同一把锁的两半不许各说各话")


def test_the_lock_file_itself_is_well_formed():
    lock = json.loads(LOCK__tool_defs_hash_lock.read_text(encoding="utf-8"))
    assert set(lock) == {"comment", "tools", "sha256", "sha256_wire_face"}
    assert len(lock["sha256"]) == 64 and all(
        c in "0123456789abcdef" for c in lock["sha256"]), "不是 sha256 的形状"
    assert len(lock["comment"]) > 50, "锁没有 comment 就是没有人说得清它为什么变"


def test_the_lock_also_pins_the_default_wire_face():
    """批次4 刀4（F-C1-1）：锁从「工具描述」扩到「整个连接期面孔」。

    doctor 的 wire face digest 行（`updater._append_wire_cost`）报的是这条进程实际
    serve 的脸；锁里的 `sha256_wire_face` 存默认面（全部工具的 minimal 投影 ＋
    DHOLE_INSTRUCTIONS）的同款指纹。从前"各算各的"是刻意的双记账，而它买到的独立性
    抵不上它付出去的代价：三份算术里只要有一份换了序列化口径，比对就只是**看起来**
    在对。现在口径只有一处（`wire.wire_face_digest`），独立性改由下面那条黄金值守 ——
    钉的是算法本身，与今天serve 的数据无关。
    """
    lock = json.loads(LOCK__tool_defs_hash_lock.read_text(encoding="utf-8"))
    digest = wire_face_digest(MasterFetchServer._TOOL_DEFS, DHOLE_INSTRUCTIONS)
    assert digest == lock["sha256_wire_face"], (
        "默认连接期面孔变了而锁没动（或反过来）—— 面孔的基线与 doctor 的 "
        "wire face digest 行对不上，升级比对就失去意义")


def test_the_digest_arithmetic_is_pinned_by_a_golden_value():
    """一把算术要有一个与今天 serve 的数据**无关**的黄金值，否则换口径没人知道。

    这一条补的是上面两处改成"只当读者"之后失去的那份独立性：数据一样时，读者与作者
    永远一致，哪怕作者自己换了序列化口径。所以这里喂一份固定的假工具表，里面刻意
    放着口径的三个易错位 —— 非 ASCII（`ensure_ascii=False`）、键的顺序（`sort_keys`）、
    以及一颗**没有 annotations** 的工具（投影不许替它补一个空字典：宿主读的是"这颗没
    声明"，补出来就是一张没人 serve 的脸）。
    """
    gold = [
        {"name": "alpha", "description": "does a thing · 一句中文",
         "inputSchema": {"type": "object",
                         "properties": {"x": {"type": "string",
                                              "description": "one"}}},
         "annotations": {"readOnlyHint": True}},
        {"name": "beta", "description": "does another",
         "inputSchema": {"type": "object", "required": ["y"], "properties": {}}},
    ]
    assert sorted(minimal_face(gold)[1]) == ["description", "inputSchema", "name"]
    assert tool_defs_digest(gold) == (
        "be011dda94f26815693eb6a7fa2c928a6229b3cd20cdf4d7f2398ad68af27a8a")
    assert wire_face_digest(gold, "instructions 文本\n") == (
        "5ee444be93fa57654cbc2d1fc7e4da965b193d771553af2d5e8aa55213cb70ad")


# ============================================================================
# 来源：test_two_counts_in_a_batch_envelope_say_two_things.py
# ============================================================================
# R01-031 · 一个信封里两个数，就得说清各自数的是什么。

def _row(url: str, *, status: int = 200, content_ok: bool = True,
         error: str = "") -> ResponseModel:
    return ResponseModel(url=url, status=status, content_ok=content_ok,
                         content=["text"] if content_ok else [],
                         fetcher_used="http", error=error)


def _dump(**kw):
    return BulkResponseModel(**kw).model_dump(mode="json")


def test_the_second_count_is_derived_from_the_rows_not_from_the_caller():
    rows = [_row("http://a/", content_ok=True),
            _row("http://b/", content_ok=False),          # 200 但是登录墙
            _row("http://c/", status=404, content_ok=False, error="http_error_404: x"),
            _row("http://d/", content_ok=False)]          # 正文被截成空
    data = _dump(results=rows, total=4, successful=3)
    assert data["successful"] == 3, "第一颗数不许被顺手动掉（它有自己的定义）"
    assert data["usable"] == 1, data["usable"]


def test_the_envelope_says_which_question_each_number_answers():
    rows = [_row("http://a/"), _row("http://b/", content_ok=False),
            _row("http://c/", content_ok=False), _row("http://d/", content_ok=False)]
    text = _dump(results=rows, total=4, successful=3)["summary"]
    assert "successful" in text and "content_ok" in text, text
    assert "3" in text and "1" in text, text
    assert "different questions" in text, text


def test_a_lying_usable_value_is_overwritten_by_the_rows():
    """两颗数只能有一个作者：行。调用方（或将来某条路径）填错了也必须被纠正。"""
    rows = [_row("http://a/"), _row("http://b/", content_ok=False)]
    data = _dump(results=rows, total=2, successful=2, usable=99)
    assert data["usable"] == 1


def test_every_row_usable_still_reports_both_counts():
    rows = [_row("http://a/"), _row("http://b/")]
    data = _dump(results=rows, total=2, successful=2)
    assert data["usable"] == 2
    assert "2" in data["summary"]


def test_a_refused_batch_says_zero_usable():
    """批量与 attach 不兼容那条路（R01-031 的兄弟现场）：交回的是拒绝行，
    一颗可用的都没有 —— 这一格必须是 0，而不是缺席。"""
    rows = [_row("http://a/", content_ok=False, error="invalid_request: x")]
    data = _dump(results=rows, total=1, successful=0)
    assert data["usable"] == 0 and data["successful"] == 0, data


def test_both_fields_are_declared_and_never_trimmed_off_the_wire():
    """`响应内无定义` 的另一半修复在 schema：两颗数都得有说明，
    而且不许被上线裁剪当成默认值省掉（那正是"看不见"的来源）。"""
    fields = BulkResponseModel.model_fields
    assert "content_ok" in fields["usable"].description
    assert "status<400" in fields["successful"].description
    omit = _WIRE_OMIT.get("BulkResponseModel") or frozenset()
    assert "usable" not in omit and "summary" not in omit, omit


# ============================================================================
# 来源：test_a_pdf_reports_how_many_pages_it_has_and_returned.py
# ============================================================================
# A PDF response must say how much of the file it read.

HERE = Path(__file__).resolve().parent
THREE_PAGE_PDF = HERE / "wrapped_heading.pdf"


def _pdf_response(pages: str | None = None):
    body = THREE_PAGE_PDF.read_bytes()
    if pages is not None:
        from dhole_mcp import context

        token = context._PDF_PAGES.set(pages)
        try:
            return _extract_pdf_response(
                body, "application/pdf", len(body), "https://docs.test/paper.pdf",
                "markdown", "http", 1.0)
        finally:
            context._PDF_PAGES.reset(token)
    return _extract_pdf_response(
        body, "application/pdf", len(body), "https://docs.test/paper.pdf",
        "markdown", "http", 1.0)


def test_a_whole_pdf_reports_its_page_count():
    out = _pdf_response()

    assert out.pages_total == 3, out.pages_total
    assert out.pages_returned == 3, out.pages_returned


def test_a_page_range_reports_what_was_extracted_not_the_whole_file():
    out = _pdf_response("1-2")

    assert out.pages_total == 3
    assert out.pages_returned == 2


def test_doc_parse_carries_the_same_two_numbers():
    out = asyncio.run(MasterFetchServer().doc_parse(
        str(THREE_PAGE_PDF), pages="1-2"))

    assert out.pages_total == 3, out.pages_total
    assert out.pages_returned == 2, out.pages_returned


def test_a_non_pdf_says_zero_on_both_fields(tmp_path):
    note = tmp_path / "note.txt"
    note.write_text("plain text, no pages", encoding="utf-8")

    out = asyncio.run(MasterFetchServer().doc_parse(str(note)))

    assert out.pages_total == 0
    assert out.pages_returned == 0


def test_the_two_fields_are_omitted_from_the_wire_at_their_defaults():
    """0/0 is not a measurement; a non-PDF response must not pay for it."""
    from dhole_mcp.models import ResponseModel
    from dhole_mcp.wire import _WIRE_OMIT

    assert {"pages_total", "pages_returned"} <= _WIRE_OMIT["ResponseModel"]
    plain = ResponseModel(status=200, url="https://x.test/", content=["x"])
    assert plain.pages_total == 0 and plain.pages_returned == 0
