"""修复复验：直接驱动真实代码路径，确认修好在线上行为上确实发生了。

与 `evals/check_image_blocking.py` 的分工：那个证明**缺陷的因果**（A/B 单变量），
这个证明**修复的结果**（走完整 web_read：真浏览器真动作链真截图真解码，或真字节
真抽取真判定）。

每一条都断言**线路上的响应模型字段**（不是内部函数返回值），detail 里带上可观察的证据；
新落的几条还带上修复前的实测值 —— 没有前值就分不清"修好了"和"本来就这样"。需要靶站
先起来：`python evals/fixture_site.py`。第 12/13 项还要联网问引擎，而池子会被验证码
左右：那两项的 detail **自己说是哪一种**（今晚活的那一发 sogou/baidu 全被挡，13 项因此
把方向改在冻存的 SERP 字节上过真 parser —— 换了一条能重跑的路，而不是假装量到了）。
Windows 下请用 `python -X utf8` 跑：控制台代码页会把中文断言烧成看不清的乱码，而看不清
的通过记录等于没有记录。

    python evals/verify_fixes.py
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNTIME = HERE / ".runtime"
BASE = "http://127.0.0.1:8399"

# 靶站在回环上，而 web_read 的私有主机门只有 cred_defaults 一个入口：`allow_private`
# 在 17.0 从参数面退役，调用内作用域又会被每次调用重推的存值覆盖（那是 D1 记着的形状
# 问题，Round 3 才修）。既然要写存值，就写进一次性 DHOLE_HOME（照 ranking_baseline 的
# 做法）—— 复验脚本不该碰用户机器上的凭据库，更不该跑完留下一个撑开的栅栏。
os.environ["DHOLE_HOME"] = tempfile.mkdtemp(prefix="dhole_verifyfixes_")
# 截图动作在验证阶段就被多模态门控拦下（actions.py），复验要显式开旗。
os.environ.setdefault("DHOLE_MULTIMODAL", "1")

from dhole_mcp import identity  # noqa: E402
from dhole_mcp.server import MasterFetchServer  # noqa: E402

PASS, FAIL = "通过", "**未通过**"
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"[{PASS if ok else FAIL}] {name}\n    {detail}")


async def check_screenshot_carries_pixels() -> None:
    """D1：动作链里的元素截图要拿到真像素，decode_qr 要解出载荷。"""
    server = MasterFetchServer()
    # 这一支曾把 `allow_private=True` 当参数传 —— 那颗参数早已不在 web_read 的参数面上，
    # 于是 TypeError 被 except 记成「未通过」：脚本坏掉的样子和它要证的缺陷一模一样
    # （说的和做的对不上，且没有任何一处报错）。门现在由 main() 一次性开在 DHOLE_HOME 里。
    res = await server.web_read(
        url=f"{BASE}/qr",
        actions=[{"screenshot": {"selector": "#qr", "decode_qr": True}}],
    )
    rows = (res.metadata or {}).get("actions") or []
    shot = next((r for r in rows if r.get("action", "").startswith("screenshot")), {})
    qr = shot.get("qr") or []
    images = getattr(res, "_action_images", None) or []
    ok = (qr == ["https://example.com/checkin?token=DHOLE-EVAL-8399"]
          and bool(images))
    record("D1 元素截图拿到像素并解出二维码", ok,
           f"qr={qr!r} 内联图片={len(images)} 张 action={shot.get('action')!r}")


async def check_submit_label() -> None:
    """D2：索引寻址的 submit 要在响应与审计里写明目标。"""
    log = RUNTIME / "audit_verify.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    if log.exists():
        log.unlink()
    os.environ["DHOLE_USAGE_LOG"] = str(log)

    server = MasterFetchServer()
    res = await server.web_read(
        url=f"{BASE}/checkout",
        actions=[{"submit": {"index": 2}}],
    )
    rows = (res.metadata or {}).get("actions") or []
    label = rows[0].get("action", "") if rows else ""
    pending = bool(rows and rows[0].get("pending_approval"))

    writes = []
    if log.exists():
        writes = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()
                  if ln.strip()]
    audit_action = writes[0].get("action", "") if writes else ""

    ok = ("[2]" in label and "None" not in label
          and pending and writes and "[2]" in audit_action)
    record("D2 索引寻址 submit 的标签与审计写明目标", ok,
           f"响应标签={label!r} pending={pending} 审计 action={audit_action!r}")
    os.environ.pop("DHOLE_USAGE_LOG", None)


async def check_obfuscated_shell_is_not_a_success() -> None:
    """R1：正文穿过**每一道**长度闸的混淆壳页，结论位要跟着形状翻。

    前值（2026-10-09 北极星那家实测）：`status: 200`、`content_ok: true`、抽取 3,559
    个字符 —— 字全抽到了，而那些字里没有一句人话。`_is_js_shell` 量的是"大 body +
    几乎没字"，这一枚恰恰是从每一道长度闸底下钻过去的。靶站的 `/shell/obfuscated`
    就是照着那一发造的（确定性、无状态）。
    """
    server = MasterFetchServer()
    res = await server.web_read(url=f"{BASE}/shell/obfuscated",
                                force_fetcher="http")
    finding = (res.metadata or {}).get("obfuscated_body") or {}
    ok = (res.status == 200
          and res.content_ok is False
          and res.error.startswith("obfuscated_payload")
          and res.page_type == "js_shell"
          and (res.diagnosis or {}).get("code") == "js_shell"
          and res.total_extracted_chars > 2_000
          and bool(finding.get("signals")))
    record("R1 混淆壳页不再被报成成功", ok,
           f"status={res.status} 抽取={res.total_extracted_chars} 字 "
           f"content_ok={res.content_ok} page_type={res.page_type!r} "
           f"code={(res.diagnosis or {}).get('code')!r} 证据={finding.get('signals')} "
           f"error={res.error[:70]!r}（前值：content_ok=true、批量把它计入 successful）")


async def check_legit_pages_survive_the_flip() -> None:
    """R1 的反向：翻闸之后，靶站每一页合法页面逐份还是成功页。

    measure 那一刀的假阳性清单是离线语料量的；这一条走的是真 HTTP、真抽取、真结论位，
    它红了就说明闸开过头 —— 那正是这批唯一还没被 pytest 覆盖到的可能性（本地挂具能造
    出成功页的形状，造不出真站点的字节）。
    """
    server = MasterFetchServer()
    judged_shell = []
    for path in ("/", "/about", "/article", "/collections", "/work/1"):
        res = await server.web_read(url=f"{BASE}{path}", force_fetcher="http")
        if "obfuscated_payload" in (res.error or ""):
            judged_shell.append((path, res.total_extracted_chars))
    record("R1 合法页面没有被翻闸误伤", not judged_shell,
           f"被判成壳的合法页面={judged_shell}（前值：无）")


async def check_a_filled_input_reads_back_the_live_value() -> None:
    """期 1.1：一次成功的 `fill` 必须能在同一条链里被读回来，读的是页面上活的值。

    前值（2026-10-10 真人体测，场景 12）：`fill '#qty' text:"3"` 报 ok:true，而
    `read {attribute:"value"}` 回 **"1"** —— 那是标记里的死 attribute，不是页面上的东西。
    于是我在一扇不可逆的订单表单前多烧 4 次调用约 44 秒去确认"到底填没填上"，而服务端
    日志里从来是对的 3。这一条把两端都收住：两读都回 3（property 那一读直说来源，
    attribute 那一读把标签层作为 `attribute_says` 带回去），而**提交之后**日志里的 qty
    就是 3 —— 页面上活的值与站点收到的值是同一件事。
    """
    orders = RUNTIME / "orders.log"
    before = len(orders.read_text(encoding="utf-8").splitlines()) if orders.exists() else 0

    server = MasterFetchServer()
    res = await server.web_act(url=f"{BASE}/checkout", timeout=60_000, actions=[
        {"fill": {"selector": "#qty", "text": "3"}},
        {"read": {"selector": "#qty", "property": "value"}},
        {"read": {"selector": "#qty", "attribute": "value"}},
        {"submit": {"selector": "#place-order", "confirm": True}},
    ])
    rows = (res.metadata or {}).get("actions") or []
    prop = next((r for r in rows if "(property)" in r.get("action", "")), {})
    attr = next((r for r in rows if r.get("action", "").endswith("@value")), {})
    after = orders.read_text(encoding="utf-8").splitlines() if orders.exists() else []
    written = ""
    if len(after) > before:
        written = json.loads(after[-1]).get("qty", "")

    ok = (prop.get("read") == "3" and attr.get("read") == "3"
          and str(prop.get("read_via", "")).startswith("property:value")
          and "asked as attribute" in str(attr.get("read_via", ""))
          and attr.get("attribute_says") == "1"
          and written == "3")
    record("1.1 填进去的值读得回来，且与站点收到的那个一致", ok,
           f"property 读={prop.get('read')!r} via={prop.get('read_via')!r} / "
           f"attribute 读={attr.get('read')!r} via={attr.get('read_via')!r} "
           f"标签层={attr.get('attribute_says')!r} / orders.log qty={written!r} "
           f"（前值：两读都回 \"1\"，而日志里是 3）")


async def check_collapse_advice_offers_only_a_door_that_changes_the_reader() -> None:
    """期 1.4：塌陷页的建议只许点名**换一条抽取管路**的那几枚，并且那枚真换得动。

    前值（2026-10-10 体测，场景 3）：同一句建议并列劝 `extraction_type='html'` 与
    `'text'`，而 `'text'` 与默认的 markdown 过的是同一个读取器 —— 照它试的那一发回
    527/1,998 字、同一条判决、同两句建议。这一条两头都量：话里不再出现共用管路的那枚
    （散文、`next_action`、`diagnosis.recover_with` 三处同一颗常量），而照着 `'html'`
    做的那一发确实把字带回来了。
    """
    from dhole_mcp.quality import _COLLAPSE_RECOVERY

    server = MasterFetchServer()
    md = await server.web_read(url=f"{BASE}/longaside", force_fetcher="http")
    html = await server.web_read(url=f"{BASE}/longaside", force_fetcher="http",
                                 extraction_type="html")
    diag = md.diagnosis or {}
    spoken = f"{md.error} {md.next_action} {diag.get('recover_with', '')}"
    one_author = diag.get("recover_with") == _COLLAPSE_RECOVERY
    # 带引号的 `'text'` 是"把这颗传进来"的语法；常量提到那一族时用无引号的
    # markdown / text / article 讲道理，所以这里只查带引号的那一枚。
    offers_text_value = "'text'" in spoken
    ok = (md.error.startswith("extraction_collapsed")
          and "extraction_type='html'" in (md.next_action or "")
          and not offers_text_value
          and one_author
          and html.content_ok and md.total_extracted_chars > 0
          and html.total_extracted_chars > md.total_extracted_chars)
    record("1.4 塌陷页只被劝去真换管路的那一枚（且那一枚真带回字）", ok,
           f"markdown 抽出={md.total_extracted_chars} 字 → html 抽出="
           f"{html.total_extracted_chars} 字（content_ok={html.content_ok}）；"
           f"三处同一颗常量={one_author}；建议里还出现 'text'={offers_text_value}")


def _dispatch_text(result) -> str:
    """把 `_dispatch` 的返回摊成一段可比对的文本（两形：content_list 或 (list, dict)）。"""
    parts = list(result) if isinstance(result, tuple) else [result]
    return json.dumps(parts, ensure_ascii=False, default=str)


async def check_a_write_refusal_answers_with_a_door_the_write_can_walk() -> None:
    """期 1.5：拒 must 具名，而名字必须是这一扇门上传得进去的那颗参数。

    前值（2026-10-10 体测，场景 9）：一次 `web_request(headers={'User-Agent': …})` 被
    拒之后，`next_action` 教人传 `anti_detect='off'` —— 那一颗在 `web_request` 的参数面
    上根本没有，照着做的那一发 0 秒弹回 `Unsupported argument(s)`。走真 `_dispatch`
    （不是直接问生成器）是为了量到那条**接线**：名字从协议层流到那句话。
    """
    server = MasterFetchServer()
    ua = _dispatch_text(await server._dispatch("web_request", {
        "url": f"{BASE}/checkout", "method": "POST", "body": "qty=9", "confirm": True,
        "headers": {"User-Agent": "curl/8.6"}}))
    validator = _dispatch_text(await server._dispatch("web_request", {
        "url": f"{BASE}/checkout", "method": "POST", "body": "qty=9", "confirm": True,
        "headers": {"If-None-Match": "\"deadbeef\""}}))
    read = _dispatch_text(await server._dispatch("web_read", {
        "url": f"{BASE}/article", "headers": {"User-Agent": "curl/8.6"}}))
    rung_offered = "anti_detect='off'" in ua
    points_at_store = "drop the user-agent key" in ua and "cred_defaults" in ua
    points_at_reader = "web_read" in validator and "is that one door" not in validator
    read_keeps_it = "anti_detect='off' stops dhole" in read
    ok = (not rung_offered) and points_at_store and "Nothing was sent." in ua \
        and points_at_reader and read_keeps_it
    record("1.5 写请求的拒答只点它收得下的那颗参数（读的那一支不减弱）", ok,
           f"写：建议里还出现 anti_detect='off'={rung_offered}，指出删头/存档="
           f"{points_at_store}；校验子那一支指向拿着它的门={points_at_reader}；"
           f"读的那一支原句完整={read_keeps_it}")


async def check_an_undersized_screenshot_budget_refuses_before_the_browser_starts() -> None:
    """期 2.5：装不下截图的那笔预算要在**开始之前**说出来，而不是把它吃掉。

    前值（2026-10-10 体测，场景 10）：一笔不够的预算 + 一条带 `screenshot` 的链，浏览器
    照样起来、整页资源照样加载，61,529ms 之后交回 0 字节加一句关于站点的话。

    独立旁证用靶站自己的 `requests.log`：真的一个请求都没发，才算"事前"而不是"事中"。
    反面（默认预算那一笔截图仍然做得成）由 D1 那一条守着 —— 它跑的就是不点名 timeout
    的路径，误伤会在它那一条上红。
    """
    from dhole_mcp.params import ArgumentRefusal
    from dhole_mcp.server import _min_visual_call_ms

    log = RUNTIME / "requests.log"
    before = len(log.read_text(encoding="utf-8").splitlines()) if log.exists() else 0
    floor = _min_visual_call_ms()
    server = MasterFetchServer()
    started = time.perf_counter()
    # 这一支走 `_dispatch`，所以拿到的是**抛出来**的那一种（`ArgumentRefusal` 是 ValueError
    # 的子类，折成信封发生在 `call_tool` —— 那一半由
    # tests/test_a_designed_refusal_never_leaves_naked_through_the_transport.py 与
    # tests/test_a_budget_too_small_for_a_screenshot… 在真 SDK 回路上守着）。这里要量的是
    # 别的东西：那句话写完了没有，以及**页面真的没被碰过**。
    refused, advice = "", ""
    try:
        await server._dispatch("web_act", {
            "url": f"{BASE}/checkout",
            "actions": [{"screenshot": {"selector": "#qty"}}],
            "timeout": 3_000})
    except ArgumentRefusal as e:
        refused = str(e)
        advice = getattr(e, "next_action", "")
    spent_ms = (time.perf_counter() - started) * 1000
    after_lines = (log.read_text(encoding="utf-8").splitlines()
                   if log.exists() else [])
    new_gets = [ln for ln in after_lines[before:] if "/checkout" in ln]

    names_the_door = "timeout=" in advice and str(floor // 1000) in refused
    says_nothing = "Nothing was fetched and no browser was started" in advice
    ok = (refused and names_the_door and says_nothing and spent_ms < 2_000
          and not new_gets)
    record("2.5 装不下截图的预算在起浏览器之前点名拒绝", bool(ok),
           f"{spent_ms:.0f}ms 回话；下限={floor}ms；话里指出 timeout=与那个数="
           f"{names_the_door}；说明什么都没发生={says_nothing}；靶站新收到的 /checkout "
           f"请求={len(new_gets)} 条（应为 0）；句子={refused[:90]!r}")


async def check_a_focused_total_says_which_stream_it_counted() -> None:
    """期 3.1：随 focus 词变的那个总数，要在响应里说出它是被过滤过的那一路。

    前值（2026-10-10 体测）：一个 URL 的 `total_extracted_chars` 交出过六个值（30994 /
    12819 / 19843 / 45312 / 9829 / 2396），而分页算的从来是 focus 过滤**之后**那一路 ——
    这句话只在"删掉了结构化块"时才出口，于是 `[No more content.]` 骗过两次（换词之后第 3
    页可以是最后一页，也可以是中间一页，两种修法相反）。

    这里断的是线上一发真读取的字段，不是 `_apply_chunking` 的孤算：同一页、两个 focus 词，
    两发都必须认领 `focused`，而**未过滤那一发的三颗字段要整个缺席**（不带的调用一个字节
    都不该多付）。
    """
    from dhole_mcp import wire

    server = MasterFetchServer()
    # 选 `/article` 而不是 `/longaside`：后者的正文只抽出 138 字（1.4 那条量的就是它），
    # focus 在上面**一个字都切不动**，于是三颗字段按设计缺席 —— 那是一条真话，但不是这一条
    # 判据要的对照。`/article` 有 387 字、段落各讲一件事，换 focus 词才真的换一路。
    url = f"{BASE}/article"
    plain = await server.web_read(url=url)
    a = await server.web_read(url=url, focus="缓存")
    b = await server.web_read(url=url, focus="卡片")

    plain_dump = wire._wire_dump(plain)
    absent = all(k not in plain_dump for k in
                 ("pagination_stream", "page_total_chars", "pagination_basis"))
    named = (a.pagination_stream == "focused" and b.pagination_stream == "focused")
    same_page = (a.page_total_chars == b.page_total_chars
                 and a.page_total_chars > a.total_extracted_chars)
    moved = a.total_extracted_chars != b.total_extracted_chars
    ok = absent and named and same_page and moved
    record("3.1 随 focus 变的总数自己说清是哪一路（不带 focus 时不占字节）", ok,
           f"不带：三颗字段缺席={absent}；两个 focus 词各交 {a.total_extracted_chars} / "
           f"{b.total_extracted_chars} 字（数在动={moved}），都认领 focused={named}，"
           f"而同一页未过滤长度都是 {a.page_total_chars}={same_page}")


async def check_the_block_ledger_arrives_on_the_wire() -> None:
    """期 3.6：切得更细之后，"这一页被分成几块、留下几块"要跟着响应上线。

    3.5 之后一页分得出的块比空行规则给的多出一个量级，而正文头部那句
    "showing 3 of 17 blocks" 住在**正文**里 —— 从第 2 片起就被分页切走。这两颗数把同一件
    事写在信封上：读者不必翻回头数，也不必猜"少掉的是正文还是版面"。

    线上断三件事：换 focus 词，`focus_blocks_total` 不动（那是**这一页**的形状），而
    `focus_blocks_kept` 在动（那是这一次的选择）；不带 focus 的一发两颗都缺席；而头部被
    分页切走的那一片，信封上仍然带着这两颗数。
    """
    from dhole_mcp import wire

    server = MasterFetchServer()
    url = f"{BASE}/article"
    plain = await server.web_read(url=url)
    a = await server.web_read(url=url, focus="缓存")
    b = await server.web_read(url=url, focus="卡片")
    page2 = await server.web_read(url=url, focus="卡片", max_content_chars=120,
                                  offset=120)

    absent = all(k not in wire._wire_dump(plain)
                 for k in ("focus_blocks_total", "focus_blocks_kept"))
    same_shape = a.focus_blocks_total == b.focus_blocks_total > 1
    choosing = a.focus_blocks_kept != b.focus_blocks_kept
    inside = all(0 < r.focus_blocks_kept <= r.focus_blocks_total for r in (a, b, page2))
    header_gone = "[Focus:" not in "".join(page2.content)
    rides_on = page2.focus_blocks_total == a.focus_blocks_total
    ok = absent and same_shape and choosing and inside and header_gone and rides_on
    record("3.6 块数写在信封上，翻页也还在", bool(ok),
           f"不带 focus：两颗字段缺席={absent}；同一页两个 focus 词的 total 都是 "
           f"{a.focus_blocks_total} 块（不动={same_shape}），kept 各交 {a.focus_blocks_kept} / "
           f"{b.focus_blocks_kept}（在动={choosing}）；第 2 片头部已不在正文里={header_gone}，"
           f"而 total 仍报 {page2.focus_blocks_total}={rides_on}")


async def check_a_thin_body_names_its_own_kind_of_thin() -> None:
    """期 3.3 / 3.4：薄正文要说清它为什么薄，而真目录与真短文不许被翻闸。

    前值（同一发线上读取，2026-10-10）：`/nav` 交出 39 个纯菜单字（首页/关于/登录…）而
    `content_ok=true`，`/longaside` 抽出 138 字、页面自写 7,149 字却**没有对照数**（那颗
    数从前只在抽得薄的一支算）。这一条断的是四张页各自的判决与它们背后的数：

      · `/nav` → `nav_only`、`content_ok=false`、结论行带着"no prose"，出路只点真换管路
        的两枚（渲染，或跟进链接）；
      · `/longaside` → `collapsed`，`substance_basis` 引的就是同一发上线的
        `visible_text_chars`（两处必须是同一颗数，否则"这一页多少字"有两种说法）；
      · `/collections`（真目录，标签占比同样 1.0）与 `/article`（387 字的真短文）→ `ok`，
        这一支是误报侧：把一次正确的回答判成失败，比漏报更难发现。
    """
    server = MasterFetchServer()
    nav = await server.web_read(url=f"{BASE}/nav")
    aside = await server.web_read(url=f"{BASE}/longaside")
    shell = await server.web_read(url=f"{BASE}/shell")
    packed = await server.web_read(url=f"{BASE}/shell/obfuscated")
    index = await server.web_read(url=f"{BASE}/collections")
    article = await server.web_read(url=f"{BASE}/article")

    nav_ok = (nav.substance == "nav_only" and nav.content_ok is False
              and "no prose" in nav.summary and nav.visible_text_chars > 0
              and ("stealthy" in nav.next_action or "links" in nav.next_action))
    aside_ok = (aside.substance == "collapsed" and aside.content_ok is False
                and f"{aside.visible_text_chars:,}" in aside.substance_basis)
    # 两个从前取不到的取值：`no_words` 的分支写着 len<1000（而混淆闸判的是长正文），
    # `nav_only` 的分支写着 >=4 行（而 /shell 整页只有 6 个可见字）。线上必须真拿得到。
    shell_ok = shell.substance == "nav_only" and shell.visible_text_chars < 200
    packed_ok = (packed.substance == "no_words" and packed.content_ok is False
                 and packed.error.startswith("obfuscated_payload"))
    index_ok = index.substance == "ok" and index.content_ok is True
    article_ok = article.substance == "ok" and article.content_ok is True
    rows = (nav, aside, shell, packed, index, article)
    seen = {r.substance for r in rows}
    ok = (nav_ok and aside_ok and shell_ok and packed_ok and index_ok and article_ok
          and seen == {"nav_only", "collapsed", "no_words", "ok"})
    record("3.3/3.4 薄正文说出它是哪一种薄，四种取值线上都取得到", ok,
           f"取值集合={sorted(seen)}；/nav={nav.substance}（整页可见 {nav.visible_text_chars}，"
           f"content_ok={nav.content_ok}）；/longaside={aside.substance}（basis 引的就是那颗"
           f"上线字数={aside_ok}）；/shell={shell.substance}（整页仅 {shell.visible_text_chars} "
           f"字）；/shell/obfuscated={packed.substance}（正文 "
           f"{sum(len(c) for c in packed.content)} 字，error={packed.error[:20]}…）；"
           f"/collections 抽出 {sum(len(c) for c in index.content)} 字仍是 {index.substance}"
           f"（目录没被误伤={index_ok}）；/article={article.substance}")


async def check_every_engine_that_said_nothing_says_why() -> None:
    """期 5.1 / 5.3：一轮搜索里每一家**没说话**的引擎都要在自己那行上说出为什么。

    前值（`review/2026-10-10-用户体验模拟报告.md` 的账）：12 轮里 **9 轮** bing_global 报
    empty 而不给理由；`consensus_basis` 写着 `degraded_pool` 却不说是 5 家拒了、被取消了、
    还是被地域挡了 —— 而这三种成因的修法互斥。那些事实今天**已经在进程里**（`EngineReport`
    带着 status / item_nodes / usable / yield_verdict），只是以前只有 `dhole -v` 读得到。

    这里断的是线上一发真搜索：`engine_detail` 每家一行、每行都有 `why`（不许空话），
    而 `families_answered_of_asked` 的分母必须等于这一轮真的问过的家族数。
    """
    server = MasterFetchServer()
    res = await server.web_search(query="python asyncio task group timeout", max_results=8)
    rows = res.engine_detail or []
    asked = len(set((r.get("family") or r.get("name") for r in rows)))
    named = [r for r in rows if not (r.get("why") or "").strip()]
    generic = [r for r in rows if (r.get("why") or "").strip().lower()
               in {"blocked", "empty", "error", "timeout", "not answered"}]
    frac = res.families_answered_of_asked or ""
    ok = (bool(rows) and not named and not generic and asked > 0
          and str(asked) in frac.replace(" asked", ""))
    answered = sum(1 for r in rows if r.get("verdict") == "healthy")
    record("5.1/5.3 每一家没说话的引擎都带着它自己的理由", ok,
           f"{len(rows)} 行 / {asked} 个家族，frac={frac!r}，实答 {answered}；"
           f"缺理由的行={len(named)}，只写空话的行={len(generic)}；"
           f"样例={(rows[0].get('why') if rows else '')[:90]!r}")


async def check_a_before_cutoff_judges_rows_row_by_row() -> None:
    """期4.3 / 4.6：`before=` 是一行一行的判决（上界），而这一轮的账要对得上。

    前值：这颗参数被**整颗拒**，拒辞写着"结果不带发布日期"—— 取证之后那句不再是真话
    （sogou 冻结夹具 8 个容器 8 个带绝对日期）。而第一版按行判决把方向写反了：它丢掉的
    恰好是调用方要的那批，回包字面上却仍然像"过滤过了"。这一条同时在活体上钉方向：
    **留在列表里的每一行，都不许被证明比那个截止更晚。**
    """
    from datetime import date, timedelta

    cutoff = date.today() - timedelta(days=30)
    server = MasterFetchServer()
    res = await server.web_search(query="固态电池 量产 公告", max_results=10,
                                  before=cutoff.isoformat())
    led = res.date_coverage or {}
    info = (res.date_filter or {}).get("before") or {}
    dated = [r for r in res.results if r.published]
    # 方向：能判的行必须都在窗口之内（证明更晚的那些应当已经不在列表里）。
    later = [r for r in dated
             if r.published_precision in ("day", "month", "year")
             and date.fromisoformat(r.published) >= cutoff]
    ok_shape = (info.get("cutoff") == cutoff.isoformat()
                and res.date_filter.get("exact") is False
                and led
                and led["rows_total"] - led["rows_dropped_by_before"] == led["rows_kept"]
                and led["rows_kept"] == len(res.results)
                and not later)          # 活的那一发：留下的行里不许有证明更晚的（今晚 2 行都是相对的）

    # 只断形状会**空转**：bing / yandex 的版面一个日期都不写（取证量过），上面那一发完全
    # 可能以"0 行可判"地通过。今晚这台机器连 sogou / baidu 都被挡着（实答为空、两家进
    # `engine_blocked`），所以"拿一发真搜索去验方向"在这里是**量不到的** —— 量不到就不许
    # 写成验到了。方向因此改在**冻存的各家 SERP 字节**上过真实 parser：确定的、可重跑的，
    # 而且用的就是线上同一套抽取与同一位判决作者（`serp_dates`）。
    from dhole_mcp import serp_dates
    from dhole_mcp.search import _row_date
    from dhole_mcp.search_metasearch import _TEXT_ENGINES, _stamp_row_date

    _iso = cutoff.isoformat()
    dres = await server.web_search(query="固态电池 量产 公告", max_results=10,
                                   before=_iso, engines=["sogou", "baidu"])

    fixtures = HERE.parent / "tests" / "engine_fixtures"
    per_engine: dict[str, list] = {}
    for html_path in sorted(fixtures.glob("*.html")):
        meta_path = html_path.with_suffix(".meta.json")
        if not meta_path.exists():
            continue
        name = json.loads(meta_path.read_text(encoding="utf-8")).get("engine") or ""
        eng_cls = _TEXT_ENGINES.get(name)
        if eng_cls is None:
            continue
        eng = eng_cls(proxy=None, timeout=15, verify=True)
        rows = eng.post_extract_results(eng.extract_results(
            html_path.read_text(encoding="utf-8")) or []) or []
        # 与线上同一格：版面原文先由各家 parser 取回，再由 `extract_kept` 那一颗
        # `_stamp_row_date` 走唯一的日期作者读成 值 + 精度 + 原文。少了这一步，量的是
        # 抽取器的原始串而不是行上线上的那一颗。
        # 键用**文件名**而不是引擎名：baidu 与 bing 各有两个版面，按引擎名建表会让后一份
        # 悄悄顶掉前一份，报出"bing 只有 1 行带日期"这种既是又不是的数。
        per_engine[html_path.stem] = [_stamp_row_date(r) for r in rows]

    with_date = [(n, r) for n, rows in per_engine.items()
                 for r in rows if getattr(r, "published", "")]
    verdicts = {}
    for n, rows in per_engine.items():
        # 读**行上线上的那三颗**（值 + 精度 + 原文），不把已盖章的 ISO 再解析一遍 ——
        # 再解析一次会把 "1 day ago" 那类相对行读成 day 精度，于是本不该参与比较的行
        # 被算成"证明更晚"，量出来的账就成了假数。行→判决的转换也只有 `search._row_date`
        # 一位作者。
        dates = [_row_date(r) for r in rows if getattr(r, "published", "")]
        if not dates:
            continue
        mid = sorted(d.value for d in dates)[len(dates) // 2]
        kept, fled = serp_dates.partition_for_before(dates, cutoff=mid)
        # 方向：留下的行里不许有"按天证明比截止更晚"的那一行。
        stayed_late = [d.value.isoformat() for d in kept
                       if d.precision == "day" and d.value >= mid]
        verdicts[n] = (len(dates), fled["rows_dropped_by_before"], fled["rows_kept"],
                       fled["rows_total"] - fled["rows_dropped_by_before"]
                       == fled["rows_kept"], mid.isoformat(), stayed_late)
    ok_frozen = (bool(with_date) and bool(verdicts)
                 and all(v[3] for v in verdicts.values())
                 and not any(v[5] for v in verdicts.values())
                 and all(r.published_basis for _n, r in with_date))
    ok = ok_shape and ok_frozen
    record("4.3/4.6 before= 逐行判决（上界）：账对得上，冻存 SERP 上没有一行证明更晚还被留下",
           ok,
           f"默认池那一发：{led.get('rows_total')} 行里 {led.get('rows_with_date')} 行带日期、"
           f"丢 {led.get('rows_dropped_by_before')}、留 {led.get('rows_kept')}、"
           f"判不了 {led.get('rows_unverifiable_by_before')}（形状齐={ok_shape}）；"
           f"sogou+baidu 那一发：实答={dres.engines_used} 被挡={dres.engine_blocked} → "
           f"今晚活 SERP 量不到方向，改在冻存字节上过真 parser；"
           f"冻存集 {len(with_date)} 行带日期，逐家（带日期行数, 丢掉, 留下, 账对得上, 截止）="
           + "; ".join(f"{n}:{v[:5]}" for n, v in sorted(verdicts.items()))
           + f"；更晚却留下的行={sum(len(v[5]) for v in verdicts.values())}")


async def main() -> int:
    print(f"靶站 {BASE}\n")
    # 门开在本脚本自己的 DHOLE_HOME 里（见文件头），不落用户的机器。
    identity.set_value("allow_private", True)
    try:
        await check_screenshot_carries_pixels()
    except Exception as e:
        record("D1 元素截图拿到像素并解出二维码", False,
               f"{type(e).__name__}: {e}")
    try:
        await check_submit_label()
    except Exception as e:
        record("D2 索引寻址 submit 的标签与审计写明目标", False,
               f"{type(e).__name__}: {e}")
    for fn, name in ((check_obfuscated_shell_is_not_a_success,
                      "R1 混淆壳页不再被报成成功"),
                     (check_legit_pages_survive_the_flip,
                      "R1 合法页面没有被翻闸误伤"),
                     (check_collapse_advice_offers_only_a_door_that_changes_the_reader,
                      "1.4 塌陷页只被劝去真换管路的那一枚"),
                     (check_a_write_refusal_answers_with_a_door_the_write_can_walk,
                      "1.5 写请求的拒答只点它收得下的那颗参数"),
                     (check_a_filled_input_reads_back_the_live_value,
                      "1.1 填进去的值读得回来，且与站点收到的那个一致"),
                     (check_an_undersized_screenshot_budget_refuses_before_the_browser_starts,
                      "2.5 装不下截图的预算在起浏览器之前点名拒绝"),
                     (check_a_focused_total_says_which_stream_it_counted,
                      "3.1 随 focus 变的总数说清是哪一路"),
                     (check_the_block_ledger_arrives_on_the_wire,
                      "3.6 块数写在信封上"),
                     (check_a_thin_body_names_its_own_kind_of_thin,
                      "3.3/3.4 薄正文说出它是哪一种薄"),
                     (check_every_engine_that_said_nothing_says_why,
                      "5.1/5.3 没说话的引擎各自带着理由"),
                     (check_a_before_cutoff_judges_rows_row_by_row,
                      "4.3/4.6 before= 逐行判决（上界）且账对得上")):
        try:
            await fn()
        except Exception as e:
            record(name, False, f"{type(e).__name__}: {e}")

    bad = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} 项通过")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
