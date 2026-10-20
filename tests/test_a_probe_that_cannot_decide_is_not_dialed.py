# -*- coding: utf-8 -*-
"""公网那一拨 TCP 预探测买不到任何东西，就别拨（评审 R02-086 · P3）。

代码侧普查（结论已落进台账 `4625af9`）：`preflight_category` 全仓只有两处读者 ——
`server.py:8277`（强制档，`definitive = ("connection_refused",)`，公网 refused 在那里**有**判决权）
与 `server.py:8609`（自动档，`definitive = (...) if _private_target else ()`）。
空集合里任何值都不可能在里面，所以自动档对公网拨完之后把答案丢在地上。

现场（本机 9202 · pid 3780 · HEAD 66f56ad · 存档 `sessions/P02-R39-escalation-location.jsonl`
· 读数 `r142_preflight_public_census.json` / `r143_escalation_location.json`）：

| 格 | 形状 | 量到的 |
|----|------|--------|
| P1/P2/P3 | 三颗公网活靶的裸拨号 | 全部 `(True, '')`，自付 **383 / 50 / 44 ms**（外加一根零字节的连接） |
| P-live | 公网活靶 `cache_ttl=0` 的真读 | 200 · `escalation_path="direct:http"` · 整份回执里 `preflight` 出现 **0 次** —— 这一拨事后谁也问不到 |
| X-dead | 私网死端口（线上开了 allow_private） | 2,018ms 定论 `network_error: timeout (TCP preflight)` · `escalation_path="preflight:timeout(skipped_http+stealthy)"` |
| X-hit | 公网命中缓存 | 10ms · 不拨（手册原话：A cache hit pays nothing） |

判据（本文件钉住）：
  * 自动档对**公网**目标不再做 TCP 预探测 —— 没有判决权的拨号不该由调用方付。
  * 私网那一支照旧**据判决定论**：拿到 refused 就得走 `preflight:…(skipped_http+stealthy)`。
  * 强制档那一支的判决集合**不缩**：公网 `connection_refused` 在那一支是有读者的定论，
    按模块源码的形状钉住，防止有人把两条路一起"优化"掉。
  * 手册为这根连接写的账（`tools.md` 里 "The auto path probes before the tier it picks has run"
    整段，原名 "The HTTP tier probes too" · R02-109 改名）必须与代码同真 ——
    F78 立下的记账契约，省掉公网那一拨之后原句不再为真，同批改写而不是留着说谎。

【三处量具自错，随档记下不删】
1. 第一趟 `public-read` 读到的是缓存命中（10ms），它回答不了"公网拨号留不留痕迹"，
   按 `cache_ttl=0` 重测才是答案。
2. `escalation_path` 不在 `metadata` 里而在回执**顶层**。
3. 判据第一版的 spy 装成 async 函数、还直接答"活着"：两个调用点都是把 `tcp_preflight`
   交给线程跑（`asyncio_to_thread(tcp_preflight, …)`），async 替身从没被 await ⇒ 一次也记不下来
   （"数不到拨号"会被读成"没有拨号"，那是反向的假绿）；而"活着"这个假答案把私网那一支的判决
   改掉了。现在是**同步、记数、可控判决**的替身。
4. 私网那一格本来由现场死端口跑（`allow_private` 开→ 线上走到 `preflight:timeout`，
   测试进程里 `tcp_preflight` 对同一颗端口 0ms 返回 `private_target_blocked` 而它不在
   `definitive` 里）。两种走法的差在"预探测看没看见那道豁免"，那是另一条账（**R02-107**）；
   这一把只钉接线：判决给成 refused，那一支就必须据此定论。
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from dhole_mcp import server as server_mod

TOOLS = (Path(inspect.getsourcefile(server_mod)).parent / "skills" / "dhole-web"
         / "references" / "tools.md")
PUBLIC_URL = "https://www.iana.org/help/example-domains"
DEAD_PRIVATE = "http://127.0.0.1:1/"
ALL_KEYS = ["auth", "cookies", "proxy", "profile", "cdp", "allow_private", "anti_detect"]


class _Spy:
    """同步、记数、可控判决的替身：默认原样委托给真探测，`verdicts` 里的那颗地址由判据说话。"""

    def __init__(self, real):
        self.calls = []
        self.verdicts = {}
        self._real = real

    def __call__(self, url, timeout=2.0):
        self.calls.append({"url": url, "timeout": timeout})
        if url in self.verdicts:
            return self.verdicts[url]
        return self._real(url, timeout)


@pytest.fixture
def dial_spy(monkeypatch):
    import dhole_mcp.fetcher as fetcher_mod
    spy = _Spy(fetcher_mod.tcp_preflight)
    monkeypatch.setattr(fetcher_mod, "tcp_preflight", spy, raising=True)
    return spy


@pytest.mark.asyncio
async def test_a_public_target_is_no_longer_dialed_by_the_auto_path(dial_spy):
    """这一格的真值条件必须与"这台机器此刻有没有网"无关。

    判的是**拨没拨**，所以断言只挂在 spy 上。两件事是让这一格稳定的：
    - `force_fetcher='http'`：这一格量的是**自动档在 HTTP 层之前**那一处预探测。不加它，一发
      在本机此刻失败的公网读会升级到浏览器档，而浏览器档那一支（`server.py:8269`）对公网
      refused **是有判决权的**（强制档，本刀明确不动），于是 spy 会记到一次**合法的**拨号，
      判据就红了 —— 那种红测的不是这一把的东西（第一、二版的飘红就是这么来的）。
    - 不断言"读得动"："公网那一发确实读得动、且回执里没有预探测的痕迹"由现场钉
      （`r143` 的 P-live：200 · `escalation_path="direct:http"` · 整份回执里 `preflight` 0 次）。
    """
    s = server_mod.MasterFetchServer()
    await s.web_read(url=PUBLIC_URL, cache_ttl=0, force_fetcher="http")
    public_dials = [c for c in dial_spy.calls if c["url"] == PUBLIC_URL]
    assert not public_dials, f"公网那一拨还在拨（无判决权却要调用方付）：{public_dials}"


@pytest.mark.asyncio
async def test_the_private_branch_still_settles_on_a_refused(dial_spy, monkeypatch):
    """私网 + refused ⇒ 据此定论，走 `preflight:…`，不去烧两档预算。"""
    dial_spy.verdicts[DEAD_PRIVATE] = (False, "connection_refused")
    s = server_mod.MasterFetchServer()
    monkeypatch.setattr(s, "_target_is_private", lambda url: True)
    cred = await s.cred_defaults(set={"allow_private": ["127.0.0.1"]})
    assert not cred.error, cred.error
    try:
        out = await s.web_read(url=DEAD_PRIVATE, cache_ttl=0)
    finally:
        await s.cred_defaults(unset=ALL_KEYS)
    assert [c for c in dial_spy.calls if c["url"] == DEAD_PRIVATE], (
        "私网那一支的拨号被一起省掉了（这一格在不开豁免时压根走不到预探测 —— 地址门先拦回来）")
    assert out.status == 0
    assert "TCP preflight" in (out.error or ""), out.error
    assert "preflight:" in (out.escalation_path or ""), out.escalation_path


@pytest.mark.asyncio
async def test_a_public_refused_still_settles_on_the_forced_browser_branch(dial_spy):
    """强制档（`force_fetcher='stealthy'`）那一支公网 refused **有**判决权 —— 本刀不动它。"""
    dial_spy.verdicts[PUBLIC_URL] = (False, "connection_refused")
    s = server_mod.MasterFetchServer()
    out = await s.web_read(url=PUBLIC_URL, cache_ttl=0, force_fetcher="stealthy")
    assert [c for c in dial_spy.calls if c["url"] == PUBLIC_URL], (
        "强制档对公网的预探测被这一把一起省掉了 —— 那一支的 refused 是有读者的定论")
    assert "preflight:" in (out.escalation_path or "") or "connection_refused" in (out.error or ""), (
        out.escalation_path, out.error)


def test_the_auto_branch_no_longer_dials_public():
    """源码形状：自动档那一处现在必须把拨号条件化，而不是拨完再丢掉答案。"""
    src = inspect.getsource(server_mod.MasterFetchServer)
    assert 'definitive = ("connection_refused",)' in src, (
        "强制档那一句判决集合变了")


class TestTheManualStillTellsTheTruth:
    def test_the_probe_accounting_paragraph_does_not_claim_a_dial_per_url(self):
        text = re.sub(r"\s+", " ", TOOLS.read_text(encoding="utf-8"))
        # 【定位子搬家 · 评审 R02-109】lead 只是这段的**位置**，不是它的断言。手册那段原来叫
        # "The HTTP tier probes too"（把探测记在档名下），r155 的计数靶量到强制 http 那一腿一根
        # 都不拨，于是句子改成自动档的说法。两颗真正的钉子（不许说"每发都拨" · 要说清在哪儿还拨）
        # 一字未动 —— 改的只是从哪儿开始读。
        lead = "The auto path probes before the tier it picks has run"
        assert lead in text, "记账那一段不见了 —— 这一族的话只能有一位作者改，不能整段蒸发"
        para = re.sub(r"\s+", " ", text.split(lead, 1)[1].split("# ")[0])
        assert "every direct fetch starts by dialing" not in para, (
            "手册还在说每发都拨，而代码已经不拨公网了")
        assert re.search(r"private|loopback", para, re.I), "改写后的那句要说清在哪儿还拨"

    def test_the_browser_layer_sentence_is_untouched(self):
        text = re.sub(r"\s+", " ", TOOLS.read_text(encoding="utf-8"))
        assert "so two lines in your own service log are accounted for rather " \
               "than looking like a retry" in text
