# -*- coding: utf-8 -*-
"""R01-015 · 搜狗那张卡的真链接就在卡里，dhole 却交出了隐藏推荐槽的埋点 URL。

P3 留给 P4 的区分实验跑完了（2026-10-14，活体抓 `www.sogou.com/web?query=SaaS 定价 策略 微信`，
原始 SERP 存 `review/.runtime/p4/sogou_serp.html`）。结论与「引擎就给这种链、dhole 只是照抄」
**相反**：那张公众号卡片的 `<h3 class="vr-title"><a href="http://mp.weixin.qq.com/s?src=11&…">`
里就是真文章链接（同一条链在卡内出现 3 次：标题、缩略图、底部 cite 链接），而 dhole 交出去的那条
`http://mp.weixin.qq.com/count/<sig>/<mid>/<idx>&scene=6` 属于**卡片尾部一个
`style="display:none" data-async="1" data-hint="sugg"` 的异步推荐槽** —— 那里唯一的
`@data-url` 是搜狗自己的埋点计数链。实测那条链 404（`sessions/P03-s7-weixin-url.json`：
archive.org 也声明没有快照）。

所以罪在选错了属性：`.//@data-url` 在整棵子树里找，命中了那个不属于「这条结果是什么」的槽。
"""

from __future__ import annotations

from dhole_mcp.search_metasearch import Sogou

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
