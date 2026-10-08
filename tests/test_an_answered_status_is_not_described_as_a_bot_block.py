"""站点答过的状态码，不许在另一格里被说成"墙"（2026-10-08 重启 MCP 后的 live 复核 · D-28）

现场（真调用，重启后的 server）：

    web_read(url="https://httpbin.org/status/503", cache_ttl=0)
      error       : "... - The origin answered 503: the failure is at the server, and both
                     tiers were told the same thing. ... - A 5xx is not a bot block."
      next_action : "All fetch tiers failed. The site may use unbypassable protection
                     (DataDome/Akamai/Turnstile) or is unreachable - switch sources."

一份回执里两句话互相拆台。两半各有作者：`error` 那串 tips 按**状态码**说话
（`server._all_tiers_failed_tips`，D-20 那一刀就是照这个建的），而 `next_action` 按
**文本分类**说话 —— 分类器读的是我们自己写的 tips 与 Playwright 的
`net::ERR_HTTP_RESPONSE_CODE_FAILURE`，读不出 503，于是落进 catch-all，而那一句恰好
断言了"可能是 DataDome/Akamai/Turnstile"和"不可达"两件被 `error` 明确否认的事。
调用方读哪一格，就会照着哪一格去做：这一条会把人从"这是站方 5xx"支去"换个源 + 找代理"。

修法只收紧假的那一半：状态码 >= 400 意味着**有答案**，此时不再猜墙、也不再说不通，
把"这个状态码意味着什么、什么能改变它"指回 `error`（那个判据唯一的作者）。真沉默
（status 0）那一支照旧 —— 沉默才允许怀疑墙或不可达，虽然那边的 tips 也说"墙会答，不会
沉默"，此处只是不否认它。

反向锁：429 这种被限流的答案同样照 429 说；真沉默（status 0）那两句候选解释照旧留着；
而 D-08b 那句"本地服务是不是没起"不许盖到**回了状态码**的私网目标上 —— 回了 503 就说明
它在听。
"""

from __future__ import annotations

from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _agent_hints
from dhole_mcp.server import _all_tiers_failed_tips

ALL_TIERS_PREFIX = "all_tiers_failed: http_503 (HTTP status 503)\nAttempted: HTTP -> Stealthy"


def _failed(status: int, *, content: str = "", url: str = "https://origin.test/page",
            error: str | None = None) -> tuple[str, str, bool]:
    result = ResponseModel(
        url=url, status=status, content=[content] if content else [],
        fetcher_used="http→stealthy",
        error=error if error is not None else (
            f"{ALL_TIERS_PREFIX}\n{_all_tiers_failed_tips(f'http_{status}', status)}"),
    )
    return _agent_hints(result)


class TestAnAnsweredStatusIsNamedAsAnAnswer:

    def test_a_503_that_every_tier_saw_is_not_reported_as_a_wall(self):
        _summary, next_action, _ok = _failed(503)
        assert "HTTP 503" in next_action, next_action
        assert "unbypassable protection" not in next_action, next_action
        assert "DataDome" not in next_action and "Turnstile" not in next_action, next_action

    def test_it_does_not_claim_the_page_is_unreachable_either(self):
        """`error` 说"答案到了"，`next_action` 就不许说"够不着"——要说也得是否认。"""
        _summary, next_action, _ok = _failed(503)
        assert "the origin answered" in next_action, next_action
        assert "neither an unreachable host" in next_action, next_action

    def test_a_429_is_named_at_429(self):
        _summary, next_action, _ok = _failed(
            429, error="all_tiers_failed: http_429 (HTTP status 429)\n"
            + _all_tiers_failed_tips("http_429", 429))
        assert "HTTP 429" in next_action, next_action
        assert "unbypassable protection" not in next_action, next_action

    def test_the_pointer_goes_to_the_field_that_already_answers(self):
        """两处不许各写一份判据：这一格只说"有答案"，含义指回 `error`。"""
        _summary, next_action, _ok = _failed(503)
        assert "`error`" in next_action, next_action


class TestSilenceStillGetsTheOpenQuestion:
    """反向锁：真的一个字节都没答时，catch-all 那两句仍是唯二可能的解释。"""

    def test_no_answer_at_all_still_names_the_two_candidates(self):
        result = ResponseModel(url="https://origin.test/page", status=0, content=[],
                               fetcher_used="http→stealthy",
                               error="all_tiers_failed: unknown (HTTP status 0)\n"
                                    + _all_tiers_failed_tips("unknown", 0))
        _summary, next_action, _ok = _agent_hints(result)
        assert "unbypassable protection" in next_action or "unreachable" in next_action, \
            next_action

    def test_a_private_target_that_ANSWERED_is_not_asked_whether_it_is_up(self):
        """D-08b 的反向锁：那句"本地服务是不是没起"只属于**一个字节都没答**的那一族。
        回环端口自己回了 503，就说明它在听 —— 再问"是不是没起"是新的假话。"""
        _summary, next_action, _ok = _failed(503, url="http://127.0.0.1:3000/api")
        assert "local service" not in next_action, next_action
        assert "HTTP 503" in next_action, next_action
