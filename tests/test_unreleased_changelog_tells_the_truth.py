"""未发版那一版的 CHANGELOG 小节必须是今天的形状 —— 它是说明书，不是历史。

`tests/test_readme_tells_the_truth.py` 明确把 CHANGELOG 划出管辖：*"按日期钉住的
历史，'当时开了一个后来关掉的开关'是一句真话"*。那句话对 **已发版** 的段落成立，
对本版不成立：`__version__` 还是 17.0、最后一个 tag 是 v16.2，而 17.0 这一版
自己在 B4/B5 里把 `options` 袋拆平、把工具改名、把七八颗旋钮注销、把
`dhole init` 整组删掉。留着旧形状的那一段不是历史，是一份"照着打字就撞墙"的
安装说明，而且它比 README 更容易被信 —— 读者正想从它那里知道"这版改了什么"。

判据四条，都是机械的：

1. 每一颗 `DHOLE_*` 要么在 `env_registry.ALL` 里，要么它所在那一行必须自己说明
   它已经注销（下面那张 `_RETIRED_MARKERS`）。名字出现不是问题，**冒充现行**才是。
2. 每一个 `dhole <子命令>` 要么真在 `_help_epilog()` 的命令表里，要么同一行说明它
   被删了。
3. 本版段里不许出现旧工具名与 `options.` 袋 —— 它们不是"当时的名字"，是同一版里
   改了两次还没扫干净。
4. 本版**未发布即移除**的四颗工具（`_REMOVED_NAMES`）不许被当成现行能力介绍：
   出现在本版段里必须是"说它们被移除"的行。理由同 3 —— 一颗今天不存在的工具，
   写进安装说明里就是"照着打字就撞墙"。

已发版的段落（16.x 及更早）由 `_released_head` 排除在外。
"""

from __future__ import annotations

import pathlib
import re

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

#: 17.0 未发布即移除的四颗工具：在未发版段里只允许出现在"说它们被移除"的行里
_REMOVED_NAMES = ("web_recipe", "web_watch", "cred_vault", "cred_login")

#: 一行里出现这些词，就算它是在"宣布移除"而不是"教人用"
_REMOVAL_MARKERS = ("移除", "未发布即", "一并删", "下线") + _RETIRED_MARKERS

#: 一行里的工具数不是今天的数时，同一行必须说明它已被取代 —— 手法同 `_RETIRED_MARKERS`：
#: 数字出现不是问题，**冒充现行**才是。
_SUPERSEDED_MARKERS = ("阶段7", "后为", "当时", "时代", "截面", "已被取代", "不再冒充")

#: 把工具数说成"现在时"的说法。报告 D12 的来路就是这几句 —— 数字本身没错，错在
#: 一个已经过去的形状被写成了今天的形状。只在**这些说法**所在的行上较真，是因为
#: 「71 颗参数」「30 颗设计过的拒绝」里的数不是工具数，而「v16.2 是 9 颗工具」
#: 那样的行本来就带着年份，不冒充谁。
_CURRENT_CLAIM_MARKERS = ("定稿", "当前", "同步到", "默认注册面")


def _today_tool_count() -> int:
    """今天的工具数，从 `_TOOL_DEFS` 现读 —— 手抄的那个数每拆一次工具就要改一次。"""
    from dhole_mcp.server import MasterFetchServer

    return len(MasterFetchServer._TOOL_DEFS)


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


def test_no_knob_is_taught_as_if_it_still_exists():
    """扫整个本版小节，把所有"冒充现行"的行一次性报出来。

    原先按行参数化（CHANGELOG 一行一例，约 90 例）；聚合成一条后**覆盖不变**，
    失败信息反而更全 —— 断言消息本来就该列出全部违规行，而不是一次只暴露一行。
    """
    registered = {getattr(n, "name", n) for n in env_registry.ALL}
    bad = []
    for line in _lines(_current_version()):
        for tok in sorted(set(re.findall(r"DHOLE_[A-Z_]+", line))):
            if tok in registered:
                continue
            if any(m in line for m in _RETIRED_MARKERS):
                continue
            bad.append(f"{tok} ← {line[:140]}")
    assert not bad, (
        "本版小节里教了今天不再被读的旋钮，而那一行没说它已注销：\n"
        + "\n".join(bad)
        + "\n要么换成真的门（cred_defaults / dhole config），要么把『本版注销』写明。")


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


def test_no_removed_tool_is_taught_as_if_it_still_exists():
    """17.0 未发布即移除的四颗：留在未发版段里必须是"说它们没了"。

    与上面那条（旧名）分开，因为语义不同：旧名是"改过名字"，这几颗是"整颗没了"。
    一颗今天不存在的工具被写进本版安装说明里，读者照着打字只会撞墙。
    """
    body = _current_version()
    bad = []
    for line in _lines(body):
        for name in _REMOVED_NAMES:
            if name in line and not any(m in line for m in _REMOVAL_MARKERS):
                bad.append(f"{name} ← {line[:120]}")
    assert not bad, (
        "这四颗工具在本版未发布即移除（见本版「移除」小节），未发版段落里只能出现在"
        "『说它们被移除』的行里：\n" + "\n".join(bad))


def test_the_options_bag_is_not_taught_as_current_in_this_version():
    """`options` 袋是本版删掉的，不是上一版就有的历史。"""
    body = _current_version()
    bad = [ln[:100] for ln in _lines(body)
           if re.search(r"\boptions\.[a-z_]+|`options`", ln)
           and not any(m in ln for m in _RETIRED_MARKERS + ("拆平", "袋"))]
    assert not bad, "还有行把参数写成袋里的键（今天这么写会被拒）：\n" + "\n".join(bad)


def test_every_tool_count_in_this_version_is_todays_or_says_it_was_superseded():
    """本版小节里的「N 颗」要么是今天的数，要么同一行说明它已被取代（报告 D12）。

    报告 D12 的来路：17.0 段落同时出现 11 / 13 / 17 三个工具数。13 不是抄错 ——
    它是四颗状态工具移除后的**真实中间态**，阶段7 又移除 `web_screenshot` 与
    `web_resolve` 才到 11（见「安全」）。错的是更早那几条 bullet 把 13 与 17 写成
    了**现在时**（"本版定稿 13 颗"、"当前 17 颗"），读者落到那一条会以为今天还是
    那个形状。判据与上面那张旋钮表同一个手法：数字出现不是问题，**冒充现行**才是。
    """
    today = _today_tool_count()
    bad = []
    for line in _lines(_current_version()):
        if not any(m in line for m in _CURRENT_CLAIM_MARKERS):
            continue
        for n in re.findall(r"(\d+)\s*颗", line):
            if int(n) == today:
                continue
            if any(m in line for m in _SUPERSEDED_MARKERS):
                continue
            bad.append(f"{n} 颗 ← {line[:140]}")
    assert not bad, (
        f"本版小节里把工具数写成了现在时，而那个数不是今天的（{today} 颗），"
        "同一行也没说它已被取代：\n"
        + "\n".join(bad)
        + "\n要么改成今天的数，要么把『阶段7 后为 N 颗』写明。")


def test_the_guard_notices_a_superseded_count():
    """反证：把那一行的取代标记抹掉，同一个数就该被判为"冒充现行"（同下一条的手法）。"""
    today = _today_tool_count()
    line = next(ln for ln in _lines(_current_version())
                if "阶段7" in ln and any(m in ln for m in _CURRENT_CLAIM_MARKERS)
                and re.search(r"(\d+)\s*颗", ln))
    stale = [n for n in re.findall(r"(\d+)\s*颗", line) if int(n) != today]
    assert stale, "这一行本来就带着一个不是今天的数"
    naked = line
    for m in _SUPERSEDED_MARKERS:
        naked = naked.replace(m, "")
    assert any(m in naked for m in _CURRENT_CLAIM_MARKERS), "现在时说法仍应留着"
    assert not any(m in naked for m in _SUPERSEDED_MARKERS)
    assert [n for n in re.findall(r"(\d+)\s*颗", naked) if int(n) != today] == stale, \
        "抹掉取代标记后仍应留下那些冒充现行的数，否则这条反证没有演到东西"


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
