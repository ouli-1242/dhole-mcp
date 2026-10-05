"""17.1 凭据落盘（G25 评审 H4）：默认只在内存，落盘必须加密。

评审的实测：pre-17.1 的 ``sessions.db`` / ``identity.db`` 是明文 SQLite——0600
在 Windows 上是空操作（paths.py 自己承认），同步盘、备份、任何同用户进程都能
直接读到 cookie 值与 Basic 密码。修复是两半，这个文件各钉一面：

    默认（B）：jar 与身份库是 ``:memory:``——存了凭据之后 ``sessions.db`` /
    ``identity.db`` 不存在；重启即空。旧的明文库在首次使用时被吸收进内存并
    **删除文件**——留着它等于默认模式在撒谎。

    落盘（A）：``DHOLE_PERSIST_CREDENTIALS=1`` 时值经 Windows DPAPI 加密——
    库文件的原始字节里找不到凭据，读回 round-trip 成立；旧明文值首次使用时
    原地重加密。没有加密器的平台（非 Windows）宁可保持内存也不回退明文。
"""

from __future__ import annotations

import sqlite3
import sys
import time

import pytest

from dhole_mcp import identity, secretbox, sessions

ME = "https://shop.example.com/account"
SECRET = "sess-token-hunter2"


def _write_legacy_sessions_db(path, rows):
    """A pre-17.1 jar: plaintext cookie VALUES, exactly what the review read."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE cookies (session_id TEXT NOT NULL, host TEXT NOT NULL,"
        " name TEXT NOT NULL, value TEXT NOT NULL, expires_at REAL NOT NULL,"
        " PRIMARY KEY (session_id, host, name))")
    conn.executemany("INSERT INTO cookies VALUES (?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def _write_legacy_identity_db(path, rows):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE defaults (key TEXT NOT NULL, scope TEXT NOT NULL DEFAULT '',"
        " value TEXT NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY (key, scope))")
    conn.executemany("INSERT INTO defaults VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.close()


# ─── 默认：内存，什么都不落盘 ────────────────────────────────────────

class TestTheDefaultIsMemory:

    def test_the_jar_never_touches_disk(self, tmp_path):
        sessions.store("bob", ME, {"sid": SECRET})
        assert not (tmp_path / "sessions.db").exists(), \
            "内存模式下存凭据不应该产生任何文件"
        assert sessions.header_for("bob", ME) == {"sid": SECRET}

    def test_a_restart_empties_the_jar(self):
        sessions.store("bob", ME, {"sid": SECRET})
        sessions.reset_for_tests()
        assert sessions.sessions() == {}, "重启（新连接）后默认一无所有"

    def test_the_identity_store_never_touches_disk(self, tmp_path):
        identity.set_value("proxy", "http://127.0.0.1:7890")
        assert not (tmp_path / "identity.db").exists()
        assert identity.get("proxy") == "http://127.0.0.1:7890"

    def test_a_legacy_plaintext_jar_is_absorbed_into_memory_and_deleted(
            self, tmp_path, caplog):
        """升级路径：旧明文库里的活 cookie 先继续能用，然后文件被删——
        把明文凭据留在盘上、嘴上说"不落盘"，是同一个谎的两半。"""
        db = tmp_path / "sessions.db"
        _write_legacy_sessions_db(db, [(
            "bob", "shop.example.com", "sid", SECRET,
            time.time() + 3600.0,
        ), (
            "bob", "shop.example.com", "dead", "stale",
            time.time() - 1.0,
        )])
        sessions.reset_for_tests()

        census = sessions.sessions()
        assert census["bob"]["cookies"] == 1, "过期的行不被复活"
        assert sessions.header_for("bob", ME) == {"sid": SECRET}
        assert not db.exists(), "吸收完成后明文文件必须消失"

    def test_a_legacy_plaintext_identity_is_absorbed_and_deleted(self, tmp_path):
        db = tmp_path / "identity.db"
        _write_legacy_identity_db(db, [(
            "proxy", "", '"http://127.0.0.1:7890"', time.time(),
        )])
        identity.close()

        assert identity.get("proxy") == "http://127.0.0.1:7890", \
            "操作者选过的默认在本进程内继续有效"
        assert not db.exists()


# ─── 落盘：DPAPI 加密 ────────────────────────────────────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI 是 Windows 的")
class TestTheEncryptedFile:

    def test_a_persisted_cookie_value_is_not_plaintext_on_disk(
            self, monkeypatch, tmp_path):
        monkeypatch.setattr(sessions, "_PERSIST", True)
        sessions.reset_for_tests()
        sessions.store("bob", ME, {"sid": SECRET})

        db = tmp_path / "sessions.db"
        assert db.exists(), "落盘模式要真有文件"
        raw = db.read_bytes()
        assert SECRET.encode() not in raw, "cookie 值明文出现在库里"
        assert b"dpapi:" in raw, "值应该是带 dpapi: 前缀的密文"
        assert sessions.header_for("bob", ME) == {"sid": SECRET}, "读回要能解开"

    def test_a_legacy_plaintext_file_is_reencrypted_in_place(
            self, monkeypatch, tmp_path):
        """落盘模式的升级路径：旧明文值首次使用即重加密，明文不再留盘。"""
        db = tmp_path / "sessions.db"
        _write_legacy_sessions_db(db, [(
            "bob", "shop.example.com", "sid", SECRET, time.time() + 3600.0,
        )])
        monkeypatch.setattr(sessions, "_PERSIST", True)
        sessions.reset_for_tests()

        assert sessions.header_for("bob", ME) == {"sid": SECRET}
        assert SECRET.encode() not in db.read_bytes(), "迁移后库里还有明文"
        assert b"dpapi:" in db.read_bytes()

    def test_a_persisted_identity_value_is_not_plaintext_on_disk(
            self, monkeypatch, tmp_path):
        monkeypatch.setattr(identity, "_PERSIST", True)
        identity.close()
        identity.set_value("auth", {"type": "basic", "username": "u",
                                    "password": "hunter2-super-secret"},
                           "shop.example.com")

        db = tmp_path / "identity.db"
        raw = db.read_bytes()
        assert b"hunter2-super-secret" not in raw
        assert b"dpapi:" in raw
        assert identity.get("auth", "shop.example.com") == {
            "type": "basic", "username": "u", "password": "hunter2-super-secret"}

    def test_a_second_process_read_the_same_file_back(self, monkeypatch, tmp_path):
        """DPAPI 绑定的是 Windows 用户，不是进程：close（=模拟重启）后重开，
        同一用户照常解开。这正是落盘模式存在的意义。"""
        monkeypatch.setattr(sessions, "_PERSIST", True)
        sessions.reset_for_tests()
        sessions.store("bob", ME, {"sid": SECRET})
        sessions.reset_for_tests()  # 连接关掉、文件留在原地
        assert sessions.header_for("bob", ME) == {"sid": SECRET}


@pytest.mark.skipif(sys.platform == "win32", reason="只在没有 DPAPI 的平台走这条")
class TestWithoutAnEncryptor:

    def test_persist_requested_without_dpapi_stays_in_memory(
            self, monkeypatch, tmp_path):
        monkeypatch.setattr(sessions, "_PERSIST", True)
        monkeypatch.setattr(secretbox, "available", lambda: False)
        sessions.reset_for_tests()
        sessions.store("bob", ME, {"sid": SECRET})
        assert not (tmp_path / "sessions.db").exists(), \
            "没有加密器就落明文盘，宁可拒存"
        assert sessions.header_for("bob", ME) == {"sid": SECRET}


# ─── secretbox 自身 ──────────────────────────────────────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI 是 Windows 的")
class TestTheSecretbox:

    def test_a_value_round_trips(self):
        boxed = secretbox.protect(SECRET)
        assert boxed.startswith("dpapi:")
        assert SECRET.encode() not in boxed.encode()
        assert secretbox.unprotect(boxed) == SECRET

    def test_unicode_round_trips(self):
        value = "令和-cookie-值-🎯"
        assert secretbox.unprotect(secretbox.protect(value)) == value

    def test_a_value_without_the_prefix_passes_through(self):
        """无前缀 = pre-17.1 明文：unprotect 放行是为了一次性迁移能读到它，
        不是存储格式——迁移完成后的新值全都带前缀。"""
        assert secretbox.unprotect("plain-old-value") == "plain-old-value"
