"""凭据来源（vault）—— 计划第七章「凭据来源」。

这里钉住的**只有一件事**，但它是最要紧的那件：**凭据值不出境**。

* `list_items` / `match_for_url` / `status` 的返回值里**不含密码或 TOTP 的值** ——
  连一次都不含。测试不是"检查某个字段为空"，而是把整个返回对象序列化后断言
  那个秘密串**一次都没出现**（"没有那个字段"和"没有那个值"是两件事，前者会被
  以后新增的字段悄悄绕过）。
* `VaultModel` 的字段表里没有能装密码的地方 —— 接口形状层面的保证。
* `secret_for` 是内部接口，它的失败路径**不能**回显 stdout（取密码的命令，
  stdout 就是密码）。
* CLI 缺失 / 锁着 / 没登录，都要**如实说**并给出修法，而不是静默当成"没有凭据"。

CLI 边界（`vault._run` / `vault._which`）用桩替换，所以这些用例不依赖机器上装了
`bw` 或 `op`。
"""

from __future__ import annotations

import json

import pytest

from dhole_mcp import vault


BW_ITEM = {
    "id": "11111111-2222-3333-4444-555555555555",
    "name": "Example — work",
    "login": {
        "username": "alice@example.com",
        "password": "hunter2-correct-horse",
        "totp": "JBSWY3DPEHPK3PXP",
        "uris": [{"uri": "https://app.example.com/login"},
                 {"uri": "example.com"}],
    },
}

OP_ITEM_LIST = [
    {"id": "op-item-1", "title": "Example", "urls": [{"href": "https://example.com"}],
     "vault": {"id": "v1", "name": "Private"}, "category": "LOGIN"},
    {"id": "op-item-2", "title": "Unrelated", "urls": [{"href": "https://other.test"}],
     "vault": {"id": "v1", "name": "Private"}, "category": "LOGIN"},
]

OP_ITEM_GET = {
    "id": "op-item-1", "title": "Example",
    "fields": [
        {"id": "username", "type": "STRING", "label": "username",
         "value": "alice@example.com"},
        {"id": "password", "type": "CONCEALED", "label": "password",
         "value": "hunter2-correct-horse"},
        {"id": "notesPlain", "type": "STRING", "label": "notes", "value": "hi"},
    ],
}

BW_PASSWORD = "hunter2-correct-horse"
OP_PASSWORD = "hunter2-correct-horse"


class FakeCLI:
    """Replace the CLI boundary: exact (source, args) -> (rc, stdout, stderr)."""

    def __init__(self, monkeypatch, responses, binaries=("bitwarden",)):
        self.calls: list[tuple[str, list]] = []
        self.responses = responses
        installed = set(binaries)

        monkeypatch.setattr(
            vault, "_which",
            lambda s: (f"/fake/bin/{vault._BINARY[s]}" if s in installed else None))

        def fake_run(source, args, timeout=None):
            self.calls.append((source, list(args)))
            return self.responses.get((source, tuple(args)), (1, "", "not stubbed"))

        monkeypatch.setattr(vault, "_run", fake_run)


@pytest.fixture
def cli(monkeypatch):
    def build(responses, binaries=("bitwarden",)):
        return FakeCLI(monkeypatch, responses, binaries)
    return build


# ─── 主机匹配 ────────────────────────────────────────────────────────────

class TestHostMatching:
    @pytest.mark.parametrize("raw,host", [
        ("https://app.example.com/login?x=1", "app.example.com"),
        ("example.com", "example.com"),
        ("HTTP://Example.COM:8443/x", "example.com"),
        ("https://user:pw@example.com/x", "example.com"),
        ("", ""),
        ("://nonsense", ""),
    ])
    def test_host_of(self, raw, host):
        assert vault._host_of(raw) == host

    @pytest.mark.parametrize("a,b,hit", [
        ("app.example.com", "example.com", True),
        ("example.com", "app.example.com", True),
        ("example.com", "example.com", True),
        ("notexample.com", "example.com", False),
        ("example.com.evil.test", "example.com", False),
        ("", "example.com", False),
    ])
    def test_host_matches(self, a, b, hit):
        assert vault._host_matches(a, b) is hit


# ─── 值不出境 ────────────────────────────────────────────────────────────

class TestValuesNeverLeave:
    def test_list_items_carries_no_password_or_totp_value(self, cli):
        c = cli({("bitwarden", ("list", "items")): (0, json.dumps([BW_ITEM]), "")})
        got = vault.list_items()
        blob = json.dumps(got, ensure_ascii=False)
        assert BW_PASSWORD not in blob, "密码值出现在元数据里了"
        assert BW_ITEM["login"]["totp"] not in blob, "TOTP 值出现在元数据里了"
        assert got["items"][0]["has_password"] is True
        assert got["items"][0]["has_totp"] is True
        # 用户名算标识符不算秘密，是刻意回的（见模块 docstring）。
        assert got["items"][0]["username"] == "alice@example.com"
        assert c.calls == [("bitwarden", ["list", "items"])]

    def test_match_for_url_carries_no_password_value(self, cli):
        cli({("bitwarden", ("list", "items")): (0, json.dumps([BW_ITEM]), "")})
        got = vault.match_for_url("https://app.example.com/")
        assert got["total"] == 1
        assert BW_PASSWORD not in json.dumps(got, ensure_ascii=False)

    def test_the_vault_response_model_has_no_place_to_put_a_secret(self):
        """接口形状层面的保证：模型里没有能装密码的字段。

        比"记得脱敏"强：以后有人加一个 `password: str = ""` 字段，这条会红。
        """
        from dhole_mcp.server import VaultModel

        fields = set(VaultModel.model_fields)
        assert not {f for f in fields if "password" in f or "secret" in f
                    or "totp" in f.lower()}, \
            f"VaultModel 出现了可能承载秘密的字段：{sorted(fields)}"
        # items 是 List[Dict]，值是自由的 —— 所以上面那条"序列化后不含值"的
        # 用例才是真正的守卫，这条只管字段名。

    def test_a_manager_error_is_scrubbed_of_the_session_token(self, cli, monkeypatch):
        monkeypatch.setenv("BW_SESSION", "supersecrettokenvalue123")
        cli({("bitwarden", ("status",)): (
            1, "", "Vault is locked. session=supersecrettokenvalue123")})
        rows = vault.status()
        blob = json.dumps(rows, ensure_ascii=False)
        assert "supersecrettokenvalue123" not in blob
        assert "***" in blob
        assert rows[0]["available"] is True and rows[0]["unlocked"] is False


# ─── 状态 ────────────────────────────────────────────────────────────────

class TestStatus:
    def test_nothing_installed_is_available_false_and_unlocked_unknown(self, cli):
        cli({}, binaries=())
        rows = vault.status()
        assert [r["source"] for r in rows] == ["bitwarden", "onepassword"]
        for r in rows:
            assert r["available"] is False
            assert r["unlocked"] is None, "没装 ≠ 锁着；没查过就说 None"
            assert r["hint"], "缺 CLI 要给出装法"

    def test_bitwarden_unlocked_and_locked(self, cli):
        cli({("bitwarden", ("status",)): (0, json.dumps({"status": "unlocked"}), "")})
        assert vault.status("bitwarden")[0]["unlocked"] is True

        cli({("bitwarden", ("status",)): (0, json.dumps({"status": "locked"}), "")})
        row = vault.status("bitwarden")[0]
        assert row["unlocked"] is False and row["hint"]

    def test_one_password_uses_whoami(self, cli):
        cli({("onepassword", ("whoami", "--format", "json")): (0, "{}", "")},
            binaries=("onepassword",))
        row = vault.status("onepassword")[0]
        assert row["available"] is True and row["unlocked"] is True

    def test_an_unknown_source_says_so(self, cli):
        cli({})
        row = vault.status("keepass")[0]
        assert row["available"] is False
        assert "keepass" in row["detail"]


# ─── 列表 ────────────────────────────────────────────────────────────────

class TestListItems:
    def test_truncation_is_reported_not_hidden(self, cli):
        many = [dict(BW_ITEM, id=f"id-{i}", name=f"item {i}") for i in range(10)]
        cli({("bitwarden", ("list", "items")): (0, json.dumps(many), "")})
        got = vault.list_items(limit=4)
        assert len(got["items"]) == 4 and got["total"] == 10
        assert got["truncated"] is True

    def test_a_locked_vault_is_a_note_with_the_fix(self, cli):
        cli({("bitwarden", ("list", "items")): (
            1, "", "Vault is locked.")})
        got = vault.list_items()
        assert got["items"] == []
        assert any("locked" in n.lower() and "BW_SESSION" in n for n in got["notes"])

    def test_a_missing_cli_is_a_note_not_an_error(self, cli):
        cli({}, binaries=())
        got = vault.list_items()
        assert got["items"] == [] and len(got["notes"]) == 2
        assert all("not found on PATH" in n for n in got["notes"])

    def test_non_json_output_is_reported(self, cli):
        cli({("bitwarden", ("list", "items")): (0, "<html>nope</html>", "")})
        got = vault.list_items()
        assert got["items"] == [] and any("not return JSON" in n for n in got["notes"])

    def test_the_query_reaches_bitwarden_as_search(self, cli):
        c = cli({("bitwarden", ("list", "items", "--search", "example")):
                 (0, json.dumps([BW_ITEM]), "")})
        vault.list_items("example")
        assert c.calls == [("bitwarden", ["list", "items", "--search", "example"])]

    def test_one_password_has_password_is_unknown_not_false(self, cli):
        """`op item list` 不回字段值，所以"有没有密码"在这一层是**未知**。

        报 False 就是把"没查"说成"没有" —— 与 watch 首跑报 changed=false 同一类
        错误。
        """
        cli({("onepassword", ("item", "list", "--format", "json")):
             (0, json.dumps(OP_ITEM_LIST), "")}, binaries=("onepassword",))
        got = vault.list_items()
        assert got["total"] == 2
        assert all(it["has_password"] is None for it in got["items"])
        assert all(it["has_totp"] is None for it in got["items"])

    def test_one_password_query_filters_on_the_title(self, cli):
        cli({("onepassword", ("item", "list", "--format", "json")):
             (0, json.dumps(OP_ITEM_LIST), "")}, binaries=("onepassword",))
        got = vault.list_items("example")
        assert [it["name"] for it in got["items"]] == ["Example"]


class TestMatchForUrl:
    def test_it_matches_a_subdomain_of_the_stored_uri(self, cli):
        cli({("bitwarden", ("list", "items")): (0, json.dumps([BW_ITEM]), "")})
        got = vault.match_for_url("https://app.example.com/dashboard")
        assert got["host"] == "app.example.com"
        assert got["total"] == 1 and got["items"][0]["matched_uri"] == \
            "https://app.example.com/login"

    def test_a_lookalike_domain_does_not_match(self, cli):
        cli({("bitwarden", ("list", "items")): (0, json.dumps([BW_ITEM]), "")})
        got = vault.match_for_url("https://notexample.com/")
        assert got["total"] == 0

    def test_an_unparseable_url_says_so(self, cli):
        cli({})
        got = vault.match_for_url("")
        assert got["total"] == 0 and got["notes"]


# ─── secret_for（内部接口） ──────────────────────────────────────────────

class TestSecretFor:
    def test_bitwarden_resolves_username_password_totp(self, cli):
        cli({("bitwarden", ("get", "item", "Example — work")):
             (0, json.dumps(BW_ITEM), "")})
        got = vault.secret_for("Example — work")
        assert got["password"] == BW_PASSWORD
        assert got["username"] == "alice@example.com"
        assert got["totp"] == BW_ITEM["login"]["totp"]
        assert got["item"] == "Example — work" and got["source"] == "bitwarden"

    def test_one_password_resolves_the_concealed_field(self, cli):
        cli({("onepassword", ("item", "get", "Example", "--format", "json")):
             (0, json.dumps(OP_ITEM_GET), "")}, binaries=("onepassword",))
        got = vault.secret_for("Example")
        assert got["password"] == OP_PASSWORD
        assert got["username"] == "alice@example.com"
        assert got["item"] == "Example" and got["source"] == "onepassword"

    def test_a_missing_item_names_what_was_tried(self, cli):
        cli({}, binaries=("bitwarden", "onepassword"))
        got = vault.secret_for("nope")
        assert got["error"]
        assert "bitwarden" in got["error"] and "onepassword" in got["error"]

    def test_the_failure_path_never_echoes_stdout(self, cli):
        """取密码的命令，它的 stdout 就是密码 —— 失败路径绝不能回显它。"""
        cli({("bitwarden", ("get", "item", "Example")):
             (0, "hunter2-correct-horse\n", "")})
        got = vault.secret_for("Example")
        assert got["error"] and "not JSON" in got["error"]
        assert BW_PASSWORD not in json.dumps(got, ensure_ascii=False)

    def test_no_manager_installed_names_the_install_step(self, cli):
        cli({}, binaries=())
        got = vault.secret_for("Example")
        assert got["error"]
        assert "bw" in got["error"] or "Bitwarden" in got["error"]

    def test_an_empty_reference_is_refused(self, cli):
        cli({})
        assert vault.secret_for("   ")["error"]

    def test_an_unknown_source_is_refused(self, cli):
        cli({})
        assert "unknown source" in vault.secret_for("x", "keepass")["error"]

    def test_an_item_without_a_password_says_so(self, cli):
        item = dict(BW_ITEM)
        item["login"] = {"username": "alice", "uris": []}
        cli({("bitwarden", ("get", "item", "Example")): (0, json.dumps(item), "")})
        got = vault.secret_for("Example")
        assert "no stored password" in got["error"]


class TestRunBoundary:
    def test_a_missing_binary_returns_minus_one_instead_of_raising(self, monkeypatch):
        monkeypatch.setattr(vault, "_which", lambda s: None)
        rc, out, err = vault._run("bitwarden", ["status"])
        assert rc == -1 and out == "" and "not found" in err

    def test_a_spawn_failure_returns_minus_one_instead_of_raising(self, monkeypatch):
        monkeypatch.setattr(vault, "_which", lambda s: "/fake/bin/bw")
        import subprocess

        def boom(*a, **k):
            raise OSError("nope")

        monkeypatch.setattr(subprocess, "run", boom)
        rc, out, err = vault._run("bitwarden", ["status"])
        assert rc == -1 and "OSError" in err
