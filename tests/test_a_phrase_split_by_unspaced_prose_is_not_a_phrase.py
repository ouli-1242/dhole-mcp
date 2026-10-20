"""建议词的「相邻」必须按原文算：中间隔着中文的两个词不是一个词组。

台账 R02-069。现场（本轮已入账的一发中文查询「那几本里有开放获取的吗」，4 行真 SERP）：

    …均以知识共享-署名-非商业性-禁止演绎4.0协议(CC-BY-NC-ND 4.0)发布。按照CC-BY-NC-ND 4.0协议的定义…

两本期刊的摘要各自都这么写，`_registrable_domain` 数得出两个独立来源，于是
`_related_queries` 交回 `["cc-by-nc-nd cc-by-nc-nd"]` 当成「值得接着搜的词」。
而原文里这两串之间隔着「)发布。按照」—— 是**中文**。挖词的分词器只认拉丁词形，
把中间那段整块丢掉，两个词因此在 token 流里成了"相邻"：`f"{title} {snippet}"` 的拼接
不是这一格的因（量的结果：把 title 与 snippet 分开挖，16 发真 SERP **一字不变**，
见 `review/.runtime/p4/f52_split.txt`），真正的因是**相邻的定义**。

判据只用形状，不用名单、也不用语言标签：两个候选词之间的那段原文里如果站着无空格文字
（沿用同模块已有的那颗 `_CJK_TERM_RE`，不另立范围作者），这一对就不算词组。
反面锁同样硬：只隔空白或半角标点的真相邻，一条不许少、顺序不许变。
"""
from dhole_mcp.search import SearchResponseModel, SearchResult, _related_queries

# 现场那一行的原文（两本期刊，各自的摘要里同一枚许可证名被写了两次，中间隔着中文）
REAL = [
    SearchResult(
        title="河南工业大学学报 开放获取声明",
        url="https://xuebaozk.haut.edu.cn/kaifanghuoqushengming",
        snippet="本刊所有内容均以知识共享-署名-非商业性-禁止演绎4.0协议(CC-BY-NC-ND 4.0)发布。"
                "按照CC-BY-NC-ND 4.0协议的定义,所有论文…"),
    SearchResult(
        title="地球科学进展 开放获取",
        url="http://www.adearth.ac.cn/CN/column/item81.shtml",
        snippet="本刊所有内容均以知识共享-署名-非商业性-禁止演绎4.0协议(CC-BY-NC-ND 4.0)发布。"
                "按照CC-BY-NC-ND 4.0协议的定义,作者…"),
]


class TestTheAdjacencyIsTheSourceS:

    def test_the_measured_pair_is_not_a_phrase(self):
        got = _related_queries("那几本里有开放获取的吗", REAL)
        assert "cc-by-nc-nd cc-by-nc-nd" not in got, got

    def test_two_different_words_split_by_prose_are_not_a_phrase_either(self):
        """形状是「中间站着无空格文字」，不是「重复串」—— 换两个词也得挡。"""
        rows = [
            SearchResult(title="异步与并发 说明", url="https://a.example.org/1",
                         snippet="先说 semaphore 机制。另外 asyncio gather 的用法…",
                         ),
            SearchResult(title="异步与并发 讲义", url="https://b.example.net/2",
                         snippet="先说 semaphore 机制。另外 asyncio gather 的用法…"),
        ]
        got = _related_queries("异步 并发 原语", rows)
        assert "semaphore asyncio" not in got, got

    def test_a_japanese_gap_is_the_same_shape(self):
        """换脚本不换形状：中间是日文时同样不许mint（判据不是「中文名单」）。"""
        rows = [
            SearchResult(title="公開議論", url="https://a.example.org/3",
                         snippet="budget の前に proposal を置いて…"),
            SearchResult(title="公開議論", url="https://b.example.net/4",
                         snippet="budget の前に proposal を置いて…"),
        ]
        got = _related_queries("公開 議論", rows)
        assert "budget proposal" not in got, got

    def test_a_real_neighbour_survives_the_gate(self):
        """只隔空白/半角标点的真相邻：一条不许少。"""
        rows = [
            SearchResult(title="open access policy notes", url="https://a.example.org/5",
                         snippet="repository rules, open access mandate, and cluster analysis"),
            SearchResult(title="open access policy notes", url="https://b.example.net/6",
                         snippet="repository rules, open access mandate, and cluster analysis"),
        ]
        got = _related_queries("论文 存储库", rows)
        assert "open access" in got, got
        assert "cluster analysis" in got, got
        assert "access mandate" in got, got

    def test_one_row_can_lose_the_phantom_and_keep_the_real(self):
        """同一行里两种形状并存时，只丢跨段文字的那一对。"""
        rows = [
            SearchResult(title="Journal policy", url="https://a.example.org/7",
                         snippet="CC-BY-NC-ND 4.0发布。按照CC-BY-NC-ND 4.0 rules, "
                                 "open access mandates apply"),
            SearchResult(title="Journal policy", url="https://b.example.net/8",
                         snippet="CC-BY-NC-ND 4.0发布。按照CC-BY-NC-ND 4.0 rules, "
                                 "open access mandates apply"),
        ]
        got = _related_queries("期刊 许可", rows)
        assert "cc-by-nc-nd cc-by-nc-nd" not in got, got
        assert "open access" in got, got

    def test_the_seam_of_title_and_snippet_is_not_adjacency(self):
        """同一条原则的另一半：标题收尾与摘要开头是本函数拼出来的，不是原文里的相邻。

        量过：把 title 与 snippet 分开挖，本轮 16 发真 SERP 一字不变
        （`review/.runtime/p4/f52_split.txt`）—— 所以这一格不是「为了让某发闭嘴」，
        是把同一条判据用完；真出现过的接缝形状是这一格自己造的。
        """
        rows = [
            SearchResult(title="async web scraping guide", url="https://a.example.org/9",
                         snippet="async web scraping guide 说明 plus cluster analysis tips"),
            SearchResult(title="async web scraping guide", url="https://b.example.net/10",
                         snippet="async web scraping guide 说明 plus cluster analysis tips"),
        ]
        got = _related_queries("抓取 并发", rows)
        assert "guide async" not in got, got
        assert got == ["analysis tips", "async web", "cluster analysis", "web scraping"], got


class TestTheFieldContractIsUnchanged:
    """这一刀改的是**挖的词对不对**，不改字段的承诺。"""

    def test_the_field_still_promises_follow_up_queries(self):
        text = SearchResponseModel.model_fields["related_queries"].description or ""
        assert "Follow-up queries worth searching next" in text, text
        assert "Empty if none derived" in text, text
