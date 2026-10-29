"""一家引擎这一轮的产出该怎么判：一把梯子，一个作者。

这块本来有两个作者。`search_metasearch._classify_yield` 是响应侧那一份（`engine_health()`
用它给每引擎的 verdict），`updater._engine_yield_row` 里又手抄了一遍同样的分支阶梯给
`dhole -v` 那行用。抄的理由写在它自己的注释里，而且是个真约束：诊断命令要在精简安装 /
半坏安装上也能跑（那时才最需要看它），所以它刻意不 import 搜索层 —— 那一层会拉
primp/lxml/httpx/fake_useragent。不 import 搜索层是对的，**重抄判据**不是：两条阶梯会漂，
而漂了不报错，症状是同一个引擎在 `dhole -v` 里读作"健康"、在响应的 `engine_detail` 里
读作"解析器坏了"，读的人只能挑一个信 —— 而 `engine_empty` 的字段说明本来就写着
"Cross-check the 'engine yield' row of `dhole -v`"，两处一旦分家，被交叉核对的那一句就是
第二个答案。

所以判据住在这里：只用 stdlib，两边都 import 得到，谁都不因此拖重依赖。这里只交 token
（"这一轮它怎么了"的**名字**），措辞归各家：响应里 `engine_detail[].why` 那句话、CLI 那
一行的短语，都是各自按 token 取词，不重判。词表覆盖率由
tests/test_one_engines_yield_verdict_has_one_author.py 钉住 —— 判据新加一颗 token 而有
一张表漏了一格，就红，而不是静默把那一格读成"没问题"。

阈值只在这里写一次：`DRIFT_STREAK` / `MIN_SAMPLES_FOR_BASELINE` / `YIELD_TTL_S`。
（样本权重 `_YIELD_EWMA` 和 n/mean/drift/zero 的**记账**仍留在 search_metasearch：那里是
唯一写者，这里只读它落下的数。）
"""

from __future__ import annotations

from typing import Any, Dict, Optional

MIN_SAMPLES_FOR_BASELINE = 8   # 少于此数不下"这引擎平时有多少产出"的结论
DRIFT_STREAK = 2               # 连续几轮 item_nodes>0 而 usable==0 才算确认漂移
YIELD_TTL_S = 3600.0           # 超过这个时间没再跑过搜索，上一轮的结论就不是现状

#: 判据可能交回的全部 token。每个渲染方（`engine_detail` 的 `why`、`dhole -v` 那一行）
#: 都必须覆盖这一整批：少一格不是少一句短语，而是那一轮的失败在两个读处同时隐形。
VERDICTS = frozenset({
    "not_asked", "blocked", "timeout", "unreachable", "not_instrumented",
    "parser_drift", "parser_suspect", "filtered_out", "upstream_empty",
    "unknown_empty", "healthy", "stale",
})

#: 这颗 token 意味着什么 —— 一句、不带数。数由调用方补（响应带本轮的容器数/可用数，
#: CLI 带每轮均值）：同源的一句话，各自的账不缺。
VERDICT_WHY: Dict[str, str] = {
    "not_asked": "cancelled once other indexes had already met the result quota, so "
                 "this round observed nothing about it",
    "blocked": "it refused this round (rate-limit / CAPTCHA / circuit open)",
    "timeout": "it did not answer inside the round's deadline",
    "unreachable": "it could not be reached at all this round",
    "not_instrumented": "no item-node count was taken this round (JSON API backend, "
                        "or the fetch never reached parsing)",
    "parser_drift": "its page carried result containers and the parser took 0 usable "
                    "rows out of them, on consecutive rounds",
    "parser_suspect": "its page carried result containers and the parser took 0 usable "
                      "rows out of them (first round seeing this)",
    "filtered_out": "its page carried result containers and the parser DID take usable "
                    "rows out of them, but the engine's own post-filter dropped every "
                    "one (ads / self-links / redirect wrappers) - a filter problem, not "
                    "a parser problem",
    "upstream_empty": "its page held no result containers at all, though this engine "
                      "normally yields some",
    "unknown_empty": "its page held no result containers and there is no baseline to "
                     "call that unusual",
    "healthy": "it answered and the parser took usable rows out of it",
    "stale": "a previous run's verdict, not this round's observation",
}


#: `dhole -v` 那一行的短语：也是按 token 取词，分支不在那里重判一次。
#: 短语留在这一张表里而不是 updater 的代码里，理由与上面同一句 —— 覆盖率要能被守卫
#: 当场问出来（`set(VERDICT_CLI_LABEL) == VERDICTS`），散在 if 链里的词表问不出来。
VERDICT_CLI_LABEL: Dict[str, str] = {
    "not_asked": "not asked",
    "blocked": "blocked/cooled",
    "timeout": "timeout",
    "unreachable": "unreachable",
    "not_instrumented": "results",
    "parser_drift": "PARSER BROKEN",
    "parser_suspect": "PARSER BROKEN",
    "filtered_out": "filter dropped all",
    "upstream_empty": "0 nodes",
    "unknown_empty": "0 this query",
    "healthy": "ok",
    "stale": "no recent run",
}


def classify_yield(stats: Dict[str, Any], status: str, nodes: int, usable: int,
                   *, now: Optional[float] = None) -> str:
    """这一轮该引擎的健康判定。关键是区分"上游真的没结果"和"我们的解析器坏了"。

    只有 item_nodes==0 且 HTTP 200 那一格是真正含糊的（可能是查询本身在该索引里
    就没东西），它需要基线才可能收窄 —— 样本不够时如实报 unknown，不猜。

    `now` 给了才判新鲜度（这一行超过 :data:`YIELD_TTL_S` 没再被写过 = `stale`）。搜索层
    不传，因为它判的就是刚刚跑完的这一轮；CLI 传，因为它读的是磁盘上的历史行，而"上次
    运行的结论"被当成"当前状态"读正是这一族判据要消灭的那种谎。
    """
    if now is not None and now - _as_float(stats.get("ts"), 0.0) > YIELD_TTL_S:
        return "stale"
    if status == "preempted":
        return "not_asked"          # 够数了被取消，本轮无观测
    if status in ("blocked", "circuit_open"):
        return "blocked"
    if status == "timeout":
        return "timeout"
    if status.startswith(("error", "init_error", "no_key")):
        return "unreachable"
    if nodes < 0:
        return "not_instrumented"   # JSON API 引擎，或这轮没走到解析
    if nodes > 0 and usable == 0:
        if _as_int(stats.get("last_raw"), -1) > 0:
            return "filtered_out"
        return ("parser_drift" if _as_int(stats.get("drift"), 0) >= DRIFT_STREAK
                else "parser_suspect")
    if nodes == 0 and usable == 0:
        if (_as_int(stats.get("n"), 0) >= MIN_SAMPLES_FOR_BASELINE
                and _as_float(stats.get("mean"), 0.0) >= 1.0):
            return "upstream_empty"  # 平时有产出、这轮容器都没了
        return "unknown_empty"
    return "healthy"


def why_for(verdict: str) -> str:
    """这颗 token 的那句话。未知 token 不编好话 —— 说破它才看得见词表缺了一格。"""
    text = VERDICT_WHY.get(verdict)
    if text is None:
        return f"unclassified verdict '{verdict}' (the yield table has no line for it)"
    return text


def explain(verdict: str, nodes: int, usable: int, error: str = "",
            raw: int = -1) -> str:
    """把 token 与本轮的观测数合成 `engine_detail[].why` 那一句。

    顺序是刻意的：解析不出东西那一格**必须**把容器数与可用数带出来 —— 没有那两个数，
    "解析器坏了"与"这个查询真没结果"在响应上又同形了，而分开它们正是这一族判据存在的
    理由。`filtered_out` 还要把自滤前的可用数（`raw`）带出来：它证明解析器取到了行，
    丢掉它们的是后处理，修法完全不同。

    其余情况先交引擎层已经写好的原因串（冷却还剩几秒、HTTP 几几几、被谁取消都
    写在里面），谁都不许再抄写第二遍。
    """
    if verdict == "filtered_out":
        return (f"{nodes} result containers; {max(raw, 0)} had a usable link/title "
                f"before the engine's own filter, 0 survived it "
                f"({why_for(verdict)})")
    if verdict in ("parser_drift", "parser_suspect"):
        return (f"{nodes} result containers on its page, {usable} usable rows parsed"
                f" ({why_for(verdict)})")
    if error:
        return error
    if verdict == "healthy" and nodes >= 0:
        return f"{usable} usable rows from {nodes} item nodes"
    return why_for(verdict)


def _as_int(value: Any, default: int) -> int:
    # 不能用 `value or default`：0 在这里是信号本身（0 条可用正是漂移），
    # `or` 会把它读成"没有观测"。
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
