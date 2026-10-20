"""浏览器腿那两颗 `no-cache` 头要有署名的作者，而作者不是 dhole（R02-081 · R02-F74）。

立案时的情形是"grep 不到作者"：`web_read(force_fetcher='stealthy')` 打到只绑回环的
header echo 时，那一发比 HTTP 腿多三颗 —— `connection: keep-alive` · `pragma: no-cache` ·
`cache-control: no-cache`。按大小写不敏感去 grep `src/` 只会命中 `# pragma: no cover` 与
SQLite 的 `PRAGMA`，于是"读源码找作者"这条路本身就是错的（这一条也写进手册）。

量出来的归因（六支仪器、同一只 echo、全部本机，`review/.runtime/p4/r81-shape*.json`）：

  · 裸 chromium 导航不带（13 颗），装了 `page.route` 才带（15 颗）；route 的 pattern
    匹配不到主文档（handler 命中 0）也带 ⇒ 与"这一笔有没有被暂停应答"无关，开关一拨全场生效。
  · 原生 CDP `Fetch.enable` 无论窄（只 Document）还是宽（`urlPattern:"*"`）都不带
    ⇒ 不是拦截域本身，dhole 的 `_DocumentFence` 那一层不加头。
  · 因子组合：`Network.enable` + `Network.setCacheDisabled{true}` 就带，加不加 `Fetch.enable`
    都带；缺一味就不带 ⇒ 作者是浏览器的"DevTools 关缓存"模式，而那一位由自动化层在
    有请求拦截时拨下（patchright 驱动 coreBundle.js:36856 `cacheDisabled: userInterception`）。
  · 在 handler 里把请求现有的头原样覆盖回去**摘不掉**（那两位当时还不在名单里），但
    **自己点名**就赢：`cache-control: max-age=0` 原样到达且那一对不再出现，带不带 route 都一样。

这一族判因此刻只钉三件事，都是"手册说的那句话不许悄悄失效"：
① 手册必须点名作者（且说清 dhole 不写它们）；② 手册承诺"点名就赢"，那一扇门
（`_header_door`）就必须真的放这两个名字过去；③ dhole 自己合成的字典里不许长出这两位
——否则"不是 dhole 写的"那句话变成假话。第四条守的是同一节里那句"几条限制"的数：
它数错过一次（写着 three 而底下四条），这类数不会报错，只会让人少读一条。
"""

from __future__ import annotations

import re

from dhole_mcp import guide
from dhole_mcp.fetcher import HTTPSession
from dhole_mcp.server import _header_door

NEEDLES = (
    "dhole never writes",          # 作者署名
    "no-cache mode",               # 机制：浏览器的关缓存模式，不是 dhole 的合成头
    "naming the header yourself",  # 出路：点名就赢（与 _header_door 那条承诺同一位）
)

_WORD_NUMBERS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def _troubleshooting() -> str:
    return (guide._ROOT / "references" / "troubleshooting.md").read_text(encoding="utf-8")


def test_the_manual_names_the_author_of_the_two_no_cache_headers():
    """① 读者在浏览器腿的 echo 里看到这两颗时，手册要给得出作者。"""
    text = _troubleshooting()
    missing = [n for n in NEEDLES if n not in text]
    assert not missing, (
        f"手册里关于 `pragma`/`cache-control` 的说法缺了这几段：{missing}。"
        "这一对在浏览器腿是稳定出现的（量过），读者照旧文会去 grep dhole 的字典而一无所获。")


def test_sys_guide_actually_ships_that_paragraph():
    """写在磁盘上不算数，得是 `sys_guide` 真送得出去的那一卷。"""
    blob = "\n".join(guide.read(topic)[1] for topic in guide.TOPICS)
    for needle in NEEDLES:
        assert needle in blob, f"{needle!r} 在磁盘上但 sys_guide 一个字也不送"


def test_the_manual_still_falsifies_when_the_paragraph_is_deleted():
    """反证：删掉那一段，上面那条守卫必须看见（覆盖率守卫伪装通过是这仓库犯过的错）。"""
    text = _troubleshooting()
    stripped = text.replace(NEEDLES[0], "", 1)
    assert NEEDLES[0] in text and NEEDLES[0] not in stripped


def test_both_names_pass_the_header_door():
    """② 手册说"点名就赢"，那这一扇拒绝门就不许先把这两个名字拒掉。"""
    for name in ("cache-control", "pragma"):
        door = _header_door(name)
        assert door is None, f"{name} 被 headers= 那扇门拒了：{door!r} —— 手册的承诺过期了"


def test_dholes_own_header_sets_never_grow_those_two_names():
    """③ "不是 dhole 写的"必须由 dhole 自己的字典作证：合成层不许悄悄长出这两位。"""
    for stealthy in (True, False):
        built = {k.lower() for k in HTTPSession(stealthy_headers=stealthy)._build_headers()}
        assert "pragma" not in built and "cache-control" not in built, (
            f"stealthy_headers={stealthy} 的合成字典里出现了 pragma/cache-control："
            "手册那句『dhole 不写它们』从此是假话")


def test_the_section_counts_its_own_limits():
    """④ "几条限制"那个数必须数得对：它从前写 three 而底下挂四条。"""
    text = _troubleshooting()
    m = re.search(r"\b(Two|Three|Four|Five|Six) limits live under dhole", text, re.I)
    assert m, "那句话改了形状，这条守卫跟着改（不许直接删守卫）"
    claimed = _WORD_NUMBERS[m.group(1).lower()]
    tail = text[m.end():]
    bullets = len(re.findall(r"^- \*\*", tail[:tail.find("\n### ")], re.M))
    assert bullets == claimed, (
        f"那句说 {claimed} 条，底下实际挂着 {bullets} 条 —— 数错的清单会让人少读一条")
