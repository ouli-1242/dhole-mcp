"""R3刀1（P1-1 收尾）：探测过期时，那道守卫的拒绝被贴上「参数错了」的标签，还被剪掉了尾巴。

现场（2026-10-07 复测）：fake-ip TUN 在会话中途上线，进程启动时那次探测说
`public` 并终身缓存 → 公网名从此解析进保留段 → DNS 复查层拒绝。信封里写的却是
`invalid_request` + 「Correct the argument」——参数没有一处可改，唯一出路是**重启宿主**
让启动探测重判（`dhole --doctor` 能看见现判）。而 discover/crawl 那条路上，这段
300 字符的解释被 `[:200]` 剪在句子中间，读者连"这是守卫"都看不清。

三段修法（本刀不动探测机制本身，那需要 operator 判决，见 R3刀2）：
  1. 这一类拒绝有专属 diagnosis（`resolver_guard` / retryable=false /
     recover_with 点名"重启宿主 + dhole --doctor"），next_action 与它说同一句真话；
  2. dhole 自己写好的拒绝（SecurityError）不再被 200 字符帽子吃掉尾巴；
  3. 缓存查找先于 hostname 复验：命中一个包都不发，因此没有 SSRF 面 —— 过期探测
     不该把**已有**的正文也一起吞掉。
"""

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp.cache import set_cached
from dhole_mcp.security import SecurityError
from dhole_mcp.server import MasterFetchServer, _tier_error_text

# 现场那一条的骨架：公网名 → 保留段地址（`_resolves_to_internal` 的原话形状）
GUARD_MSG = ("URL hostname docs.example.com resolves to internal/private IP "
             "(10.5.5.5 in 10.0.0.0/8). This guard is fail-closed: dhole auto-detects "
             "fake-ip TUN resolvers (public names answering with reserved addresses) "
             "and skips the layer only for them; this hostname still resolved into a "
             "private range.")


def _flip_the_resolver(monkeypatch, url: str, ip: str = "10.5.5.5",
                      public: str = "93.184.216.34") -> None:
    """让解析器把一个名字领进私网段，其余名字照常回公网地址（stub 翻转探测现场）。

    地址取 10.0.0.0/8 —— 守卫的内网表里认它，198.18.0.0/15 只在**探测**那一步用作
    fake-ip 判据，复验层并不把它列为内网。
    """
    import socket

    from dhole_mcp import security as sec

    sec._DNS_CHECK_CACHE.clear()
    hostname = url.split("//", 1)[1].split("/")[0]

    def fake_getaddrinfo(host_, port=None, *a, **kw):
        addr = ip if host_ == hostname else public
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    # 进程记着的那句话：public —— 于是 DNS 复查层常开，而它现在会咬。
    monkeypatch.setattr(sec, "probe_fake_ip", lambda: ("public", "", ""))


class TestTheDiagnosis:

    @pytest.mark.asyncio
    async def test_the_refusal_is_not_called_a_wrong_argument(self, monkeypatch):
        url = "https://docs.example.com/page"
        _flip_the_resolver(monkeypatch, url)
        out = await MasterFetchServer().web_read(url=url, cache_ttl=0)
        assert "resolves to internal/private IP" in out.error, out.error
        assert "Correct the argument" not in out.next_action, out.next_action

    @pytest.mark.asyncio
    async def test_the_diagnosis_names_the_real_recovery(self, monkeypatch):
        url = "https://docs.example.com/page"
        _flip_the_resolver(monkeypatch, url)
        out = await MasterFetchServer().web_read(url=url, cache_ttl=0)
        diag = out.diagnosis
        assert diag is not None, "error 有句子，机器可读的孪生也得有"
        assert diag["code"] == "resolver_guard", diag
        assert diag["retryable"] is False, "重发同一发不会改变答案：进程记着旧判定"
        assert "restart" in diag["recover_with"] and "doctor" in diag["recover_with"]

    @pytest.mark.asyncio
    async def test_next_action_says_the_same_thing_as_the_diagnosis(self, monkeypatch):
        url = "https://docs.example.com/page"
        _flip_the_resolver(monkeypatch, url)
        out = await MasterFetchServer().web_read(url=url, cache_ttl=0)
        assert "restart" in out.next_action and "doctor" in out.next_action

    @pytest.mark.asyncio
    async def test_other_ssrf_refusals_keep_their_own_wording(self, monkeypatch):
        """云元数据那条永久边界不许被这次改动改成"重启宿主"——它重启也不变。"""
        out = await MasterFetchServer().web_read(
            url="http://169.254.169.254/latest/meta-data/")
        assert "design boundary" in out.next_action, out.next_action
        assert "restart" not in out.next_action


class TestTheTailIsNotClipped:
    """`[:200]` 的帽子是给 Playwright 的 call-log 准备的，不该吃掉 dhole 自己写的拒绝。"""

    def test_a_security_error_keeps_its_whole_sentence(self):
        out = _tier_error_text(SecurityError(GUARD_MSG))
        assert out == GUARD_MSG, out
        assert out.endswith("private range.")

    def test_other_exception_text_is_still_clipped(self):
        """浏览器层的 call-log 那种噪声仍旧剪：豁免只给写得完整的拒绝句。"""
        assert len(_tier_error_text(RuntimeError("x" * 900))) == 200

    def test_the_cap_is_not_the_problem_for_an_ordinary_error(self):
        assert len(_tier_error_text(ValueError("y" * 900))) == 200

    def test_the_guard_summary_ends_where_a_word_ends(self):
        """重启后实测的另一处：summary 标签 `msg[:80]` 停在 "…can compile (S" 这种半截词上。

        整句仍在 error 里，标签可以短，但不能短得像坏掉的字符串。
        """
        out = server_mod._resolver_guard_result("https://docs.example.com/", GUARD_MSG)
        prefix = "refused by the SSRF resolver guard · "
        assert out.summary.startswith(prefix)
        tail = out.summary[len(prefix):]
        assert GUARD_MSG[len(tail)] == " ", f"summary 停在词中间: {tail!r}"
        # 【原判据搬家 · 评审 R02-113】这一颗从前钉的是 `out.error == GUARD_MSG` —— 那句裸文本
        # 正是"不穿信封"的那一副脸：同一族的私网拒绝、URL 形状拒绝、存储门拒绝都带
        # `invalid_request:` 前缀，只有守卫这一族五扇门不带（r172 的六张脸）。产品补齐前缀之后，
        # 这条断言若原样留着就等于把缺陷钉成契约。**没有放宽**：整句仍然逐字必须在，
        # 只是按信封契约带着前缀在 —— 剪词那一害（上面那颗）与"不许把责任推给参数"那一族断言
        # 一字未动，16 格同文件其余全绿。
        assert out.error == f"invalid_request: {GUARD_MSG}", out.error
        assert GUARD_MSG in out.error, "整句被换了，不只是加了前缀"


class TestDiscoverAndCrawlSayTheWholeSentence:
    """评审现场的第二处：discover/crawl 把 300 字符的解释剪在 200，尾巴正是出路。"""

    @pytest.mark.asyncio
    async def test_the_guard_sentence_survives_the_crawl_envelope(self, monkeypatch):
        url = "https://docs.example.com/"
        _flip_the_resolver(monkeypatch, url)
        out = await MasterFetchServer().web_discover(url=url)
        assert "resolves to internal/private IP" in out.error, out.error
        assert out.error.rstrip().endswith("whatever this machine measured."), out.error
        assert "restart" in out.next_action and "doctor" in out.next_action
        assert len(out.error) > 200, "帽子还在：豁免必须是 SecurityError 才有"


PAGE = "https://docs.example.com/page"


@pytest.fixture()
def stored_copy(monkeypatch):
    """本机缓存里放一份这份页面的正文，并让读侧指纹与写侧对齐。

    `_cache_context` 打平为空串：`set_cached` 的默认 ctx 也是空串，于是这个用例读到
    的就是那份正文。指纹机制本身由 test_cache*.py 那一组守着，不在这里重复。
    """
    monkeypatch.setattr(server_mod, "_cache_context", lambda *a, **k: "")
    return PAGE


class TestTheCacheOutlivesTheStaleVerdict:
    """守卫拦的是**要发出的那一发**；命中一个包都不发，所以它拦不到（R3刀1）。"""

    @pytest.mark.asyncio
    async def test_a_stored_page_is_still_readable(self, monkeypatch, stored_copy):
        await set_cached(stored_copy, "markdown", ["the page as stored"], status=200)
        _flip_the_resolver(monkeypatch, stored_copy)

        out = await MasterFetchServer().web_read(url=stored_copy)

        assert not out.error, out.error
        assert out.content == ["the page as stored"]
        assert out.cached is True and out.fetcher_used == "cache"

    @pytest.mark.asyncio
    async def test_the_hit_says_the_next_request_is_still_refused(self, monkeypatch,
                                                                  stored_copy):
        """答案是真的，门也真的还堵着 —— 只报喜会让下一发撞上去时毫无准备。"""
        await set_cached(stored_copy, "markdown", ["body"], status=200)
        _flip_the_resolver(monkeypatch, stored_copy)

        out = await MasterFetchServer().web_read(url=stored_copy)

        assert "any NEW fetch of this URL is still refused" in out.summary, out.summary

    @pytest.mark.asyncio
    async def test_cache_ttl_0_still_asks_the_guard(self, monkeypatch, stored_copy):
        """cache_ttl=0 要的就是那一发新包 —— 守卫拦的正是它，这份抄本不能顶上去。"""
        await set_cached(stored_copy, "markdown", ["body"], status=200)
        _flip_the_resolver(monkeypatch, stored_copy)

        out = await MasterFetchServer().web_read(url=stored_copy, cache_ttl=0)

        assert "resolves to internal/private IP" in out.error, out.error
        assert out.diagnosis["code"] == "resolver_guard"

    @pytest.mark.asyncio
    async def test_a_schema_call_is_not_answered_with_the_plain_copy(self, monkeypatch,
                                                                     stored_copy):
        """schema 要在原始 HTML 上重跑选择器：匿名抓来的 markdown 正文没被问过。"""
        await set_cached(stored_copy, "markdown", ["body"], status=200)
        _flip_the_resolver(monkeypatch, stored_copy)

        out = await MasterFetchServer().web_read(
            url=stored_copy, schema={"properties": {"title": {"selector": "h1"}}})

        assert "resolves to internal/private IP" in out.error, out.error

    @pytest.mark.asyncio
    async def test_a_check_call_is_not_answered_with_the_plain_copy(self, monkeypatch,
                                                                    stored_copy):
        """`check` 问的是「站方说变了吗」，答案只能是站方的 —— 抄本答不了。"""
        await set_cached(stored_copy, "markdown", ["body"], status=200)
        _flip_the_resolver(monkeypatch, stored_copy)

        out = await MasterFetchServer().web_read(url=stored_copy,
                                                 extraction_type="check")

        assert "resolves to internal/private IP" in out.error, out.error

    @pytest.mark.asyncio
    async def test_a_head_asks_the_server_and_the_copy_cannot_answer(self, monkeypatch,
                                                                    stored_copy):
        await set_cached(stored_copy, "markdown", ["body"], status=200)
        _flip_the_resolver(monkeypatch, stored_copy)

        out = await MasterFetchServer().web_read(url=stored_copy, method="HEAD")

        assert "resolves to internal/private IP" in out.error, out.error

    @pytest.mark.asyncio
    async def test_the_site_s_current_robots_rules_still_stop_the_copy(
            self, monkeypatch, stored_copy):
        """合规门不倒：站方规则（缓存里读得到，不必发包）说不许，就不交抄本。"""
        await set_cached(stored_copy, "markdown", ["body"], status=200)
        _flip_the_resolver(monkeypatch, stored_copy)
        # 这台机器上 DHOLE_IGNORE_ROBOTS=1 是常开的（评审现场如此），要读的这条
        # 分支得先让它"在管"，否则测的是绕过分支。
        monkeypatch.setattr(server_mod, "robots_env_disabled", lambda: False)

        class _Disallowed:
            @staticmethod
            def cached(url, **kw):
                return False

        monkeypatch.setattr(server_mod, "get_robots_cache", lambda: _Disallowed)

        out = await MasterFetchServer().web_read(url=stored_copy)

        assert "resolves to internal/private IP" in out.error, out.error

    @pytest.mark.asyncio
    async def test_the_compliance_stance_of_a_guard_hit_is_said_honestly(
            self, monkeypatch, stored_copy):
        """这次没查 robots.txt（查它要发包），姿态字段不能替它说「complied」。"""
        await set_cached(stored_copy, "markdown", ["body"], status=200)
        _flip_the_resolver(monkeypatch, stored_copy)
        monkeypatch.setattr(server_mod, "robots_env_disabled", lambda: False)

        class _NoRecord:
            @staticmethod
            def cached(url, **kw):
                return None

        monkeypatch.setattr(server_mod, "get_robots_cache", lambda: _NoRecord)

        out = await MasterFetchServer().web_read(url=stored_copy)

        assert out.content == ["body"]
        assert out.metadata.get("robots") == (
            "not checked (no cached robots.txt, and this call sends nothing)"
        ), out.metadata.get("robots")
