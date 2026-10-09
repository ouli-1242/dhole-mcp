"""同一个字段名只能有一套词表（体验修复刀2-S6）。

症状（15 场景体测 + R0 响应字段闸立起来时记下的那条）：搜索行的 `source_type` 走
`search._source_type`，抓取行走 `envelope.classify_source` —— 两边各自养一张域名表。
后果不是"名字不好看"，而是**取值不可达**：搜索行永远发不出 `github|qa|ecommerce|docs-site`
（我实测见过一个 github 结果被标成 `repo`），抓取行永远发不出 `other`，而手册里那一行
只能描述其中一套，读手册的 agent 于是拿着错的枚举去匹配。

合并方向由实测决定，不是由"哪套更顺眼"决定：把 747 条真实 SERP URL 直接改调抓取侧那颗
函数，有 **191 行从有名掉成 unknown**（blog 88 / docs 40 / forum 33 …），净亏 121 个
标签。所以这一刀是把两份知识折进**一张表**，最终 measured：有名覆盖 291 → **351**
（39% → 47%），而 named → unknown 的丢失是 **0**。
"""

from __future__ import annotations

import pytest

from dhole_mcp.envelope import _stale_days_for, classify_source
from dhole_mcp.search import RawResult, _build_results


def _row(url: str, i: int = 1) -> RawResult:
    return RawResult(title=f"t{i}", url=url, snippet="s", source="bing",
                     position=i, consensus=1, sources=("bing",))


def _search_row_source(url: str) -> str:
    """走真正的铸行路径，而不是再调一次分类器 —— 要守的是"两边同源于一个函数"。"""
    built = _build_results("q", [_row(url)], [1.0], total_families=2)
    return built[0].source_type


class TestOneFunctionAnswersOneFieldName:

    @pytest.mark.parametrize("url", [
        "https://github.com/openclaw/openclaw",
        "https://stackoverflow.com/questions/1/how",
        "https://huggingface.co/models",
        "https://docs.aws.amazon.com/lambda/",
        # 这两条不是凑数：老词表把"路径里有 /docs/"当成一条独立规则，任何"两侧各有一套
        # 判定"的写法都会在这里显形 —— 只测按域名认的 URL，第二套词表可以完全藏住
        # （我第一次跑反向实验时就是这样，那条实验白做了）。
        "https://kubernetes.io/docs/concepts/services-networking/",
        "https://redis.io/docs/latest/operate/oss_and_stack/",
        "https://www.zhihu.com/question/2",
        "https://arxiv.org/abs/2401.00001",
        "https://36kr.com/p/1",
        "https://randomsite12345.example/page",
    ])
    def test_a_search_row_says_what_the_fetched_page_says(self, url):
        """同一条 URL，搜索行与抓取行必须是同一个词。这一条是这一刀的守门员：
        只要有人再给搜索行接上第二套表，它就红。"""
        assert _search_row_source(url) == classify_source(url)[0]

    def test_the_values_that_used_to_be_unreachable_now_reach_search_rows(self):
        """体测里我见过的形状：一个 github 结果在搜索行上只能被写成 `repo`。"""
        assert _search_row_source("https://github.com/openclaw/openclaw") == "github"
        assert _search_row_source("https://stackoverflow.com/q/1") == "qa"
        assert _search_row_source("https://docs.rs/serde/latest/serde/") == "docs-site"
        assert _search_row_source("https://shopee.example.shop/item") == "ecommerce"


class TestNothingThatHadANameLostIt:
    """折表进来的最低要求：老表认得的站，新词表一个都不许退回 unknown。

    这些 URL 不是编的 —— 每条都来自冻结 SERP 语料里实测"会丢名字"的那 92 个域，
    逐域留一发代表。写死在这里是因为这件事**只 regression 一次就再也看不见**：
    一个字段值从 `blog` 变成 `unknown` 不会让任何测试失败，除非有人专门钉它。
    """

    @pytest.mark.parametrize("url,expected", [
        ("https://www.zhihu.com/question/1234567", "forum"),
        ("https://zhuanlan.zhihu.com/p/9", "forum"),
        ("https://www.cnblogs.com/someone/p/1.html", "blog"),
        ("https://dev.to/someone/a-post", "blog"),
        ("https://juejin.cn/post/7123456", "blog"),
        ("https://blog.csdn.net/x/article/details/1", "blog"),
        ("https://huggingface.co/models", "docs-site"),
        ("https://doc.rust-lang.org/book/", "docs-site"),
        ("https://learn.microsoft.com/en-us/azure/", "docs-site"),
        ("https://kubernetes.io/docs/concepts/", "docs-site"),
        ("https://web.dev/articles/ttfb", "docs-site"),
        ("https://realpython.com/python-generators/", "docs-site"),
        ("https://nginx.org/en/docs/http/ngx_http_core_module.html", "docs-site"),
        ("https://segmentfault.com/q/101", "forum"),
        ("https://news.ycombinator.com/item?id=1", "forum"),
    ])
    def test_a_domain_the_old_table_knew_still_has_its_word(self, url, expected):
        assert classify_source(url)[0] == expected, url

    def test_the_corpus_paths_that_only_a_path_rule_could_name(self):
        """/docs/ 那一半折进来了：实测语料里 14 条 URL 的**唯一**名字来源就是路径，
        全是被任何表都不认识的文档站（typescriptlang.org、redis.io、一个 MDN 镜像）。
        /api/ 那一半刻意没折 —— 见下一条。"""
        assert classify_source("https://www.typescriptlang.org/docs/handbook/2/everyday-types.html")[0] == "docs-site"  # fmt: skip
        assert classify_source("https://redis.io/docs/latest/operate/")[0] == "docs-site"
        assert classify_source("https://example.com/blog/post-1")[0] == "blog"


class TestWhatWasDeliberatelyNotFolded:

    def test_an_api_path_is_not_documentation(self):
        """`/api/` 不折：机器端点不是文档，而 docs-site 这个标签还会连带换上长视野
        过期判定 —— 那是行为改动，得单独拿证据，不能趁合并顺手做掉。"""
        assert classify_source("https://api.example.com/v1/users")[0] == "unknown"
        assert classify_source("https://example.com/api/health")[0] == "unknown"

    def test_repo_pages_join_github_in_the_long_horizon(self):
        """`repo` 进长视野是这一刀的**唯一**一处视野改动，理由写在函数 docstring：
        此前只有 github 因为挂在注册表名单上而顺带拿到 730 天，gitlab 却没有 ——
        那不是一个关于"页面类型"的判断，是一个巧合。"""
        assert _stale_days_for("https://gitlab.com/group/project") == 730
        assert _stale_days_for("https://github.com/o/r") == 730
        assert _stale_days_for("https://example.com/blog/x") == 365
        assert _stale_days_for("https://36kr.com/p/1") == 30


class TestTheManualNamesEveryValueInTheVocabulary:
    """词表加长之后，手册那一行必须逐个点名 —— 否则只读手册的 agent 拿着一套旧枚举
    去匹配，"这个取值不存在"看起来像数据坏了。表面取自语料生成的取值，不取自代码。"""

    def test_every_value_produced_over_the_corpus_is_documented(self):
        from dhole_mcp import guide
        manual = (guide._ROOT / "references" / "fields.md").read_text(encoding="utf-8")
        row = next(ln for ln in manual.splitlines()
                   if ln.startswith("| `source_type` |"))
        urls = [
            "https://www.nasa.gov/mission", "https://www.mit.edu/research",
            "https://github.com/o/r", "https://gitlab.com/g/p",
            "https://docs.python.org/3/", "https://stackoverflow.com/q/1",
            "https://www.zhihu.com/question/1", "https://medium.com/@u/p",
            "https://shop.example.shop/x", "https://en.wikipedia.org/wiki/A",
            "https://arxiv.org/abs/1", "https://bbc.com/news/x",
            "https://randomsite12345.example/p",
        ]
        values = {classify_source(u)[0] for u in urls}
        missing = sorted(v for v in values if f"`{v}`" not in row)
        assert not missing, f"手册的 source_type 行没点名的取值: {missing}"
