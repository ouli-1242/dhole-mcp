# -*- coding: utf-8 -*-
"""R02-F64 · 门看的是**目录**，不是「名字以它开头」（R02-079 · P2）。

现场（本机，真实调用）：把 TEMP 指到 `C:\\Users\\ouli\\.dhole-tmp` 之后，全量 pytest
一口气红了 101 格 —— 每一格的错误都是同一句：

    Access denied: path is in a restricted system directory (c:\\users\\ouli\\.dhole)

`~\\.dhole-tmp` 不是 `~\\.dhole` 的孩子，它是**同一个父亲下的另一个目录**。门用的
是 `norm.startswith(blocked)`（`server.py:4654`），于是名字蹭上前缀的兄弟目录一起被挡。
被冤枉的当然不止临时目录：

| 路径 | 修前 | 该不该挡 |
|---|---|---|
| `~\\.dhole-tmp\\a.txt` | 拒 | 不 |
| `~\\.dholestuff\\b.txt` | 拒 | 不 |
| `~\\.dholeX\\f.txt` | 拒 | 不 |
| `~\\.dhole\\config.json` | 拒 | **要**（那是 dhole 自己的秘密） |
| `~\\.dhole\\nested\\c.txt` | 拒 | **要**（它的孩子） |

同一把尺还量到凭据文件那一组：`~\\.env` 是要挡的**一颗文件**，而 `~\\.envrc`、
`~\\.environment\\prod.txt` 蹭前缀也被挡 —— 那句话对用户说的是"这颗在受限系统目录里"，
而真正的答案是"你的名字和我的黑名单撞了三个字符"。

这一刀只改**边界**，不改名单：挡住的还是那一串，一个字节都不许少（`blocked_dirs`
里每一颗自己仍要拒、它的孩子仍要拒 —— 那是安全方向，不许为了绿灯放水）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dhole_mcp.server import _blocked_path_prefix  # noqa: E402

HOME = os.path.expanduser("~").replace("/", "\\") if os.name == "nt" else os.path.expanduser("~")


def _p(*parts):
    sep = "\\" if os.name == "nt" else "/"
    return HOME + sep + sep.join(parts)


# ── 格 1：兄弟目录不再被冤枉 ────────────────────────────────────────────

class TestASiblingIsNotTheDirectory:

    @pytest.mark.parametrize("name", [".dhole-tmp", ".dholestuff", ".dholeX",
                                      ".ssh-tmp", ".aws-keys", ".kubelet"])
    def test_a_name_that_shares_a_prefix_is_not_the_blocked_directory(self, name):
        got = _blocked_path_prefix(_p(name, "notes.md"))
        assert got == "", (
            f"{name} 不是 {name.split('-')[0]} 的孩子，只是名字蹭了前缀：{got!r}")

    @pytest.mark.parametrize("name", [".envrc", ".environment", ".env.local",
                                      ".bash_history.bak", ".netrc.d"])
    def test_a_file_that_shares_a_credential_name_is_not_that_credential(self, name):
        assert _blocked_path_prefix(_p(name)) == "", name


# ── 格 2：名单一个不许少（安全方向）─────────────────────────────────────

class TestTheListIsStillTheList:

    @pytest.mark.parametrize("rel", [".dhole", ".ssh", ".gnupg", ".aws", ".kube",
                                     ".azure", ".config/gcloud", ".config/gh",
                                     ".env", ".bash_history", ".zsh_history",
                                     ".git-credentials", ".netrc"])
    def test_every_blocked_name_still_blocks_itself(self, rel):
        path = _p(*rel.split("/"))
        assert _blocked_path_prefix(path) != "", path

    @pytest.mark.parametrize("rel", [[".dhole", "config.json"],
                                     [".ssh", "id_ed25519"],
                                     [".dhole", "nested", "deeper.json"]])
    def test_every_blocked_name_still_blocks_its_children(self, rel):
        assert _blocked_path_prefix(_p(*rel)) != "", rel

    def test_the_system_directory_still_blocks_by_its_real_root(self):
        root = os.environ.get("SystemRoot") or "C:\\Windows"
        assert _blocked_path_prefix(os.path.join(root, "System32", "drivers",
                                                 "etc", "hosts")) != ""
        assert _blocked_path_prefix(root) != ""


# ── 格 3：边界要算上大小写与分隔符两种写法 ──────────────────────────────

class TestTheBoundaryCountsBothSpellings:

    def test_a_sibling_written_with_a_trailing_separator_is_still_a_sibling(self):
        # `~/.dhole-tmp/` 与 `~/.dhole-tmp\x` 同一个意思，都不该挡。
        assert _blocked_path_prefix(_p(".dhole-tmp", "")) == ""

    def test_the_blocked_directory_written_uppercase_is_still_blocked(self):
        path = _p(".dhole", "config.json").upper()
        assert _blocked_path_prefix(path) != "", path

    def test_the_fix_does_not_move_the_list(self):
        # 名单只有一个作者：这一格不许它被顺手加长或改短。
        import inspect

        src = inspect.getsource(_blocked_path_prefix)
        for needle in (".dhole", ".ssh", ".gnupg", ".aws", ".kube", ".azure",
                       ".config/gcloud", ".config/gh", ".env", ".git-credentials"):
            assert needle in src, needle
