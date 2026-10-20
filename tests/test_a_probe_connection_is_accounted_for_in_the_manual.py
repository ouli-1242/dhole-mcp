# -*- coding: utf-8 -*-
"""预探测那根不带请求的连接，手册要替它说话（R02-085 · P3）。

现场（`review/.runtime/p1/r90_preflight_scope.json` · 自建计数靶 127.0.0.1:8405 · HEAD f303c5a）：

| 格 | 形状 | 靶子数到的 |
|----|------|-----------|
| X1/X4 | 无代理 + 私网目标 | **2 根 accept · 1 根一字节未发 · 1 条 request line** |
| X2 | 显式代理指向 8405 | 2 根 accept · 1 根空的 —— 预探测落在**代理端点**上 |
| X3 | 死端口 8404 | 0 根 accept（没到靶子），2,812ms 定论 `network_error: timeout (TCP preflight)` · `escalation_path=preflight:timeout(skipped_http+stealthy)` |

这一扇门外写着 `A write is sent ONCE: not retried`，而调用方/运维手上唯一看得见的是自家的连接
日志：一次抓取两根，其中一根什么都没有。把那个形状读成"重发"是**必然**的——本连评审就在同一处
先记错过一次（后来靠换解析级靶子判死，记在 B-42）。浏览器那一层的同类预探测早就有账
（`summary` 一句 + 手册一句 "so two lines in your own service log are accounted for rather than
looking like a retry"），HTTP 这一层两者皆无，所以这一把**只补手册那一句**：

- 为什么不进 `summary`：预探测在每一次直接抓取前都发生，给每份回执加一句是"把一件常态写成一桩
  事故"，还会撞连接面的字节预算（`CONNECT_CEILING` 那一族账）；调用方需要的是**认得出这根连接**，
  不是每次被抓提醒。
- 为什么不动探测本身：它换来的是 X3 那种 2.8s 的定论，而不是 30s 的静默。

判据三面对称：手册里有这句话（正面）· 这句话不许越过代码给的范围（"只在私网那一支有判决权"
钉源码形状）· 浏览器那一层的原句不许被顺手改掉（反向锁）。
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from dhole_mcp import server as server_mod

TOOLS = (Path(inspect.getsourcefile(server_mod)).parent / "skills" / "dhole-web"
         / "references" / "tools.md")


def _flat(text: str) -> str:
    """手册是硬换行的散文：判据按**折叠空白之后**的字面找。

    第一版照原文逐字比，两格假红 —— `two TCP connections, one of them carrying` 在文件里
    跨了一行，`summary names the probe on that call` 也一样。手册的行宽不是它说了什么的一部分。
    """
    return re.sub(r"\s+", " ", text)


@pytest.fixture(scope="module")
def tools_text() -> str:
    return _flat(TOOLS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def http_probe_para(tools_text: str) -> str:
    # 【原判据搬家 · 评审 R02-109】lead 从前是 "The HTTP tier probes too" —— 那句是这段的**作者**，
    # 而它把预探测记在 HTTP 档名下。计数靶（r155 · 127.0.0.1:8408）量到的不是这个形状：自动档
    # 两根连接（一根零字节），`force_fetcher='http'` **一根**、零字节的那根不存在。探测住在自动
    # 阶梯里，不住在档上。lead 跟着改成新句子；这一段其余的钉子一颗没动。
    lead = "The auto path probes before the tier it picks has run"
    assert lead in tools_text, "手册里那段给预探测记账的话不见了"
    tail = tools_text.split(lead, 1)[1]
    # 这一段到下一个 markdown 标题为止（段落里不许长出第二份"话"）
    return tail.split("\n## ", 1)[0]


class TestTheManualAccountsForTheProbe:

    @pytest.mark.parametrize("needle", [
        "two TCP connections, one of them carrying zero bytes",
        "neither connection is a retry",
        "the probe lands on the proxy endpoint",
        "only ever gets to decide on a private target",
        # 【原判据搬家 · 评审 R02-086】原来这一颗钉的是 "decides nothing" —— 那句说的是
        # "公网照拨，但拨完不判"。产品改了：没有判决权的拨号不再发生，所以这句话本身已经不
        # 为真。钉的新的一颗说的是同一件事的现在式（公网那一腿压根不拨），而不是把判据删掉。
        "public target is not dialed at all",
    ])
    def test_the_paragraph_says_the_measured_shape(self, http_probe_para, needle):
        assert needle in http_probe_para, f"这一段不再说出量到的形状：{needle}"

    @pytest.mark.parametrize("needle", [
        "one probe per URL",
        # 【原判据搬家 · 评审 R02-086】那颗数（8 发 = 16 根连接）本来是在**本机计数靶**上量的
        # （`r91_probe_cost.py` · 127.0.0.1:8405），也就是私网那一腿 —— 手册当时把它说成了所有
        # 直接抓取通则。产品改后它是私网那一腿的性质，句子也跟着加了 `local` 限定。数字一字未改。
        "a batch of 8 local URLs is 16 connections",
        "A cache hit pays nothing",
    ])
    def test_the_scaling_is_stated_the_way_it_was_measured(self, http_probe_para, needle):
        """成本按**颗**付还是按**发**付，是这扇门会不会撞上连接级限流的关键一问。

        量在 `r91_probe_cost.py`：`web_read urls=[8]` → 16 根 accept（8 条请求 + 8 根一字节
        未发），比值在四种形状下都是 2.0；`cache_ttl` 热了的那一发 0 根 —— 预探测排在缓存读
        之后，命中就不拨。手册若哪天把这些数改掉，这一格先红。
        """
        assert needle in http_probe_para, f"{needle}"

    def test_it_names_the_verdict_it_buys(self, http_probe_para):
        """记账要连**为什么值得付**一起说：否则下一把会把它当纯开销删掉。"""
        assert "network_error: timeout (TCP preflight)" in http_probe_para
        assert "skipped_http+stealthy" in http_probe_para
        assert "30s silence" in http_probe_para

    def test_the_forced_http_leg_is_not_promised_a_probe(self, http_probe_para):
        """【R02-109 · 本把新钉的一颗】强制 http 那一腿不付探测 —— 手册以前把探测记在档上。

        计数靶逐条登记（`r155_connection_shapes.json` · 127.0.0.1:8408 · HEAD 09c8453 · pid 12176）：
        自动档读一颗私网靶 = 2 根连接（1 根零字节 + 1 根带请求行）；同一颗靶换成
        `force_fetcher='http'` = **1 根**、零字节那根不存在；缓存命中 = 0 根。
        所以这一格要手册说出"强制 http 不付探测"，并且不许反过来把两根连接说成所有档的通则。
        """
        assert "forced `http` read pays no probe" in http_probe_para, http_probe_para[:400]
        assert "one connection, none of them carrying zero bytes" in http_probe_para, (
            "那一腿的数没跟着句子走")
        assert "every tier pays a probe" not in http_probe_para
        assert "The HTTP tier probes too" not in http_probe_para, (
            "把探测记回档名下 —— 实测那一档自己一根都不拨")

    def test_the_probe_is_not_promised_as_a_receipt_field(self, http_probe_para):
        """反向锁：这一段说清了"没有哪个字段会告诉你"，不许被改写成"summary 会告诉你"。

        浏览器那一层的原句确实这么说（`summary names the probe on that call`）—— 那是因为它
        只出现在**强制浏览器读**那一支；HTTP 这一层每一发都有预探测，写进 summary 就是把常态
        写成事故，还要撞连接面的字节预算。
        """
        assert "`summary` names the probe" not in http_probe_para, http_probe_para
        assert "no field says so" in http_probe_para


class TestTheClaimsStayInsideWhatTheCodeDoes:

    def test_the_probe_only_decides_for_private_targets(self):
        """手册说"只在私网那一支有判决权"，钉住那句的**源码形状**。

        【原判据搬家 · 评审 R02-086】从前钉的是 `definitive = (...) if _private_target else ()`
        —— 那一支"照拨，但公网不判"。R02-086 把没有读者的那一拨省了，所以这一颗要钉的东西变成
        两件事：**拨号本身**被条件化到私网，而私网那一腿的判决集合**一字未缩**。
        两句都钉，缺任何一句这一格就红 —— 这不是把判据放宽，是把它对准新的形状。
        """
        src = inspect.getsource(server_mod.MasterFetchServer)
        # 注释不算形状的一部分：判据要看的是"拨号这一行动作被那道条件包住"，而这一段里
        # 条件与动作之间隔着几句为什么。（第一版直接把原文喂给正则，被注释挡了一下。）
        code = "\n".join(line for line in src.splitlines()
                         if not line.lstrip().startswith("#"))
        assert re.search(r"if _private_target:\s*\n\s*reachable, preflight_category = "
                         r"await asyncio_to_thread\(tcp_preflight, url, 2\.0\)", code), (
            "拨号不再只发生在有判决权的那一支 —— 手册那句『only ever gets to decide on a "
            "private target - so that is the only target it dials』就不为真了")
        assert 'in ("connection_refused", "timeout")' in src, (
            "私网那一腿的判决集合缩了：省拨号不许把判决一起省掉")

    def test_the_browser_tier_sentence_still_holds(self):
        """被镜像的那句原话不许被顺手改掉（同一现象、两处账，动一处要先说清理由）。"""
        text = _flat(TOOLS.read_text(encoding="utf-8"))
        assert "dials that target **twice**" in text, text[:200]
        assert "`summary` names the probe on that call" in text
