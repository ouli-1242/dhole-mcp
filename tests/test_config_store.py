"""`~/.dhole/config.json` 这一层配置的契约。

为什么有文件层：旋钮只有 env 一个门的时候，改一个数字 = 编辑宿主的 MCP JSON +
重启宿主。那是基础设施操作，不是配置。文件按 mtime 重读，所以
`dhole config set DHOLE_DEFAULT_CONTENT_CHARS 8000` 下一次调用就生效。

env 仍然优先，且这一条不可协商：现有安装全部通过 env 块配置，一个文件不能悄悄
覆盖别人已经做出来的决定。
"""

import json
import pathlib
import re

import pytest

from dhole_mcp import env_registry as er

SRC = pathlib.Path(er.__file__).resolve().parent

#: 直接读环境变量的三种写法。`os.environ["X"]` 也算 —— 它会抛 KeyError，但那是
#: 另一种"文件里的值没人看"，同样要抓。
_DIRECT_READ = re.compile(
    r"""(?:os\.environ\.get|os\.getenv)\(\s*["'](DHOLE_[A-Z0-9_]+)["']"""
    r"""|os\.environ\[\s*["'](DHOLE_[A-Z0-9_]+)["']\s*\]""")


@pytest.fixture(autouse=True)
def _fresh_cache():
    """每个用例前后丢缓存：mtime 粒度粗，残留会让上一条的值伪装成"生效了"。"""
    er.invalidate_file_cache()
    yield
    er.invalidate_file_cache()


def _write(mapping) -> None:
    path = er.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping), encoding="utf-8")
    er.invalidate_file_cache()


class TestPrecedence:

    def test_a_file_value_reaches_the_typed_reader(self):
        _write({"DHOLE_DEFAULT_CONTENT_CHARS": "8000"})
        assert er.read_int("DHOLE_DEFAULT_CONTENT_CHARS", 40000) == 8000
        assert er.source_of("DHOLE_DEFAULT_CONTENT_CHARS") == "file"

    def test_env_beats_the_file(self, monkeypatch):
        """现有安装全在 env 块里；文件不能覆盖一个已经做出来的决定。"""
        _write({"DHOLE_SEARCH_DEADLINE": "30"})
        monkeypatch.setenv("DHOLE_SEARCH_DEADLINE", "12")
        assert er.read_float("DHOLE_SEARCH_DEADLINE", 16.0) == 12.0
        assert er.source_of("DHOLE_SEARCH_DEADLINE") == "env"

    def test_nothing_set_means_the_default(self):
        assert er.read_int("DHOLE_COOKIE_TTL_S", 86400) == 86400
        assert er.source_of("DHOLE_COOKIE_TTL_S") == "unset"

    def test_a_number_or_bool_in_the_file_is_read_as_text(self):
        """人手写 JSON 会写 `true` 和 `30`，不是 `"true"`/`"30"`。存储层归一化成
        字符串，读侧的解析器因此不需要第二种语义。"""
        _write({"DHOLE_USAGE_LOG": True, "DHOLE_SEARCH_DEADLINE": 20})
        assert er.read_flag("DHOLE_USAGE_LOG") is True
        assert er.read_float("DHOLE_SEARCH_DEADLINE", 16.0) == 20.0


class TestWritingRefusesWhatItCannotHonor:

    def test_an_unknown_name_is_refused_with_a_suggestion(self):
        with pytest.raises(ValueError) as exc:
            er.set_entry("DHOLE_DEFUALT_CONTENT_CHARS", "8000")
        assert "not a dhole configuration variable" in str(exc.value)
        assert "DHOLE_DEFAULT_CONTENT_CHARS" in str(exc.value), "打错的名字要给出邻近的正确写法"

    def test_a_known_name_lands_and_unlands(self):
        er.set_entry("DHOLE_USAGE_LOG", "1")
        assert er.file_entries().get("DHOLE_USAGE_LOG") == "1"
        assert er.unset_entry("DHOLE_USAGE_LOG") is True
        assert er.unset_entry("DHOLE_USAGE_LOG") is False

    def test_an_orphan_in_the_file_is_visible(self):
        """文件里写了个没人读的名字 = 看着像设置、其实什么都不做。只能靠审计抓到。"""
        _write({"DHOLE_TASK": "1"})
        assert [k for k in er.file_entries() if er.by_name(k) is None] == ["DHOLE_TASK"]

    def test_a_secret_is_never_printed(self):
        er.set_entry("DHOLE_TAVILY_API_KEY", "sk-supersecret-value")
        entry = er.by_name("DHOLE_TAVILY_API_KEY")
        shown = entry.display_value(er.value_of("DHOLE_TAVILY_API_KEY"))
        assert shown == f"set (len={len('sk-supersecret-value')})"
        assert "supersecret" not in shown


class TestABrokenFileDegradesLoudly:

    def test_unparseable_json_uses_defaults_and_records_a_warning(self):
        er.clear_warnings()
        path = er.config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not json", encoding="utf-8")   # 真的坏 JSON，不是被 dumps 救回来的字符串
        er.invalidate_file_cache()
        assert er.read_int("DHOLE_SEARCH_DEADLINE", 16) == 16
        msgs = [w.message for w in er.warnings_snapshot()]
        assert any("config.json" in m and "IGNORED" in m for m in msgs), msgs

    def test_a_json_array_is_ignored_rather_than_crashing(self):
        _write(["DHOLE_USAGE_LOG"])
        assert er.file_entries() == {}


#: 可以持久化的旋钮：注册过、且不在 env-only 白名单里。
PERSISTABLE = {e.name for e in er.ALL} - er.ENV_ONLY


def _direct_reads(text: str) -> list[str]:
    """源码里对可持久化旋钮的直读，返回 `行号:名字`。

    注释行跳过：文档里写 `os.environ.get("DHOLE_USAGE_LOG")` 解释 env  precedence
    是常态，把解释当成违规只会让人开始给守卫加豁免。
    """
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        for match in _DIRECT_READ.finditer(line):
            name = match.group(1) or match.group(2)
            if name in PERSISTABLE:
                out.append(f"{lineno}:{name}")
    return out


class TestTheBootstrapKnobsStayEnvOnly:
    """五个旋钮故意只认环境变量，理由是结构性的，不是口味问题（见 ENV_ONLY）。"""

    def test_the_env_only_list_names_real_knobs(self):
        unknown = sorted(n for n in er.ENV_ONLY if er.by_name(n) is None)
        assert not unknown, f"ENV_ONLY 里这些名字不是注册过的旋钮: {unknown}"
        assert "DHOLE_HOME" in er.ENV_ONLY, "DHOLE_HOME 定位这个文件，从文件读它是循环"

    def test_no_module_reads_a_persistable_knob_straight_from_the_environment(self):
        """这条守卫是文件层的全部价值所在。

        任何一个可持久化的旋钮若还有人直接读 os.environ，它就越"对一半调用者
        生效"：env 块里设了有用，`dhole config set` 设了没用 —— 而这两条路的文案
        长得一模一样。这正是本批次要消灭的"我设了但没生效"。

        扫源码而不是维护一份"哪些走了 registry"的清单：新写一处直读立刻被抓住，
        不需要谁记得登记。
        """
        offenders = []
        for file in sorted(SRC.glob("*.py")):
            if file.name == "env_registry.py":
                continue
            offenders += [f"{file.name}:{hit}" for hit in _direct_reads(
                file.read_text(encoding="utf-8"))]
        assert not offenders, (
            "这些旋钮被当环境变量直读，因此 ~/.dhole/config.json 里的值不会被看到：\n  "
            + "\n  ".join(offenders)
            + "\n改读 env_registry.value_of(name) —— env 优先、文件其次、都没有则默认。")

    def test_the_scanner_finds_a_direct_read_when_one_exists(self):
        """机制自检：删掉这个断言，上一条守卫就能靠"匹配不到任何东西"永远通过。"""
        snippet = (
            'import os\n'
            'raw = os.environ.get("DHOLE_USAGE_LOG")\n'
            '# os.environ.get("DHOLE_USAGE_LOG") 在注释里，不算违规\n'
            'z = os.environ["DHOLE_BROWSER_PROFILE"]\n'
            'ok = os.environ.get("DHOLE_HOME")            # env-only，本就该直读\n'
            'also_ok = env_registry.value_of("DHOLE_USAGE_LOG")\n'
        )
        assert _direct_reads(snippet) == ["2:DHOLE_USAGE_LOG", "4:DHOLE_BROWSER_PROFILE"]
        assert "DHOLE_HOME" not in PERSISTABLE
