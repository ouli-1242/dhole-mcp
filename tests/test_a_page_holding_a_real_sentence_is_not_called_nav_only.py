"""一行真的句子就是正文的证据：`nav_only` 不许在正文在场时判出来。

现场（2026-10-15 · P02-02 邝野 · S5 十八条一把的边界探测 · R02-006 · P1 · 组 E）：

    web_read(urls=[/work/1 … /work/18]) → 18 条里 12 真 6 假
    假的那 6 条（n ≡ 1 mod 3）的 basis 一律是
      「5 lines, 80% of them are short fragments without sentence punctuation」

本机量的到的两副形状（同一把尺、同一类页面，只差标签数）：

    /work/1  行长 [4, 2, 2, 2, 25]  → 标签占比 4/5 = 0.80 → nav_only（content_ok=false）
    /work/2  行长 [4, 2, 2, 25]     → 标签占比 3/4 = 0.75 → ok         （content_ok=true）

两副里那顆 25 字的行都是同一句完整正文（「第 N 篇的独有正文，用来证明这一列真的每页不同。」）。
所以翻面的不是内容，是多出来的那一枚标签把占比推过了 0.8 —— 用户按 `content_ok` 过滤就
静默丢掉三分之一的数据。

根因（读到码）：`quality.body_substance` 的第一支只看三颗形状读数（行数 / 标签行占比 /
均行长），`label_share >= 0.8` **默认容许 20% 的行不是标签**，而"不是标签"在这个尺子里
就等于"带句末标点或超过 24 字" —— 也就是说，判决在它自己说"整页都是菜单碎片"的时候，
并没有要求**一句正文都不在**。同一函数第二支的 basis 明明写着 "with no sentence in them"
（第 724-726 行）：一把尺的两个刻度，一处要求"没有句子"，一处不要求（组 E · 两处共认）。

契约（本文件钉住）：
  * 带着一颗真的句子（句末标点 + 够长的一行）的正文不许判 `nav_only`；两副实测形状
    （4 行与 5 行）必须同判；
  * 反面锁：纯菜单（全是碎片、没有句子）照旧 `nav_only`；整页不足 200 可见字那一支照旧；
    目录那一族（/collections 的形状：占比 1.00 而均行 16 字）照旧 ok；
  * 句子的地板是一位作者定的数（`_SENTENCE_LINE_MIN_CHARS`），5 个字的"好的。"这种
    碎片不算句子 —— 不许为了翻这一发把门整体拆掉；
  * 判决位不借这一刀扩张：`nav_only` 仍是 `content_ok=false` 的那一族（本刀只是让它
    不再在正文在场时说谎）。
"""

from __future__ import annotations

from dhole_mcp.quality import (
    _SENTENCE_LINE_MIN_CHARS,
    _JS_SHELL_MIN_TEXT_CHARS,
    body_substance,
)

#: 本机 8399 靶站量到的两副真实形状（n=1 与 n=2），逐字取自 web_read 的 content。
WITH_EXTRA_TAG = "作品 1\n街景\n夜景\n人像\n第 1 篇的独有正文，用来证明这一列真的每页不同。"
WITHOUT_EXTRA_TAG = "作品 2\n街景\n夜景\n第 2 篇的独有正文，用来证明这一列真的每页不同。"

#: 靶站 /nav 实测的纯菜单形状：12 行碎片、均行 2.3 字。
PURE_NAV = "\n".join(["首页", "关于", "登录", "文档", "作品一", "集合", "注册",
                      "帮助", "联系", "投稿", "搜索", "地图"])

#: 靶站 /collections 实测的目录形状：15 行、占比 1.00、均行 16.1 字。
CATALOG = "\n".join([f"专题 {i} 的一篇长标题条目" for i in range(1, 16)])


# ─── 1 · 正文在场时不许判 nav_only ───

def test_a_measured_page_with_one_real_sentence_is_not_nav_only():
    verdict, basis = body_substance(WITH_EXTRA_TAG, visible=400)
    assert verdict != "nav_only", f"带着整句正文的页被当成菜单残渣：{verdict} / {basis}"


def test_the_two_measured_shapes_of_the_same_page_agree():
    """18 条同形状的页面不许一翻三分之一：两副实测形状必须同判。"""
    a = body_substance(WITH_EXTRA_TAG, visible=400)
    b = body_substance(WITH_EXTRA_TAG.replace("人像\n", ""), visible=400)
    c = body_substance(WITHOUT_EXTRA_TAG, visible=400)
    assert a[0] == b[0] == c[0], (a, b, c)
    assert a[0] != "nav_only", a


# ─── 2 · 反面锁：那三族照旧 ───

def test_a_page_of_only_fragments_is_still_nav_only():
    verdict, basis = body_substance(PURE_NAV, visible=666)
    assert verdict == "nav_only", f"纯菜单被这刀放过了：{verdict} / {basis}"
    assert "short fragments" in basis, basis


def test_the_whole_page_shell_branch_is_untouched():
    verdict, basis = body_substance("加载中\n<div>", visible=6)
    assert verdict == "nav_only", verdict
    assert "the whole page holds" in basis, basis


def test_a_catalog_page_is_still_ok():
    assert body_substance(CATALOG, visible=1_200)[0] == "ok"


# ─── 3 · 句子的地板是一位作者定的 ───

def test_a_tiny_fragment_with_a_full_stop_is_not_a_sentence():
    """「好的。」这种碎片不算正文 —— 地板在那颗常量上，不在这一发的判决里。"""
    body = "\n".join(["首页", "关于", "登录", "注册", "好的。"])
    assert len("好的。") < _SENTENCE_LINE_MIN_CHARS
    assert body_substance(body, visible=666)[0] == "nav_only"


def test_the_sentence_floor_is_named_once_in_the_source():
    import inspect

    from dhole_mcp import quality

    ruler = inspect.getsource(quality._body_line_shape)
    assert ruler.count("_SENTENCE_LINE_MIN_CHARS") == 1, "地板在尺子里只许写一次"
    assert inspect.getsource(quality.body_substance).count(
        "_SENTENCE_LINE_MIN_CHARS") == 0, "判决处又兜了一次 —— 第二个作者"
    assert _SENTENCE_LINE_MIN_CHARS <= 8, "地板高于一切量过的标签行就没有反面锁可言"


# ─── 4 · 判决位不扩张 ───

def test_nav_only_still_flips_content_ok_when_it_is_nav_only():
    from dhole_mcp.quality import _NOT_SUBSTANTIVE

    assert "nav_only" in _NOT_SUBSTANTIVE
    assert _JS_SHELL_MIN_TEXT_CHARS == 200
