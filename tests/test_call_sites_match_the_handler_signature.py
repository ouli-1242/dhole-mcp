"""src/ 里对工具处理器的调用，实参名必须真的在那个处理器的签名里。

为什么需要它（这条守卫是一次真实回归的直接产物）：

B5-13c「身份不再逐次传」把 `auth` / `cookies` / `proxy` / `extra_headers` /
`allow_private` 五颗从参数面下线，但 `crawl.py` 里两处
`server.web_read(..., proxy=_crawl_proxy, ...)` 没跟着改。后果不是"少个功能"：
`web_read` 由 `_smart_fetch_request_context` 包着，包装器第一件事就是
`signature.bind(*args, **kwargs)`，于是**每一次**真站点抓取都抛
`TypeError: got an unexpected keyword argument 'proxy'`，被 `crawl.py` 自己的
`except Exception` 吞成 `content_ok=False` 的页面 —— 爬虫对任何真 URL 都抓不到东西。

而 3000+ 用例全绿。原因是离线 crawl 测试的假 server 写成

    async def web_read(self, url, **kwargs):   # ← **kwargs 把 proxy= 静静吃掉

只有 live 用例（真 `MasterFetchServer`，签名严格）才现形。所以"测试全绿"在这里
恰恰是问题本身：**假件比真件宽容，是这类静默失败最好的藏身处。**

判据（机械的）：AST 扫 `src/dhole_mcp/*.py`，凡是形如 `X.<tool>(...)` 的调用，
其关键字实参名必须 ⊆ 真 `MasterFetchServer.<tool>` 的形参名。出现 `**something`
的调用跳过 —— 那是显式转发的合法形态，静态判不了。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dhole_mcp.server import MasterFetchServer

SRC = Path(__file__).resolve().parents[1] / "src" / "dhole_mcp"


def _tool_names() -> set[str]:
    return {td["name"] for td in MasterFetchServer._TOOL_DEFS}


def _accepted(tool: str) -> tuple[set[str], bool]:
    """(形参名集合, 是否有 **kwargs)。"""
    handler = getattr(MasterFetchServer, tool, None)
    if handler is None:
        return set(), True
    params = inspect.signature(handler).parameters
    return (
        {name for name in params},
        any(p.kind is p.VAR_KEYWORD for p in params.values()),
    )


def violations_in(source: str, *, tools: set[str] | None = None,
                  filename: str = "<src>") -> list[str]:
    """返回形如 ``文件:行 <tool> 传了不存在的实参 'x'`` 的违规列表。"""
    tools = tools or _tool_names()
    accepted = {t: _accepted(t) for t in tools}
    out: list[str] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in tools:
            continue
        # `**something` 转发：静态判不了，放行
        if any(kw.arg is None for kw in node.keywords):
            continue
        params, has_var_kw = accepted[func.attr]
        if has_var_kw:
            continue
        for kw in node.keywords:
            if kw.arg is not None and kw.arg not in params:
                out.append(
                    f"{filename}:{kw.lineno} {func.attr} 传了不存在的实参 "
                    f"{kw.arg!r}（真签名没有它 —— 是不是从参数面下线了？）")
    return out


def test_no_src_call_site_passes_a_parameter_the_handler_rejects():
    bad: list[str] = []
    files = sorted(SRC.glob("*.py"))
    assert files, f"没扫到源文件：{SRC}"
    for path in files:
        bad += violations_in(path.read_text(encoding="utf-8"),
                             filename=path.name)
    assert not bad, (
        "src/ 里有调用点传了处理器不接受的实参 —— 运行时就是一个 TypeError，\n"
        "如果它落在某个 except 里，会变成「静默失败」而不是红：\n  "
        + "\n  ".join(bad))


def test_the_guard_notices_the_exact_regression_it_was_born_from():
    """反证：把 crawl.py 那行原样喂进来，必须被判红。

    守卫的有效性等于它能拦住历史。这条不依赖真源码，喂一段字符串。
    """
    snippet = (
        "async def f(server):\n"
        "    return await server.web_read(\n"
        "        url=u, extraction_type='html',\n"
        "        proxy=_crawl_proxy,\n"
        "        ignore_robots=ignore_robots,\n"
        "    )\n"
    )
    bad = violations_in(snippet, tools={"web_read"}, filename="snippet.py")
    assert len(bad) == 1, bad
    assert "'proxy'" in bad[0], bad


def test_the_guard_leaves_a_legitimate_forwarding_call_alone():
    """`**call` 转发是合法形态，不该被判红（否则守卫会逼人删掉正确的写法）。"""
    snippet = "async def f(server, call):\n    return await server.web_read(url=u, **call)\n"
    assert violations_in(snippet, tools={"web_read"}, filename="snippet.py") == []
