"""R3刀3（展示批量）：读得到 ≠ 读得懂 —— 五处各自把话说准。

评审清单里的 P3 一批，全是"响应/文档在描述自己时撒了小谎"：
- sys_guide 的主题列表给没有别名的那一卷打出 `(aka None)`（半张别名表查反了方向，
  Python 的缺省形状漏进了给 agent 看的表里）；section 没猜中时标题清单先逐条列一遍、
  再挤成一行列第二遍。
- web_search 的参数类拒绝只带 error 不带 next_action —— 这些拒绝都发生在任何一个
  引擎被问到之前，改对了再调是零成本，前提是读者看得见要改哪一颗。
- `cred_defaults set={allow_private:false}` 被拒（false 不是这颗键的取值），拒绝句
  却不给那条真出路：关上 = `unset=["allow_private"]`。
- 爬行停早时的样板无条件推荐 `path_include=['/docs']`，站点没有 /docs 就是一条修不
  好任何东西的建议。
- 动作报错里的 `{{...}}` 是花括号转义漏在非 f 字面量里 —— 读者看到双花括号，而它想
  给的形状是单花括号。
"""

import pytest

from dhole_mcp import guide
from dhole_mcp import quality
from dhole_mcp.actions import build_page_action
from dhole_mcp.crawl import _scope_hint
from dhole_mcp.server import MasterFetchServer


class TestTheTopicList:

    def test_a_topic_without_an_alias_does_not_claim_one(self):
        text = guide.index()
        assert "(aka None)" not in text, "别名表查反了方向，缺省被打成了别名"
        # 表头说"别名在括号里"，那有别名的那一卷就得真的带着括号
        assert "(aka 'skill')" in text, text

    def test_a_section_miss_lists_its_headings_once(self):
        topic = next(iter(guide.TOPICS))
        _header, body = guide.read(topic=topic, section="zzz-no-such-heading")
        headings = [line[2:] for line in body.splitlines() if line.startswith("- ")]
        assert headings, body
        tail = body.split("Headings in this volume:")[-1]
        for h in headings:
            assert tail.count(h) == 1, f"{h!r} 列了 {tail.count(h)} 遍"


class TestParameterRefusalsCarryARepair:
    """一颗显式点名的参数错了，就要说怎么改 —— 一个引擎都没问，重来是免费的。"""

    @pytest.mark.asyncio
    async def test_an_unknown_engine_names_the_aliases(self):
        out = await MasterFetchServer().web_search("widgets", engines=["nope"])
        assert "Invalid engine" in out.error, out.error
        assert "Nothing was searched" in out.next_action, out.next_action
        assert "360" in out.next_action and "ddg" in out.next_action, out.next_action

    @pytest.mark.asyncio
    async def test_a_bad_location_says_what_it_takes(self):
        out = await MasterFetchServer().web_search("widgets", location="worldwide")
        assert "Invalid location" in out.error, out.error
        assert "ISO country code" in out.next_action, out.next_action

    @pytest.mark.asyncio
    async def test_a_bad_page_says_pages_count_from_zero(self):
        out = await MasterFetchServer().web_search("widgets", page=99)
        assert "Invalid page" in out.error, out.error
        assert "page 0 IS the first page" in out.next_action, out.next_action

    @pytest.mark.asyncio
    async def test_a_bad_date_says_the_accepted_shapes(self):
        out = await MasterFetchServer().web_search("widgets", after="last tuesday")
        assert out.error, "unreadable date must not be dropped silently"
        assert "ISO date" in out.next_action, out.next_action

    @pytest.mark.asyncio
    async def test_no_engine_was_asked_for_any_of_them(self, monkeypatch):
        """这些拒绝的价值全在"一个包都没发"：真去问了引擎，错误就变成一次开销。"""
        from dhole_mcp import search as search_mod

        called = []

        async def spy(*a, **k):
            called.append(a)
            return ([], [])

        monkeypatch.setattr(search_mod, "multi_search", spy)
        for kwargs in ({"engines": ["nope"]}, {"location": "worldwide"}, {"page": 99}):
            out = await MasterFetchServer().web_search("widgets", **kwargs)
            assert out.results == []
        assert called == [], f"参数拒绝之后还是去问了引擎: {called}"


class TestTheClosedDoorHasAKnob:

    @pytest.mark.asyncio
    async def test_setting_allow_private_false_shows_the_way_off(self):
        out = await MasterFetchServer().cred_defaults(set={"allow_private": False})
        assert "allow_private" in out.error, out.error
        assert 'unset=["allow_private"]' in out.error, out.error

    @pytest.mark.asyncio
    async def test_an_unparseable_value_does_not_get_the_off_hint(self):
        """`"banana"` 不是"想关门"，是拼错：那句出路只给真正的假值。"""
        out = await MasterFetchServer().cred_defaults(set={"allow_private": "banana"})
        assert "unset=" not in out.error, out.error


class TestTheScopeSuggestionFollowsTheQueue:

    def test_a_real_subtree_majority_is_named_with_its_share(self):
        queue = [f"https://s.test/blog/post-{i}" for i in range(6)] + \
                ["https://s.test/about", "https://s.test/contact"]
        hint = _scope_hint(queue)
        assert "path_include=['/blog']" in hint, hint
        assert "6 of the 8" in hint, hint

    def test_a_site_without_a_dominant_subtree_is_not_offered_a_fake_one(self):
        queue = [f"https://s.test/{name}" for name in
                 ("about", "pricing", "careers", "press")]
        assert _scope_hint(queue) == ""

    def test_a_queue_of_bare_origins_cannot_invent_a_subtree(self):
        assert _scope_hint(["https://s.test/", "https://s.test/"]) == ""

    def test_a_thin_majority_is_not_worth_recommending(self):
        """/blog 只有 2 条而队列有 20 条：推荐它省不下预算，只会误导。"""
        queue = [f"https://s.test/a{i}" for i in range(18)] + \
                ["https://s.test/blog/1", "https://s.test/blog/2"]
        assert _scope_hint(queue) == ""


class TestTheShapesUseOnePairOfBraces:
    """`{{` 在非 f 字面量里就是两个字符 —— 给 agent 看的形状示例不能长这样。"""

    @pytest.mark.parametrize("action,expected", [
        ({"drag": "just-a-selector"}, "{selector|index, to}"),
        ({"snapshot": False}, '{"snapshot": true}'),
        ({"submit": "form"}, "{selector, confirm?}"),
        ({"fill": "hello"}, "{selector, text}"),
        ({"wait_selector": 5}, "{selector, count, state, timeout_ms}"),
    ])
    def test_each_refusal_shows_the_shape_it_means(self, action, expected):
        with pytest.raises(ValueError) as exc:
            build_page_action([action])
        msg = str(exc.value)
        assert expected in msg, msg
        assert "{{" not in msg, msg

    @pytest.mark.asyncio
    async def test_the_credential_shape_hint_is_not_escaped_either(self):
        """cred_defaults 的 cookies 拒绝同属这一类：`{{name: value}}` 不是形状。"""
        out = await MasterFetchServer().cred_defaults(
            set={"cookies": "not-a-cookie-shape", "host": "example.com"})
        assert "cookies" in out.error, out.error
        assert "{name: value}" in out.error, out.error
        assert "{{" not in out.error, out.error


class TestTheRejectionSummaryBreaksAtAWord:
    """`msg[:80]` 把拒绝句的 summary 剪成 "…can compile (S" —— 半截 token 看着像坏了的字符串。

    整句仍在 `error` 里（summary 是标签，不是正文），所以这里只要求帽落在词界上。
    """

    MSG = ("css_selector 'a >> b {;}' is not a selector the extraction engine can compile "
           "(SelectorSyntaxError: Expected selector, got <DELIM '>' at 3>) - it would have "
           "been ignored and the whole page returned")

    def test_the_summary_ends_where_a_word_ends(self):
        out = quality._invalid_request_result("https://example.com", self.MSG)
        prefix = "invalid request · "
        assert out.summary.startswith(prefix)
        tail = out.summary[len(prefix):]
        assert self.MSG[len(tail)] == " ", f"summary 停在词中间: {tail!r}"

    def test_the_error_still_carries_the_whole_sentence(self):
        out = quality._invalid_request_result("https://example.com", self.MSG)
        assert out.error == f"invalid_request: {self.MSG}"

    def test_a_short_rejection_is_not_padded(self):
        """短句子不需要剪，也就不该多出词界的痕迹。"""
        out = quality._invalid_request_result("https://example.com", "url is required")
        assert out.summary == "invalid request · url is required"


class TestTheRejectionSummaryStopsAtAClause:
    """词界只保证不剪出半个 token，不保证那句话说得完（2026-10-08 真实使用模拟）。

    同一轮真宿主调用里抓到三处：`…stored credentials for`（悬空介词）、
    `…POST / PUT / PATCH /`（悬空斜杠）、`…Valid values:`（冒号后面正是那串合法值 ——
    被剪掉的正是解药）。整句仍在 `error` 里，所以标签可以短；但一个以 "for" 或 ":"
    收尾的标签不是"短"，是"坏"：它承诺了一个自己没有的东西。
    """

    # 四条都是这一轮实测打出来的原话，不是编的样本。
    POST = ("method=POST is a write: it changes server state, and stored credentials "
            "for this host ride along automatically")
    GET = ("method=GET is a read, not a request - web_request takes POST / PUT / "
           "PATCH / DELETE / HEAD")
    ENUM = ("extraction_type='markdownx' is not a value this tool accepts. Valid "
            "values: markdown, html, text, article, structured, check. No request "
            "was made.")
    GUARD = ("URL hostname localtest.me resolves to internal/private IP (127.0.0.1 in "
             "127.0.0.0/8). This guard is fail-closed: on a fake-ip host only the "
             "tunnel ranges are tolerated - it is refused whatever this machine measured.")

    @pytest.mark.parametrize("msg", [POST, GET, ENUM, GUARD])
    def test_the_label_never_ends_on_a_dangling_word_or_separator(self, msg):
        label = quality._summary_label(msg)
        assert label, "剪完什么都不剩就是另一种坏"
        assert label.lower().split(" ")[-1] not in {
            "for", "and", "the", "a", "to", "of", "with", "in", "not", "is", "takes"}, (
            f"标签停在一个需要宾语的词上：{label!r}")
        assert not label.rstrip().endswith((",", ":", ";", "-", "/", "(")), (
            f"标签停在一个分隔符上，像在列一份没列完的东西：{label!r}")
        assert msg.startswith(label), "标签必须是原话的前缀，不许改写"

    def test_the_enumeration_is_not_promised_and_then_cut(self):
        """`Valid values:` 收尾 = 承诺了一份不存在的清单；句子边界在那之前。"""
        label = quality._summary_label(self.ENUM)
        assert label == "extraction_type='markdownx' is not a value this tool accepts"

    def test_a_clause_inside_the_cap_beats_a_word_at_the_edge(self):
        """有子句边界就用它，哪怕词边界能多带几个字符。"""
        label = quality._summary_label(self.POST)
        assert label == "method=POST is a write: it changes server state"
        assert "stored credentials" not in label

    def test_no_boundary_in_the_cap_falls_back_to_the_word(self):
        """一条边界都没有时，退回原来的词界规则 —— 这一条不许被新规则吃掉。"""
        msg = "alpha " * 30 + "omega"
        label = quality._summary_label(msg)
        assert msg[len(label)] == " "
        assert label == ("alpha " * 13).strip()

    def test_the_whole_sentence_still_travels_in_error(self):
        out = quality._invalid_request_result("https://example.com", self.ENUM)
        assert out.error == f"invalid_request: {self.ENUM}"
        assert "structured" in out.error, "剪的是标签，不是正文"
