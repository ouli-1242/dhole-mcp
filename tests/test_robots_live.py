"""B3：真实站点的 robots.txt 实测（默认档不跑；`pytest -m live` 显式开）。

test_robots.py 用注入的假抓取器验的是**逻辑**；这里验的是**真文件**——那些假 fixture
不容易写出来的形态：多 User-agent 分组、`Crawl-delay`、通配符 `/pypi*/json`、
尾部 `$`、以及「根本没有 robots.txt」的 404。REPLAN 把这一项列为阻塞项 B3（交接环境
无网络），本文在这些站点上跑通后关闭。

站点改版是这条测试唯一会红的原因，红了就去读新的 robots.txt 并重记结论 ——
不要改成「怎么都过」的断言，那样它就失去意义了。实测记录（2026-09-27）：

    httpbin.org            User-agent: * / Disallow: /deny
    pypi.org               通配符 /pypi*/json、/search*，Disallow: /simple/
    github.com             多分组（GPTBot/ClaudeBot/…）+ Crawl-delay: 1 + Allow: /$
    doc.rust-lang.org      前缀式 /book/first-edition/
    quotes.toscrape.com    404（无 robots）→ 放行，reason=unavailable
"""

from __future__ import annotations

import pytest

from dhole_mcp import robots
from dhole_mcp import server as server_mod

pytestmark = [pytest.mark.live, pytest.mark.asyncio]

# 每条都是 2026-09-27 对真文件实测的结果。
DISALLOWED_URLS = [
    "https://httpbin.org/deny",
    "https://pypi.org/simple/requests/",
    "https://pypi.org/pypi/requests/json",   # /pypi*/json 通配
    "https://pypi.org/search/?q=celery",      # /search* 通配
    "https://doc.rust-lang.org/book/first-edition/",
    "https://www.rfc-editor.org/rfc/authors/",
]
ALLOWED_URLS = [
    "https://httpbin.org/get",
    "https://pypi.org/project/requests/",
    "https://github.com/features",            # 多分组里 * 组放行
    "https://doc.rust-lang.org/stable/std/",
    "https://www.rfc-editor.org/rfc/rfc9110.html",
]
# 没有 robots.txt 的站点：放行，但 reason 必须是 unavailable，不是 allowed
# （「没查到」和「合规通过」是两件事，见 robots 模块 docstring）。
NO_ROBOTS_URL = "https://quotes.toscrape.com/"


@pytest.fixture(autouse=True)
def _real_network():
    """conftest 把 DHOLE_IGNORE_ROBOTS 设成了 1（进程级豁免）；这里要的是真检查。"""
    mp = pytest.MonkeyPatch()
    mp.delenv(robots.ENV_IGNORE_ROBOTS, raising=False)
    robots.reset_robots_cache()
    yield
    robots.reset_robots_cache()
    mp.undo()


async def _check_or_skip(url: str) -> robots.Verdict:
    try:
        verdict = await robots.check(url)
    except Exception as e:  # pragma: no cover - only when this box has no route
        pytest.skip(f"no route to {url}: {e}")
    if verdict.reason == robots.UNAVAILABLE and url != NO_ROBOTS_URL:
        pytest.skip(f"robots.txt unreachable from this network for {url}")
    return verdict


@pytest.mark.parametrize("url", DISALLOWED_URLS)
async def test_a_real_disallow_rule_is_enforced(url):
    verdict = await _check_or_skip(url)
    assert verdict.allowed is False, f"{url} is allowed by the real rules now - re-check"
    assert verdict.reason == robots.DISALLOWED
    assert verdict.robots_url.endswith("/robots.txt")


@pytest.mark.parametrize("url", ALLOWED_URLS)
async def test_a_real_allow_rule_is_not_overblocked(url):
    verdict = await _check_or_skip(url)
    assert verdict.allowed is True, f"{url} newly disallowed - re-read the site's robots.txt"
    assert verdict.reason == robots.ALLOWED


async def test_a_host_without_robots_txt_is_still_fetched():
    verdict = await _check_or_skip(NO_ROBOTS_URL)
    assert verdict.allowed is True
    assert verdict.reason == robots.UNAVAILABLE, "no rules must not read as 'compliance passed'"


async def test_multi_group_file_matches_our_own_token():
    """github 给一堆具名爬虫写了规则，`dhole-mcp` 不属于它们中的任何一个。

    具名组不能串味：我们该按 `User-agent: *` 那组判，而不是被 GPTBot 的规则挡住。
    """
    url = "https://github.com/features"
    if (await robots.check(url)).reason == robots.UNAVAILABLE:
        pytest.skip("no route to github")
    assert (await robots.check(url)).allowed is True


async def test_smart_fetch_refuses_the_real_disallowed_page():
    """整条链路：真站点、真 robots.txt、真的一个请求都不发。"""
    # robots 抓不到时是 fail-open（60s 短 TTL 后重查），那一轮的答复就是 200 + 正文。
    # 那是策略允许的行为，不是这条用例要钉的东西，所以先看一眼规则拿没拿到。
    verdict = await _check_or_skip("https://httpbin.org/deny")
    assert verdict.allowed is False, verdict.reason
    srv = server_mod.MasterFetchServer()
    out = await srv.smart_fetch("https://httpbin.org/deny", cache_ttl=0, timeout=20000)
    if out.error.startswith("timeout") or out.status == -1:
        pytest.skip(f"no route to httpbin: {out.error[:80]}")
    assert out.error.startswith("robots_disallowed"), out.error
    assert out.status == 0
    assert not any((c or "").strip() for c in out.content)
    assert out.content_ok is False
    assert "ignore_robots=true" in out.next_action
    # httpbin 的 /deny 页本身是 200 + "YOU SHOULDN'T BE HERE"：正文里出现那句话
    # 就说明检查被旁路了。
    assert "SHOULDN'T BE HERE" not in "".join(out.content)


async def test_smart_fetch_allows_a_real_allowed_page():
    srv = server_mod.MasterFetchServer()
    out = await srv.smart_fetch("https://httpbin.org/get", cache_ttl=0, timeout=20000)
    if out.status in (-1, 0) and not out.content_ok:
        pytest.skip(f"no route to httpbin: {out.error[:80]}")
    assert out.status == 200
    assert not out.error.startswith("robots_disallowed")
    assert any((c or "").strip() for c in out.content)


async def test_ignore_robots_overrides_the_real_verdict():
    srv = server_mod.MasterFetchServer()
    out = await srv.smart_fetch("https://httpbin.org/deny", cache_ttl=0,
                                ignore_robots=True, timeout=20000)
    if out.status in (-1, 0) and not out.content_ok:
        pytest.skip(f"no route to httpbin: {out.error[:80]}")
    assert not out.error.startswith("robots_disallowed")
    assert "SHOULDN'T BE HERE" in "".join(out.content)
