"""未发版那一版的 CHANGELOG 小节必须是今天的形状 —— 它是说明书，不是历史。

`tests/test_readme_tells_the_truth.py` 明确把 CHANGELOG 划出管辖：*"按日期钉住的
历史，'当时开了一个后来关掉的开关'是一句真话"*。那句话对 **已发版** 的段落成立，
对本版不成立：`__version__` 还是 17.0、最后一个 tag 是 v16.2，而 17.0 这一版
自己在 B4/B5 里把 `options` 袋拆平、把工具改名、把七八颗旋钮注销、把
`dhole init` 整组删掉。留着旧形状的那一段不是历史，是一份"照着打字就撞墙"的
安装说明，而且它比 README 更容易被信 —— 读者正想从它那里知道"这版改了什么"。

判据三条，都是机械的：

1. 每一颗 `DHOLE_*` 要么在 `env_registry.ALL` 里，要么它所在那一行必须自己说明
   它已经注销（下面那张 `_RETIRED_MARKERS`）。名字出现不是问题，**冒充现行**才是。
2. 每一个 `dhole <子命令>` 要么真在 `_help_epilog()` 的命令表里，要么同一行说明它
   被删了。
3. 本版段里不许出现旧工具名与 `options.` 袋 —— 它们不是"当时的名字"，是同一版里
   改了两次还没扫干净。

已发版的段落（16.x 及更早）由 `_released_head` 排除在外。
"""

from __future__ import annotations

import pathlib
import re

import pytest

from dhole_mcp import env_registry
from dhole_mcp.ops import _help_epilog

CHANGELOG = pathlib.Path(__file__).resolve().parents[1] / "CHANGELOG.md"

#: 一行里出现这些词，就算它是在"宣布注销"而不是"教人用"
_RETIRED_MARKERS = ("注销", "删掉", "删除", "不再被读", "收掉", "收成", "写死",
                    "随大众化", "曾有", "一度", "整组删", "退役", "搬走", "取消", "已拆平")

#: 旧工具名只允许出现在"说这个名字后来怎么了"的行里
_RENAMED_MARKERS = ("落地时叫", "原名", "旧名", "曾用名", "改名", "移进", "拆平")

#: 本版内改过两次以上的名字：出现在未发版段里一定是漏扫，不是历史
_OLD_NAMES = ("smart_fetch", "smart_crawl", "smart_search", "feed_fetch",
              "dhole_doc", "solve_cloudflare", "real_chrome", "discover_only")


def _text() -> str:
    return CHANGELOG.read_text(encoding="utf-8")


def _current_version() -> str:
    from dhole_mcp import __version__

    s = _text()
    head = f"## [{__version__}]"
    assert head in s, f"CHANGELOG 里没有本版（{__version__}）的小节"
    i = s.index(head)
    j = s.find("\n## [", i + len(head))
    return s[i:(j if j > 0 else len(s))]


def _lines(body: str) -> list[str]:
    return body.splitlines()


def test_the_helper_finds_the_unreleased_section():
    """机制自检：小节切错了，下面三条守卫会一起伪装成通过。"""
    body = _current_version()
    assert body.startswith("## [")
    assert "### 新增" in body, "小节切片没吃到正文"
    assert "## [16." not in body, "切片越界，已发版的历史被算进管辖内了"


@pytest.mark.parametrize("line", _lines(_current_version()),
                         ids=lambda ln: ln[:48])
def test_no_knob_is_taught_as_if_it_still_exists(line):
    for tok in set(re.findall(r"DHOLE_[A-Z_]+", line)):
        if tok in {getattr(n, "name", n) for n in env_registry.ALL}:
            continue
        assert any(m in line for m in _RETIRED_MARKERS), (
            f"本版小节里教了一颗今天不再被读的旋钮 {tok}，而这一行没说它已注销：\n"
            f"{line[:160]}\n要么换成真的门（cred_defaults / dhole config），"
            "要么把『本版注销』写明。")


def test_every_command_the_version_section_teaches_exists():
    body = _current_version()
    real = set(re.findall(r"dhole ([a-z\-]+)", _help_epilog())) | {"--http", "-v"}
    bad = []
    for line in _lines(body):
        for cmd in set(re.findall(r"dhole ([a-z][a-z\-]+)", line)):
            if cmd in real:
                continue
            if any(m in line for m in _RETIRED_MARKERS):
                continue
            bad.append(f"dhole {cmd} ← {line[:90]}")
    assert not bad, "本版小节里出现的命令今天敲会报错：\n" + "\n".join(bad)


def test_no_pre_rename_tool_name_survives_in_the_unreleased_section():
    body = _current_version()
    bad = []
    for line in _lines(body):
        for name in _OLD_NAMES:
            if name in line and not any(m in line for m in _RENAMED_MARKERS):
                bad.append(f"{name} ← {line[:90]}")
    assert not bad, (
        "这些名字在 17.0 自己里就被改掉了（B5 改名 / B4 拆袋），未发版段落里只能出现在"
        "『说这个名字后来怎么了』的行里：\n" + "\n".join(bad))


def test_the_options_bag_is_not_taught_as_current_in_this_version():
    """`options` 袋是本版删掉的，不是上一版就有的历史。"""
    body = _current_version()
    bad = [ln[:100] for ln in _lines(body)
           if re.search(r"\boptions\.[a-z_]+|`options`", ln)
           and not any(m in ln for m in _RETIRED_MARKERS + ("拆平", "袋"))]
    assert not bad, "还有行把参数写成袋里的键（今天这么写会被拒）：\n" + "\n".join(bad)


def test_the_guard_notices_a_forgotten_knob():
    """反证：把"注销"那两个字抹掉，同一行就该被判为"冒充现行能力"。

    这条守卫唯一的宽容是同行有注销标记，所以它的有效性等于那个标记的存在性 ——
    拿真语料演一遍，防止标记表退化成什么都放行。
    """
    registered = {getattr(n, "name", n) for n in env_registry.ALL}
    line = next(ln for ln in _lines(_current_version()) if "DHOLE_MULTIMODAL" in ln)
    assert "DHOLE_MULTIMODAL" not in registered
    assert any(m in line for m in _RETIRED_MARKERS), "这一行本来就该带注销标记"
    naked = line
    for m in _RETIRED_MARKERS:
        naked = naked.replace(m, "")
    assert not any(m in naked for m in _RETIRED_MARKERS)
