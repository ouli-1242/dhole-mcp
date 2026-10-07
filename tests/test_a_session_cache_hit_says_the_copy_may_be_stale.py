"""R2刀5（P2-6）：登录会话命中的是**匿名时代**的缓存体，而响应一个字都不提。

现象（评审实测）：命名 `session_id` 登录之后，TTL 内的 `web_read` 直接从缓存出——
缓存按 URL 键，不看会话身份。于是调用方拿到的是登录前的那份页面（saucedemo 的
`Login` 而非 `Logout`），而 summary 里只有 `cached`，没有任何一句"这不是你登录态
下的版本"。cookie 桥本身是好的（`cache_ttl=0` 之后 Logout 就出现了），缺的是**说**。

两段修法：
  1. 语料（不占 wire）：登录流程那两处（recipes.md 的 "Sites behind login" 与
     SKILL.md 的登录那一行）显式写回读要带 `cache_ttl=0` —— 过去这句只在 fetcher
     档位的语境里出现过，读的人不会想到它也是登录回读的解法；
  2. 行为：`session_id` 在场且这一发是缓存命中时，summary 注记一句。缓存按会话
     身份分键在「不修清单」里（键结构变更有全局成本），注记 + 语料是这一刀的边界。
"""

from pathlib import Path

from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _with_agent_hints

ROOT = Path(__file__).resolve().parents[1] / "src" / "dhole_mcp" / "skills" / "dhole-web"


def _body() -> list[str]:
    return ["real page text " * 30]


def _finalize(**kw) -> ResponseModel:
    return _with_agent_hints(ResponseModel(url="https://shop.example.test/account",
                                           status=200, content=_body(),
                                           content_ok=True, **kw))


class TestTheCorpusNamesTheFreshRead:
    """语料守卫：登录那两行必须说出「回读要 cache_ttl=0」。"""

    def test_recipes_login_flow_says_it(self):
        text = (ROOT / "references" / "recipes.md").read_text(encoding="utf-8")
        login = text.split("## Sites behind login", 1)[1].split("\n## ", 1)[0]
        assert "cache_ttl=0" in login, login
        assert "cache is keyed by URL" in login or "not by" in login

    def test_skill_login_row_says_it(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        row = next(line for line in text.splitlines()
                   if "Log into a site WITHOUT handing over a password" in line)
        assert "cache_ttl=0" in row, row


class TestTheCacheHitUnderASessionSaysSo:

    def test_a_session_cache_hit_carries_the_note(self):
        out = _finalize(cached=True, session_id="acct")
        assert "predate" in out.summary, out.summary
        assert "cached" in out.summary

    def test_a_cache_hit_without_a_session_says_nothing(self):
        """没有会话就没有"你的登录态"这回事——那句注记会变成一个假问题。"""
        out = _finalize(cached=True)
        assert "predate" not in out.summary, out.summary

    def test_a_live_fetch_under_a_session_says_nothing(self):
        out = _finalize(cached=False, session_id="acct")
        assert "predate" not in out.summary, out.summary

    def test_the_note_is_appended_after_the_existing_cache_marker(self):
        """注记是追加：`cache · cached` 那两个词本来就是"这一发没发包"的信号。"""
        base = _finalize(cached=True, fetcher_used="cache").summary
        with_session = _finalize(cached=True, fetcher_used="cache",
                                 session_id="acct").summary
        assert with_session.startswith(base), (base, with_session)
        assert "cached" in base and "predate" in with_session[len(base):]
