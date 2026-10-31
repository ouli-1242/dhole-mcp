"""同一个地址的两种拼法（默认端口、www.）不该在结果列表里占两行。

跨后端去重的唯一键是归一化 URL。引擎之间对 `www.` 与 `:443` 的写法并不统一：
同一篇文章可能被一家写成 `https://example.com/a`、另一家写成
`https://www.example.com:443/a`。它们此前是两行，读者看到的是两个独立来源。
"""

import pytest

from dhole_mcp.search_metasearch import _normalize_url as norm

pytestmark = pytest.mark.smoke


def test_an_http_default_port_is_the_same_address():
    assert norm("http://example.com:80/a") == norm("http://example.com/a")


def test_an_https_default_port_is_the_same_address():
    assert norm("https://example.com:443/a") == norm("https://example.com/a")


def test_a_non_default_port_stays_a_different_address():
    assert norm("https://example.com:8443/a") != norm("https://example.com/a")
    assert norm("http://example.com:8080/a") != norm("http://example.com/a")


def test_the_www_alias_merges_with_the_bare_host():
    assert norm("https://www.example.com/a") == norm("https://example.com/a")
    assert norm("https://WWW.Example.COM/a") == norm("https://example.com/a")


def test_a_subdomain_that_starts_with_www_is_only_an_alias_when_the_rest_is_a_host():
    # 名为 www.com 的主机真实存在：剥掉前缀会把它并进 TLD `com`，那不是同一页。
    assert norm("https://www.com/a") != norm("https://com/a")


def test_a_credential_url_keeps_its_netloc_verbatim():
    raw = "https://user:pw@www.example.com:443/a"
    assert norm(raw) == raw


def test_an_ipv6_default_port_is_stripped_but_the_brackets_stay():
    assert norm("http://[::1]:80/a") == "http://[::1]/a"
    assert norm("https://[::1]:8443/a") != norm("https://[::1]/a")


def test_the_mail_and_click_ids_merge_while_real_query_params_stay_apart():
    base = norm("https://e.test/p?page=2")
    for tag in (
        "msclkid", "yclid", "_hsenc", "_hsmi", "mkt_tok", "trk",
        "trkcampaign", "sc_cid", "vero_id", "wickedid",
        "oly_anon_id", "oly_enc_id",
    ):
        assert norm(f"https://e.test/p?page=2&{tag}=abc") == base, tag
    assert norm("https://e.test/p?page=3") != base
