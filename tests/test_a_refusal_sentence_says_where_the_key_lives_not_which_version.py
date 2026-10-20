"""交出去的话里不许带版本号：三处句子只说"现在在哪儿"，不说"哪一版改的"。

台账 R02-033（P02-09 沈序）点名的那一发：

    web_read(url=…, options={'retries': 3})
      error = "Unsupported argument(s) for web_read: ['options']. The options object was
               removed in 17.0 - its keys are top-level parameters now, so pass them beside
               url, not inside an object. Supported: […]"

他的说法：「报错里别写版本号，写这个键现在在哪儿。」两条理由：

① **对不在该版本上的调用方是误导**。运行时并不知道对面看到的是哪一版；同一句话在旧版上是
   错的，而它被写在"告诉您该怎么做"的那一格里 —— 假话出现在处方位置上比缺失更贵。
② **本仓已经有这条规矩，并有门守着**：`test_the_corpus_carries_no_maintainer_history.py`
   把 SKILL.md + 六份 reference 钉死不许出现版本号/决策编号/批次代号。运行时消息是同一批
   文本的另一个出口，那个出口没在名单里（这就是三处一直没人拦的原因）。

现场量（`review/.runtime/p4/f49_measure.py`，用 `tokenize` 分注释与字符串）指出同一族三条腿：
`server.py` 的 options 袋子拒绝、`reranker.active_model()` 的遗留配置警告、
`search_proxy` 的池子警告。三处都还留着正确的去处（顶层键 / 唯一在册模型 / 用第一颗），
所以去掉版本号**不减信息量** —— 这正是本刀要的：信息量不许降，版本依赖清零。

三种**不是维护史**的数字不许被误伤（这也是为什么判据问句子，不问"有没有点号"）：
环回地址 `127.0.0.1`、UA 令牌 `Dhole-Sitemap/8.0`、pip 依赖声明 `pip install -e ".[all]"`。
"""
import logging
import re

from dhole_mcp import reranker, search_proxy
from dhole_mcp.params import ArgumentRefusal

# 与语料门同一族的形状：`1x.y` 后面不接时长、不接 IP 段（那条正则在册
# `tests/test_the_corpus_carries_no_maintainer_history.py:27`，这里只取版本那一项）。
VERSION_TALK = re.compile(r"(?<![\d.])1[0-9]\.[0-9xX]+(?![\d.s])")


def _options_refusal_text(options: dict | None = None) -> str:
    """把沈序那一发的句子取出来 —— 直接问写这句话的那位作者。

    `_reject_unknown_args(tool, args)` 就是 `server.py:4277-4303` 那三行 raise 的作者；
    从工具面绕一圈反而拿不到（校验在调用分发那一层）。
    """
    from dhole_mcp.server import _reject_unknown_args
    try:
        _reject_unknown_args("web_read", {"url": "http://x/",
                                          "options": options or {"retries": 3}})
    except ArgumentRefusal as e:
        return str(e)
    return ""


class TestTheOptionsRefusalStaysVersionFree:

    def test_no_version_token_in_the_refusal(self):
        text = _options_refusal_text()
        assert "options" in text, f"这一发本来就该点名 options：{text[:200]}"
        assert not VERSION_TALK.search(text), (
            f"拒绝话术里出现了版本号，调用方不在那一版时这句是假话：{text[:300]}")

    def test_the_key_still_says_where_it_lives(self):
        """去掉版本叙述之后，处方必须一寸不少：键在顶层、不要装进对象、支持集点名。"""
        text = _options_refusal_text()
        low = text.lower()
        assert "top-level" in low or "top level" in low, text[:300]
        assert "supported" in low, text[:300]

    def test_the_keys_that_can_move_out_are_still_named(self):
        """`leftover` 那一支（本次袋子里哪些键能原样搬出去）是本刀之前就在的信息，不许退。"""
        text = _options_refusal_text({"max_content_chars": 500})
        assert "max_content_chars" in text, text[:300]


class TestTheLegacyRerankerNoticeStaysVersionFree:

    def test_warning_names_what_runs_and_not_which_version_changed(self, monkeypatch,
                                                                    caplog):
        monkeypatch.setattr(reranker, "_legacy_selection", lambda: "m3-old-choice")
        with caplog.at_level(logging.WARNING):
            reranker.active_model()
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "m3-old-choice" in text, f"遗留值必须还在（它是要被删的那颗文件）：{text[:200]}"
        assert reranker.DEFAULT_MODEL in text, text[:200]
        assert not VERSION_TALK.search(text), (
            f"警告里写了哪一版删的：{text[:240]}")
        assert "delete" in text.lower(), f"出路（删掉那个文件）不许丢：{text[:240]}"

    def test_no_warning_when_the_config_matches_the_only_model(self, monkeypatch, caplog):
        """反面锁：没有遗留分歧时不许凭空说一句。"""
        monkeypatch.setattr(reranker, "_legacy_selection", lambda: reranker.DEFAULT_MODEL)
        with caplog.at_level(logging.WARNING):
            reranker.active_model()
        assert not VERSION_TALK.search(" ".join(r.getMessage() for r in caplog.records))
        assert "reranker.json" not in " ".join(r.getMessage() for r in caplog.records)


class TestTheProxyPoolNoticeStaysVersionFree:

    def test_one_line_of_proxies_says_what_it_does_now(self, monkeypatch, caplog):
        monkeypatch.setattr(search_proxy.env_registry, "value_of",
                            lambda name: "http://127.0.0.1:1/,http://p2:80"
                                          if name == "DHOLE_SEARCH_PROXY" else "")
        with caplog.at_level(logging.WARNING):
            got, source = search_proxy.configured_proxy()
        text = " ".join(r.getMessage() for r in caplog.records)
        assert got and "127.0.0.1" in got, f"第一颗照样要用：{got!r}"
        assert "127.0.0.1" in text or "first" in text.lower(), text[:240]
        assert not VERSION_TALK.search(text), (
            f"警告里写了哪一版删的池子：{text[:240]}")
        assert "rotat" in text.lower() or "first" in text.lower(), (
            f"「不轮换、只用第一颗」这一句信息量不许丢：{text[:240]}")
        assert source == "DHOLE_SEARCH_PROXY"


class TestTheLoopbackAndTheUAareNotCollateral:

    def test_loopback_and_pip_and_ua_tokens_survive(self):
        """判据问的是句子，不是"有没有点号"：这三种数字不是维护史。"""
        assert not VERSION_TALK.search("http://127.0.0.1:9222")
        assert not VERSION_TALK.search("Dhole-Sitemap/8.0")
        assert not VERSION_TALK.search('python -m pip install -e ".[all]"')
        assert not VERSION_TALK.search("~1.5-2.5s")
