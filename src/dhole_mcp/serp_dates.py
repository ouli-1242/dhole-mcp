# -*- coding: utf-8 -*-
"""行级日期的判决叶子：一条 SERP 行带着它的精度，能对 `before=` 说什么、不能说什麼。

独立成模块（而不是住在 `search.py`）有两个理由，都是这仓库的账：

1. **各家解析器与 `search.py` 都要读同一套语义**。精度放进 `RawResult` 之后，抓取侧
   （4.5 的核验）与检索侧（4.6 的逐行判决）会同时想问"这一行能不能拿来比日期"；两处各
   写一遍就是两个作者，而这两个作者的分工恰恰是这次要修的缺陷本身（"3 hours ago" 被
   存成光秃秃的 ISO 就是一处自作主张的第二次判决）。
2. 这一层只依赖标准库与 `envelope.parse_serp_date`，所以守卫**完全离线**：不必联网、
   不必等浏览器、也不必改任何既有夹具。

三条判据，每条都站在"不许替读者编结论"那一侧：

· **能判才判**：只有 day/month/year 三种精度、且比较在精度内不留歧义时，`before=` 才
  真的丢行 —— `2026-03` 早于 `2026-10-01` 是事实，而 `2026-10` 与 `2026-10-01` 谁先谁后
  取决于被抹掉的那一位，那种行不许丢。
· **relative 永远不算数**：`5天前` 的真值随抓取那一刻漂，逐行拒（这一行留在结果里，
  并进 `rows_unverifiable`）。整体拒绝 `before=` 已经从前的做法，现在改成"能对哪几行
  就 filter 哪几行，剩下的大声说出来"。
· **丢了多少必须说出口**：`coverage()` 交的就是这份账 —— 有几行带日期、有几行是核验过
  的（4.5）、有几行因为没日期或精度不够而**留下了**、又真丢了几行。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping

from dhole_mcp.envelope import parse_serp_date

#: 一行日期判词的三种去向。名字是给响应字段读的，不是给人猜的。
KEEP = "keep"
DROP = "drop"
UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True)
class RowDate:
    """一行自己的日期：值 + 精度 + 命中的原文。"""

    value: date | None = None
    precision: str = ""
    basis: str = ""
    verified_by_fetch: bool = False

    @property
    def present(self) -> bool:
        """Whether the date this row gives can enter the comparable ledger.
        """
        return self.value is not None

    def as_fields(self) -> dict:
        """上线的三颗字段（无日期时全空，供 wire 省略）。"""
        return {"published": self.value.isoformat() if self.value else "",
                "published_precision": self.precision,
                "published_basis": self.basis}


def read_row(text: str, now: date | None = None) -> RowDate:
    """把版面上一段日期文本读成 `RowDate`；读不出日期就交回 `NO_DATE` 形状。"""
    value, precision, matched = parse_serp_date(text or "", now=now)
    if value is None:
        return RowDate()
    return RowDate(value=value, precision=precision, basis=matched)


def before_verdict(row: RowDate, cutoff: date) -> str:
    """这一行在 `before=<cutoff>` 下的去向。

    `before=` 是一轮的**上界**：与 `after=` 那一下界成对，也是所有搜索 API 里 `before:`
    的意思 —— 要的是"发表早于这一天"的那几行。所以被丢掉的永远是**证明比这天更晚**的。
    （这一颗曾经反着判：`value <= cutoff` 就丢，于是它丢掉的正是调用方要的那批，而回包
    字面上仍然像"过滤过了"。名字与算术相反比没过滤更糟 —— 调用方无从怀疑一个看起来执行了
    的上界。方向由 `tests/test_before_filters_the_rows_it_can_and_says_the_rest.py` 钉住。）

    丢掉一行是**不可逆**的动作（读者不会在别处看到它），所以只有"精度内不留歧义"才丢：
      · day 精度：逐日比，早于 cutoff 才留（`before` 是严格早于，落在当天不算）。
      · month/year 精度：整月/整年的**最后一天**都早于 cutoff 才算证明早；**第一天**就落在
        cutoff 当天或之后才算证明晚；cutoff 落在那个月/年之内时两边都证明不了 —— 留下并
        计入 `unverifiable`。
      · relative 精度：不参与比较。它的真值来自抓取那一刻，`before=` 是一次事后的
        集合运算，两者不该互相冒充。
    """
    if not row.present or not cutoff:
        return KEEP
    if row.precision == "relative":
        return UNVERIFIABLE
    if row.precision == "day":
        return KEEP if row.value < cutoff else DROP
    if row.precision == "month":
        # 整月的最后一天（=下月一日）都不晚于 cutoff，整月才都在窗口之内。
        if _month_end(row.value) <= cutoff:
            return KEEP
        return DROP if row.value >= cutoff else UNVERIFIABLE
    if row.precision == "year":
        if _year_end(row.value) <= cutoff:
            return KEEP
        return DROP if row.value >= cutoff else UNVERIFIABLE
    return UNVERIFIABLE


def _month_end(d: date) -> date:
    return date(d.year + (d.month == 12), 1 if d.month == 12 else d.month + 1, 1)


def _year_end(d: date) -> date:
    return date(d.year + 1, 1, 1)


def partition_for_before(rows: Iterable[RowDate], cutoff: date) -> tuple[list, dict]:
    """按 `before=` 分行，并当场结清这份账（丢了几行、留下几行、各是因为什么）。

    返回 `(kept_rows, ledger)`。`ledger` 的键就是 4.3 那颗 `date_coverage` 的输入，所以
    这里少算一项，响应上就少一项 —— 判决与收据必须是同一次遍历的产物，不然两次遍历会
    在并发/分页改动之后各说一份数。
    """
    kept: list[RowDate] = []
    dropped = unverifiable = with_date = no_date = relative = verified = 0
    filtering = bool(cutoff)
    for row in rows:
        if not row.present:
            no_date += 1
            kept.append(row)
            continue
        with_date += 1
        if row.verified_by_fetch:
            verified += 1
        verdict = before_verdict(row, cutoff)
        if verdict == DROP:
            dropped += 1
            continue
        kept.append(row)
        if row.precision == "relative":
            relative += 1
        # `rows_unverifiable_by_before` 说的是"这一行没能参与比较"，所以没在 filter 的
        # 那一轮（`coverage()`）它必须是 0 —— 报成非零就是替调用方编了一次不存在的判决。
        if filtering and verdict == UNVERIFIABLE:
            unverifiable += 1
    ledger = {"rows_total": no_date + with_date, "rows_with_date": with_date,
              "rows_no_date": no_date, "rows_verified_by_fetch": verified,
              "rows_relative_precision": relative,
              "rows_unverifiable_by_before": unverifiable,
              "rows_dropped_by_before": dropped, "rows_kept": len(kept)}
    return kept, ledger


def coverage(rows: Iterable[RowDate]) -> dict:
    """不 filter 时的 date_coverage：只报数，不动任何一行。"""
    _kept, ledger = partition_for_before(rows, cutoff=None)
    return ledger


def page_stated_date(metadata) -> str:
    """这一页**自己**写的发表日期（源站的说法，不是任何引擎的说法）。

    只读 `published_time` 那一颗，不碰 `creation_date` / `modified_time`：协作站的建页日期
    与"这页什么时候写的"是两件事（`envelope.compute_freshness` 为同一个理由把 wiki 的
    age 报成 null），而"最后改过"更不是"发表"。解析仍走 `parse_serp_date` —— 日期解析
    的作者只有一位，页面自述与 SERP 版面共用它。写法也共用：整串 ISO，页面只写到月/年时落
    1 号 / Jan 1，那是**帽**不是声明（与 `published` 同一套约定）—— 两颗字段各写各的精度
    口径，读者就得先猜它们是不是同一把尺。
    读不出就交回空串：空串在这一维上是"没有源站的日期可核验"，既不是"这页没日期"，
    也不是"页面更旧"，所以 `before=` 永不据它丢行。
    """
    text = (metadata or {}).get("published_time")
    if not isinstance(text, str) or not text.strip():
        return ""
    rd = read_row(text)
    return rd.value.isoformat() if rd.value else ""


def recount_verified(rows: Iterable[RowDate], ledger: Mapping[str, int]) -> dict:
    """抓回来的页面把源站自己的日期写回行上之后，这一轮的账重算一次。

    为什么要有这颗：`date_coverage` 在建响应模型时算，而自动取正文在那之后（扇出住在
    server 那一侧，它才有 `web_read`）。不重算的话 `rows_verified_by_fetch` 会永远报 0
    —— 一颗线上取不到的取值比少一颗更糟，读手册的人会以为判过了。
    · 账本已经存在（问过 `before=`）：只有核验这一格跟着动，其余格是已经发生过的判决。
    · 账本是空的（没人问过日期）：现在有东西可说了，整份 `coverage()` 一次算出来。
    """
    rows = list(rows)
    if not ledger:
        return coverage(rows)
    updated = dict(ledger)
    updated["rows_verified_by_fetch"] = sum(1 for r in rows if r.verified_by_fetch)
    return updated


def describe_ledger(ledger: Mapping[str, int], cutoff_iso: str) -> str:
    """把这份账说成一句人话（`summary` / `next_action` 读它）。

    必须**先说丢了几行**：调用方最该知道的是有一批行不在了，而不是共有几行带日期。
    """
    d = int(ledger.get("rows_dropped_by_before", 0))
    u = int(ledger.get("rows_unverifiable_by_before", 0))
    n = int(ledger.get("rows_no_date", 0))
    bits = [f"{ledger.get('rows_total', 0)} rows: "
            f"{ledger.get('rows_with_date', 0)} carry a publish date"]
    if d:
        bits.append(f"{d} dropped as newer than before={cutoff_iso}")
    if u:
        bits.append(f"{u} kept because their date is too coarse or relative to compare")
    if n:
        bits.append(f"{n} kept because they carry no date at all")
    return "; ".join(bits)
