"""test_search_metasearch.py —— 由 2 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
import pytest
from dhole_mcp.search_metasearch import MetaSearchException
from dhole_mcp.server import MasterFetchServer
from dhole_mcp import quality
from dhole_mcp.models import ResponseModel
from dhole_mcp.search_metasearch import Sogou


# ============================================================================
# 来源：test_error_guidance_names_the_real_door.py
# ============================================================================
# next_action 要说真话：错误指引家族的四处（评审R1刀3 + D4）。

def _stub_search(monkeypatch, exc: Exception):
    """让 web_search 走真管道，只有引擎层抛指定的错（与既有回归测试同一手法）。"""
    from dhole_mcp import search as S

    async def fake_multi_search(query, max_results, **kwargs):
        raise exc

    async def fake_get_cached(*a, **k):
        return None

    async def fake_set_cached(*a, **k):
        return None

    async def fake_ensure_reranker(*a, **k):
        return None

    monkeypatch.setattr(S, "multi_search", fake_multi_search)
    monkeypatch.setattr(S, "get_cached", fake_get_cached)
    monkeypatch.setattr(S, "set_cached", fake_set_cached)
    monkeypatch.setattr(S, "ensure_reranker", fake_ensure_reranker)
    return S


class TestAMissingKeyIsNotARephraseProblem:

    @pytest.mark.asyncio
    async def test_the_env_var_is_named_and_rephrasing_is_off_the_table(
            self, monkeypatch):
        # 文案逐字来自 search_metasearch 的单 keyed 引擎抛错路径。
        S = _stub_search(monkeypatch, MetaSearchException(
            "Engine 'tavily' requires DHOLE_TAVILY_API_KEY, which is not set."))
        out = await S.web_search(None, "test query", 6)
        assert "DHOLE_TAVILY_API_KEY" in (out.next_action or "")
        assert "Rephrase" not in (out.next_action or ""), \
            "换说法修不了一把不存在的钥匙"
        assert "DHOLE_TAVILY_API_KEY" in (out.error or "")

    @pytest.mark.asyncio
    async def test_the_json_engine_missing_key_spelling_is_covered_too(
            self, monkeypatch):
        # search_json 缺 key 的另一份拼写（无 "Engine '…'" 前缀）同样命中。
        from dhole_mcp.search import _search_next_action
        row = _search_next_action(
            [], [], "tavily requires DHOLE_TAVILY_API_KEY, which is not set",
            ["tavily"])
        assert "DHOLE_TAVILY_API_KEY" in row
        assert "Rephrase" not in row


class TestTheRerankerClaimIsFactual:

    def test_nothing_to_rerank_makes_no_absence_claim(self, monkeypatch):
        """空结果 ≠ 重排器缺席：重排器暖着的时候尤其不许说反话。"""
        from dhole_mcp import search as S

        class _Warm:
            def score(self, q, docs):
                return [0.5] * len(docs)

        monkeypatch.setattr("dhole_mcp.reranker.get_reranker", lambda: _Warm())
        monkeypatch.setattr(S, "unavailable_reason", lambda: "")
        ranked, scores, mode, note = S._rank("q", [], "neural")
        assert ranked == [] and mode in ("neural", "merge")
        assert "has not run" not in note, "重排器在进程里明明跑过/暖着"
        assert "NOT active" not in note

    def test_a_real_absence_with_candidates_still_explains_itself(self, monkeypatch):
        """有东西可排、重排器真的不在 → 缺席说明照旧（零回归）。"""
        from dhole_mcp import search as S
        from dhole_mcp.search_engines import RawResult

        monkeypatch.setattr("dhole_mcp.reranker.get_reranker", lambda: None)
        monkeypatch.setattr(S, "unavailable_reason", lambda: "")
        rows = [RawResult(title="t", url="https://e.example/", snippet="s",
                          source="bing", position=1, consensus=1,
                          sources=("bing",))]
        ranked, scores, mode, note = S._rank("q", rows, "neural")
        assert mode == "merge" and ranked
        assert "neural rerank unavailable" in note


class TestTheTemplateNamesTheCaller:

    @pytest.mark.asyncio
    async def test_web_request_refusals_say_web_request(self):
        out = await MasterFetchServer().web_request(url="", method="POST")
        assert "url is required" in (out.error or "")
        assert "web_request takes an absolute http(s) URL" in (out.next_action or "")
        assert "web_read takes" not in (out.next_action or "")

    @pytest.mark.asyncio
    async def test_web_act_refusals_say_web_act(self):
        out = await MasterFetchServer().web_act(url="", actions=[{"wait": 1}])
        assert "url is required" in (out.error or "")
        assert "web_act takes an absolute http(s) URL" in (out.next_action or "")
        assert "web_read takes" not in (out.next_action or "")

    @pytest.mark.asyncio
    async def test_web_read_keeps_its_own_name(self):
        """样板点名调用方这条锁的判据是**用默认模板的那类拒绝**。

        2026-10-08 刀3 之后，内网地址那一类不再走默认模板（它有自己那句出路），所以
        这颗改用一颗真的没写对的 URL 当载体 —— 契约不变：共享校验路不许把
        web_request 的调用说成 web_read。
        """
        out = await MasterFetchServer().web_read(url="not-a-url-at-all")
        assert "web_read takes an absolute http(s) URL" in (out.next_action or "")
        assert "web_request takes" not in (out.next_action or "")

    @pytest.mark.asyncio
    async def test_a_private_address_no_longer_borrows_the_url_template(self):
        """刀3 的另一半：地址被拒时，那句 URL 拼法教程本身就是错的话。"""
        out = await MasterFetchServer().web_read(url="http://10.0.0.5/")
        assert "internal/private IP" in (out.error or "")
        assert "takes an absolute http(s) URL" not in (out.next_action or "")
        assert "allow_private" in (out.next_action or ""), "这一类有出口，要指出来"


class TestAPermanentBoundaryDoesNotOfferAnArgumentFix:

    @pytest.mark.asyncio
    async def test_cloud_metadata_refusal(self):
        out = await MasterFetchServer().web_read(
            url="http://169.254.169.254/latest/meta-data/")
        assert "cloud metadata endpoint" in (out.error or "")
        assert "No argument fixes this" in (out.next_action or "")
        assert "Correct the argument" not in (out.next_action or "")

    @pytest.mark.asyncio
    async def test_the_same_boundary_on_the_web_request_probe_path(self):
        # HEAD 不过写门，URL 校验的拒绝原文可达；工具名已无关紧要——
        # 边界句替换的是"怎么改参数"那句话。
        out = await MasterFetchServer().web_request(
            url="http://169.254.169.254/", method="HEAD")
        assert "cloud metadata endpoint" in (out.error or "")
        assert "No argument fixes this" in (out.next_action or "")

    @pytest.mark.asyncio
    async def test_dns_rebinding_service_refusal(self):
        out = await MasterFetchServer().web_read(
            url="http://127.0.0.1.nip.io/")
        assert "DNS rebinding service" in (out.error or "")
        assert "No argument fixes this" in (out.next_action or "")


# ─── 4 · 登录墙的建议要看墙在哪台机器上（D4）────────────────────
# 实测（2026-10-09 真人体验测试 C9）：`web_read("http://127.0.0.1:8765/admin")` 撞出
# auth_wall，`next_action` 说「Switch source, or try the Internet Archive for a public
# copy」。调用方点名要的就是这台机器上这个端口的东西 —— 换源换不掉它，而档案站**从不
# 持有登录态后的那一份**。这句建议在这台机器上不是"偏弱"，是"永远不可能兑现"。
#
# 双向都要钉：只在私网这一侧收口，公网那条真退路（公开副本）不许被一起关掉。


LOCAL = "http://127.0.0.1:8765/admin"
PUBLIC_WALL = "https://news.example.com/premium/report"


def _wall(url: str, **updates) -> ResponseModel:
    base = dict(url=url, status=200, content=["Sign in to continue"],
                fetcher_used="http", extracted_type="markdown",
                total_size_bytes=4_200, page_type="auth_wall",
                error="auth_wall_detected: page is a login/sign-in wall, not content")
    base.update(updates)
    return ResponseModel(**base)


def _next_action(result: ResponseModel) -> str:
    return quality._agent_hints(result)[1]


class TestAWallOnYourOwnHostNamesYourOwnDoors:

    def test_the_archive_is_taken_off_the_table_for_a_private_target(self):
        """点名"档案站"是允许的 —— 只要那句话是"这次它答不了"，而不是"去试试"。

        与 `test_advice_never_recommends_the_tier_that_just_failed.py` 里那颗旋钮同
        一条判据：告诉调用方哪条路已经堵住，正是他能自己核对的那句。要禁的是祈使句。
        """
        hint = _next_action(_wall(LOCAL))
        assert "try the Internet Archive" not in hint, hint
        assert "Switch source" not in hint, hint
        assert "never holds a logged-in copy" in hint, hint
        assert "the host YOU named" in hint, hint

    def test_the_two_doors_that_can_actually_open_it_are_named(self):
        hint = _next_action(_wall(LOCAL))
        assert "cred_defaults" in hint, hint
        assert "web_act" in hint, "第二扇真门：页面上那个表单本来就能走"

    def test_the_public_copy_is_still_on_the_table_for_a_public_host(self):
        """反向半边：好机器不许被一起劝退 —— 公网确实可能有公开副本。"""
        hint = _next_action(_wall(PUBLIC_WALL))
        assert "Internet Archive" in hint, hint
        assert "Switch source" in hint, hint
        # 但要说清那份是什么：某一天的登出态快照，不是这个账号里的数据
        assert "logged-out" in hint, hint
        assert "web_act" not in hint, hint

    def test_the_page_type_branch_agrees_with_the_error_branch(self):
        """同一个决定有两条进来的路（error 串 / page_type），两处必须说同一句话。"""
        page_only = _wall(LOCAL, error="", content_ok=True)
        hint = _next_action(page_only)
        assert "the host YOU named" in hint, hint
        assert "Internet Archive" not in hint, hint

    def test_the_machine_readable_twin_reads_the_same_geography(self):
        """`diagnosis.recover_with` 与 `next_action` 是同一决定的两个落点。

        表里那句 "or the Internet Archive copy" 是**没有 URL 可问**的那条路（feed 行、
        批量行）的作者，公网形状留着它是对的；私网形状如果也从表里取，两个格子就
        互相否决 —— D5 那条法点名的正是这个形状。
        """
        private = quality._diagnose(_wall(LOCAL))
        public = quality._diagnose(_wall(PUBLIC_WALL))
        assert private["code"] == public["code"] == "auth_wall"
        assert "try the Internet Archive" not in private["recover_with"], private
        assert "logged-in copy" in private["recover_with"], private
        assert "cred_defaults" in private["recover_with"], private
        assert "Internet Archive" in public["recover_with"], public

    def test_the_row_shape_without_a_url_keeps_the_generic_door(self):
        """没有 url 可判时不许猜：档案站那句仍然带着"登出态快照"的限定留下。"""
        from dhole_mcp.quality import row_diagnosis
        row = row_diagnosis("auth_wall_detected: login form")
        assert row and "Internet Archive" in row["recover_with"], row
        assert "logged-out" in row["recover_with"], row

    def test_the_verdict_survives_the_real_dispatch(self, monkeypatch):
        """走一次真 `web_read`：判据要认的是最终信封上的 url，不是调用方刚写的那串。

        `force_fetcher='http'` 是为了让这一发只走 HTTP 一档 —— 否则一次墙判定会去
        要浏览器，而 conftest 的守卫拦住真启动（那是另一件事的测试）。
        """
        monkeypatch.setenv("DHOLE_ALLOW_PRIVATE_HOSTS", "127.0.0.1")

        async def fake(self, url, **kwargs):
            return _wall(url)

        monkeypatch.setattr(MasterFetchServer, "get", fake)
        out = asyncio.run(MasterFetchServer().web_read(url=LOCAL, cache_ttl=0,
                                                       force_fetcher="http"))
        assert "login/sign-in wall" in out.next_action, out.next_action
        assert "cred_defaults" in out.next_action, out.next_action
        assert "try the Internet Archive" not in out.next_action, out.next_action
        assert (out.diagnosis or {}).get("code") == "auth_wall", out.diagnosis


# ============================================================================
# 来源：test_the_sogou_card_gives_up_its_own_link_for_a_hidden_slot.py
# ============================================================================
# R01-015 · 搜狗那张卡的真链接就在卡里，dhole 却交出了隐藏推荐槽的埋点 URL。

# 真实版面的最小复制件（2026-10-14 抓自 www.sogou.com，签名段换成占位）：
# 卡片自己的链接在 h3 的 a 上，data-url 只存在于尾部那个隐藏的异步推荐槽里。
WEIXIN_CARD = """
<html><body><div class="vrwrap">
  <div class="struct201102">
    <h3 class="vr-title" vrcid="title.b429921">
      <a target="_blank" href="http://mp.weixin.qq.com/s?src=11&amp;timestamp=1791938050&amp;ver=7025&amp;signature=REDACTED&amp;new=1"
         id="sogou_vr_11002601_title_0">SaaS 成功的定价模型、策略</a>
    </h3>
    <div class="text-layout">
      <p class="star-wiki">wideplum 定价是 SaaS 赚钱的核心表现…</p>
      <a href="http://mp.weixin.qq.com/s?src=11&amp;timestamp=1791938050&amp;ver=7025&amp;signature=REDACTED&amp;new=1"
         class="citeLinkClass"><span>公众号</span><span class="cite-date">2020-06-04</span></a>
    </div>
  </div>
  <div class="r-sech ext_query result_list click-better-sugg" style="display:none"
       data-async="1" data-url="http://mp.weixin.qq.com/count/oIWsFt15DKTwbAz/2247485862/1&amp;scene=6"
       data-hint="sugg"><span>推荐您搜索</span></div>
</div></body></html>
"""

# 同一版面的正常卡：真链就在容器自己的 data-url 上，标题锚点是 /link?url= 相对包装。
# 这一张钉的是**次序**：修「选错属性」不许把绝大多数卡片从 data-url 改拽到包装链上。
ORDINARY_CARD = """
<html><body><div class="vrwrap" data-url="https://zhuanlan.zhihu.com/p/473352034">
  <h3 class="vr-title"><a href="/link?url=ISO9jdJlQdWn…">知乎的一篇 SaaS 定价</a></h3>
  <div class="fz-mid"><span>正文摘要</span></div>
</div></body></html>
"""


def _hrefs(html: str) -> list[str]:
    return [r.href for r in Sogou().extract_kept(html)]


def test_the_weixin_card_returns_the_article_link_not_the_tracking_slot():
    hrefs = _hrefs(WEIXIN_CARD)
    assert len(hrefs) == 1, hrefs
    href = hrefs[0]
    assert "/count/" not in href, (
        f"交出的是隐藏推荐槽的埋点计数链，用户点开是 404：{href}")
    assert href.startswith("http://mp.weixin.qq.com/s?"), href


def test_an_async_slot_is_not_a_result_target_even_when_it_is_the_only_data_url():
    """修法的判据是结构（`data-async` 的槽由脚本后填，不是这条结果本身），
    不是「这条 URL 长什么样」—— 不许按 count/、scene= 这类串做特判。"""
    hrefs = _hrefs(WEIXIN_CARD)
    assert "data-hint" not in hrefs[0]
    assert "scene=6" not in hrefs[0], hrefs[0]


def test_the_usual_card_still_uses_its_own_data_url():
    """反向守卫：锚点是包装链时，不许因为「改成优先锚点」而把行交坏。"""
    hrefs = _hrefs(ORDINARY_CARD)
    assert hrefs == ["https://zhuanlan.zhihu.com/p/473352034"], hrefs


def test_the_captured_weixin_shape_is_what_the_repo_thinks_it_is():
    """用例的前提自查：如果哪天版面里没有「隐藏槽 + 卡内锚点」这个形状，本文件要先红，
    而不是留下三条不再关于任何真实版面的断言。"""
    rows = Sogou().extract_kept(WEIXIN_CARD)
    assert len(rows) == 1
    assert "SaaS" in rows[0].title
