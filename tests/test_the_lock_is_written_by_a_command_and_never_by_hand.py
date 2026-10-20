"""锁由一扇命令写，不由手抄 —— 而那句"不许手抄"现在有人执行了。

`tests/tool_defs_lock.json` 的 docstring 早就写着"锁文件……**不许手抄**"（阶段 7 的作者
手抄时丢过开头的 "3"）。那句话立了将近十刀，而它的执行方式是"记得去算" —— 一条只能被
记住的规矩迟早被破，破的时候没有任何东西响。这一刀把算的人换成命令：`dhole wire lock`
读，`dhole wire lock --write --reason "…"` 写，而算术本身在 `wire.py` 里只有一份
（doctor 那一行、锁的两半、这两处读者都调用它）。

四件事，都不是措辞：

1. §1 命令与文件当下 agrees（这就是 CI 里那条红/绿的等价物，而它说的是同一串字节）。
2. §2 写锁**必须**附一句理由 —— 没有理由的改写不是更新锁，是抹掉一次决定，而这份
   comment 就是这张面孔唯一的变更史。
3. §3 `--write` 在临时目录里跑一次完整往返：写出来的两半正好是命令自己算的那两个，
   comment 长了那一行而旧文一字不丢，工具数同颗。
4. §4 名字要在登记表上：`_SUBCOMMANDS` 与 `dhole --help` 的命令表都得有它，否则它是一扇
   写得动东西却没人知道的门（README 那一侧由 `test_readme_tells_the_truth.py` 反向管着）。
"""

from __future__ import annotations

import json

import pytest

from dhole_mcp import ops
from dhole_mcp.server import DHOLE_INSTRUCTIONS, MasterFetchServer
from dhole_mcp.wire import tool_defs_digest, wire_face_digest

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
