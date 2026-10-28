"""状态面问的是"这台机器上有什么"，不是"这个进程手里有什么"。

现场（2026-10-27 全量 MCP 覆盖）：进程 1 开着 `DHOLE_PERSIST_CREDENTIALS=1` 写入
`~/.dhole/sessions.db`，进程 2（内存模式，MCP 服务进程）里
`sys_state(clear=['persist-test'])` 回来 `unknown_state_token`。凭据确实躺在磁盘上，
而状态面既列不出、也抹不掉 —— 调用方因此确认不了它、更没有工具能清掉它。

两处成因，这里各钉一面：

1. 普查只读**本进程**的罐。内存模式的进程不会去读 `sessions.db`，于是磁盘上那份
   属于另一个进程的会话在它眼里不存在。
2. 更坏的一条：内存模式第一次打开罐时，`_absorb_legacy_file` 会把**任何**活行
   吸进内存并删掉文件。它假设文件一定是 pre-17.1 的明文罐；可那个文件里是
   `dpapi:` 加密值时，密文会被当成 cookie 值，另一个进程的持久罐还会被删除。

修法：内存模式照样**只读**磁盘那一份做普查、照样能 DELETE 那些行（删行不需要
解密），但绝不吸收密文、绝不删文件；每行带 `store` 说明它在哪个罐里、本进程能不能
取用。文件由本进程持有时（持久模式）不重复读，一颗 cookie 不会数成两颗。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from dhole_mcp import secretbox, sessions, wire
from dhole_mcp.server import MasterFetchServer

CREATE = (
    "CREATE TABLE cookies (session_id TEXT NOT NULL, host TEXT NOT NULL,"
    " name TEXT NOT NULL, value TEXT NOT NULL, expires_at REAL NOT NULL,"
    " PRIMARY KEY (session_id, host, name))"
)
HOST = "shop.example.com"
URL = f"https://{HOST}/account"
# 持久模式写进文件的值一定是这个形状；内存模式不认识它，也不该假装认识。
ENCRYPTED = "dpapi:Zm9v"


def _write_disk_jar(path: Path, rows) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(CREATE)
    conn.executemany("INSERT INTO cookies VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def _disk_rows(path: Path) -> list:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT session_id, host, name FROM cookies").fetchall()
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_a_disk_session_is_listed_and_the_encrypted_file_is_left_alone():
    db = Path(sessions._db_path())
    _write_disk_jar(db, [("other-proc", HOST, "sid", ENCRYPTED,
                          time.time() + 3600.0)])

    out = await MasterFetchServer().sys_state()

    rows = {r.session_id: r for r in out.sessions}
    assert "other-proc" in rows, "磁盘上的凭据必须在普查里"
    row = rows["other-proc"]
    assert row.store == "disk", row.store
    assert row.cookies == 1 and row.hosts == {HOST: ["sid"]}
    assert row.browser_open is False, "本进程没有为它开浏览器"
    assert db.exists(), "内存模式不许吸收/删除另一个进程的加密罐"
    assert sessions.header_for("other-proc", URL) == {}, (
        "dpapi: 密文没有被当成 cookie 值吸进内存")
    assert "only in the on-disk jar" in out.message, out.message
    # 标记必须过线；被 wire 压实掉的话，调用方看到的又是一行不知道在哪的会话。
    _text, payload = wire._wire_json(out)
    assert payload["sessions"][0]["store"] == "disk", payload["sessions"][0]


@pytest.mark.asyncio
async def test_a_named_disk_session_is_erased_not_answered_with_unknown():
    db = Path(sessions._db_path())
    _write_disk_jar(db, [("other-proc", HOST, "sid", ENCRYPTED,
                          time.time() + 3600.0)])

    out = await MasterFetchServer().sys_state(clear=["other-proc"])

    assert not out.error, out.error
    assert out.cleared == ["sessions"], out.cleared
    assert out.session_id == "other-proc"
    assert out.closed == 1 and out.cookies_forgotten == 1
    assert _disk_rows(db) == [], "点名要抹的磁盘行必须真的被删掉"
    assert out.sessions == [] and out.open_count == 0, "抹完的普查里不该还剩它"


@pytest.mark.asyncio
async def test_the_same_id_in_both_jars_is_one_row_marked_both():
    db = Path(sessions._db_path())
    _write_disk_jar(db, [("both", HOST, "disk_sid", ENCRYPTED,
                          time.time() + 3600.0)])
    sessions.store("both", URL, {"mem_sid": "value"})

    out = await MasterFetchServer().sys_state()

    rows = [r for r in out.sessions if r.session_id == "both"]
    assert len(rows) == 1, "同一个 id 两行会让'有几个会话'数出两个"
    assert rows[0].store == "memory+disk", rows[0].store
    assert rows[0].cookies == 2, rows[0].cookies
    assert set(rows[0].hosts) == {HOST}, rows[0].hosts
    assert set(rows[0].hosts[HOST]) == {"disk_sid", "mem_sid"}, rows[0].hosts


@pytest.mark.asyncio
async def test_erasing_every_session_reaches_the_disk_jar_too():
    db = Path(sessions._db_path())
    _write_disk_jar(db, [("disk-one", HOST, "sid", ENCRYPTED,
                          time.time() + 3600.0)])
    sessions.store("mem-one", URL, {"mem": "value"})

    out = await MasterFetchServer().sys_state(clear=["sessions"], confirm=True)

    assert not out.error, out.error
    assert out.closed == 2 and out.cookies_forgotten == 2
    assert _disk_rows(db) == []
    assert sessions.sessions() == {}
    assert out.open_count == 0


@pytest.mark.skipif(not secretbox.available(),
                    reason="a file-backed jar needs Windows DPAPI")
def test_a_file_backed_process_counts_each_cookie_once(monkeypatch):
    monkeypatch.setattr(sessions, "_PERSIST", True)
    sessions.reset_for_tests()
    sessions.store("persisted", URL, {"sid": "value"})

    census = sessions.sessions_everywhere()

    assert census["persisted"]["cookies"] == 1, "同一份文件读两次 = 一颗数成两颗"
    assert census["persisted"]["store"] == "disk", (
        "文件由本进程持有时，它仍是所有进程共享的那一份")
