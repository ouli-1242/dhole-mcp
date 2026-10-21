"""test_server_5.py —— 由 4 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
import json
import pytest
from dhole_mcp import env_registry, reranker, updater
from dhole_mcp.server import MasterFetchServer
import re
from datetime import datetime, timedelta, timezone
from dhole_mcp import feed as feed_mod
from dhole_mcp import guide
import inspect
from dhole_mcp import server as server_mod
import os
from pathlib import Path
from urllib.parse import urlparse
from dhole_mcp import paths


# ============================================================================
# 来源：test_the_reranker_model_state_is_askable_on_the_wire.py
# ============================================================================
# 「这台机器的重排器装了没有」在 wire 上问得出吗 —— R02-054（P2 · 组 F）。

ME_MODEL = "bge-test"
APPROX_MB = 278
MIN_BYTES = 1_000_000


class _StubModel:
    name = ME_MODEL
    label = "test cross-encoder"
    approx_bytes = APPROX_MB * 1_000_000
    min_bytes = MIN_BYTES
    publisher_sha256 = {}


@pytest.fixture()
def model_dir(tmp_path, monkeypatch):
    """把「模型在不在盘上」交给一个真目录，把这个**进程的账本**归零。

    三种形状（截断 / 半装 / 齐装）都用真文件说话 —— 判决那位作者读的就是这些字节。
    归零那四颗（`_reranker` / `_load_attempts` / `_last_attempt_ms` / 记录下的原因）是因为
    `model_status()` 里的 `loaded` 说的就是「这个进程握着暖会话」，而**全量里前头的测试真的会
    把会话暖起来**（第一趟全量就是这样：磁盘是空的而 state 报 loaded，绿不绿取决于谁先跑）。
    这一族的断言讲的是磁盘，所以磁盘与进程两头都要由测试握着。
    """
    d = tmp_path / "models" / ME_MODEL
    d.mkdir(parents=True)
    monkeypatch.setattr(reranker, "active_model", lambda: _StubModel)
    monkeypatch.setattr(reranker, "active_model_dir", lambda: d)
    monkeypatch.setattr(reranker, "_reranker", None)
    monkeypatch.setattr(reranker, "_load_attempts", 0)
    monkeypatch.setattr(reranker, "_last_attempt_ms", 0.0)
    monkeypatch.setattr(reranker, "_reranker_unavailable_reason", "")
    return d


def _install(model_dir, *, onnx_bytes=MIN_BYTES + 10, tokenizer=True):
    (model_dir / "model.onnx").write_bytes(b"0" * onnx_bytes)
    if tokenizer:
        (model_dir / "tokenizer.json").write_text("{}", encoding="utf-8")


def _structured(args: dict) -> dict:
    _content, structured = asyncio.run(MasterFetchServer()._dispatch("sys_state", args))
    return structured


def _cap_row() -> tuple[str, str, bool]:
    """`dhole -v` 的能力行（neural rerank 那一行）。"""
    for name, text, ok in updater.capabilities():
        if name == "neural rerank":
            return name, text, ok
    raise AssertionError("能力行里没有 neural rerank")


# ─── 1 · 名字册里没有这一类 ────────────────────────────────────

def test_the_vocabulary_holds_the_model_class():
    assert "models" in MasterFetchServer._STATE_TOKENS, MasterFetchServer._STATE_TOKENS


def test_an_unknown_token_still_teaches_the_vocabulary():
    """报错那一支自己会列词表：新加的一类必须出现在那里，否则问不出也学不到。"""
    out = _structured({"clear": ["typo-ed"]})
    assert "models" in out["error"], out["error"]


# ─── 2 · 没装那一支 ────────────────────────────────────────────

def test_a_machine_without_the_model_says_which_one_and_how_big(model_dir):
    out = _structured({"show": ["models"]})
    row = out["reranker"]
    assert row["state"] == "not_downloaded", row
    assert row["model"] == ME_MODEL, row
    assert str(APPROX_MB) in out["message"], out["message"]
    assert "not downloaded" in out["message"], out["message"]
    assert "first neural search" in out["message"], out["message"]


def test_the_answer_carries_no_disk_path(model_dir):
    """本机信息的边界：名字可以给，`~` 底下那个绝对路径不能上回执。"""
    _install(model_dir)
    blob = json.dumps(_structured({"show": ["models"]}), ensure_ascii=False)
    assert str(model_dir) not in blob, blob[:400]


def test_the_answer_lists_every_registered_profile(tmp_path, monkeypatch):
    """多档模型不能只报"现在这颗"：换档之前得看得见另一颗在不在盘上。"""
    monkeypatch.setattr(reranker.paths, "models_dir", lambda: tmp_path / "models")
    monkeypatch.setattr(env_registry, "_load_file", lambda: {})
    monkeypatch.delenv("DHOLE_RERANK_MODEL", raising=False)
    reranker.reset_selection_cache()
    row = _structured({"show": ["models"]})["reranker"]
    profiles = {a["model"]: a for a in row["available"]}
    assert {"bge-zh", "mmarco"} <= set(profiles), profiles
    assert profiles["bge-zh"]["active"] is True
    assert profiles["mmarco"]["active"] is False
    assert profiles["mmarco"]["arch_supported"] is True
    assert row["model"] == "bge-zh"
    assert row["raw_high"] == 0.5


# ─── 3 · 判决只许有一位作者（今天有三处各算各的） ──────────────

def test_a_truncated_download_is_not_reported_as_ready(model_dir):
    """onnx 在但只有半个文件：加载那一侧说「不在」，`dhole -v` 的能力行今天说 ready。

    只用现成的两位读者说话（不碰本刀新增的 API）—— 这条锁的红与绿都只取决于分歧本身。
    也不许反过来把它说成「一颗都没有」：那会让人以为重下要从头开始，而它是可续的。
    """
    _install(model_dir, onnx_bytes=1_024)
    assert reranker.model_present() is False
    _name, text, ok = _cap_row()
    assert ok is False, (_name, text, ok)
    assert "incomplete" in text and "not usable" in text, text


def test_the_doctor_row_does_not_call_a_truncated_file_ok(model_dir):
    """体检行今天连字节数都不看：`0.0MB in 2 file(s)` 后面跟着一个 `ok`。"""
    _install(model_dir, onnx_bytes=1_024)
    checks: list = []
    updater._append_rerank_model(checks)
    row = [c for c in checks if c[0] == "rerank model"]
    assert row and row[0][1] != "ok", row


def test_a_half_install_is_not_reported_ok_by_the_doctor(model_dir):
    """onnx 齐但缺 tokenizer.json：体检行今天只看 model.onnx 就报 ok。"""
    _install(model_dir, tokenizer=False)
    assert reranker.model_present() is False
    checks: list = []
    updater._append_rerank_model(checks)
    row = [c for c in checks if c[0] == "rerank model"]
    assert row and row[0][1] != "ok", row


def test_the_cli_and_the_wire_start_from_one_verdict(model_dir):
    """同一格（截断）里，wire 的 state 与 CLI 的 ready 位不许各说各话。"""
    _install(model_dir, onnx_bytes=1_024)
    state = _structured({"show": ["models"]})["reranker"]["state"]
    _name, _text, ok = _cap_row()
    assert (state in ("loaded", "on_disk")) == ok, (state, _text)


@pytest.mark.parametrize("shape", ["empty", "truncated", "half_install", "complete"])
def test_every_shape_leaves_the_three_readers_with_one_answer(model_dir, shape):
    """四种形状 × 三位读者：判决只有一个来源，措辞可以各写各的。

    这一条是本刀的核心断言 —— 它不许任何一位读者再回去自己数文件（那正是今天
    截断那一格里 CLI 两行替半个文件背书的原因）。
    """
    if shape == "truncated":
        _install(model_dir, onnx_bytes=1_024)
    elif shape == "half_install":
        _install(model_dir, tokenizer=False)
    elif shape == "complete":
        _install(model_dir)

    present = reranker.model_present()
    state = reranker.model_status()["state"]
    # 同一进程里若有别的测试已经把会话暖起来，`loaded` 与 `on_disk` 说的是同一件事
    # （盘上那一份能用）—— 这条锁要钉的是「有/没有」，不是「这一发暖没暖」。
    assert (state in ("on_disk", "loaded")) == present, (shape, state)
    _name, _text, cap_ok = _cap_row()
    assert cap_ok == present, (shape, _text)
    checks: list = []
    updater._append_rerank_model(checks)
    doc = [c for c in checks if c[0] == "rerank model"][0]
    assert (doc[1] == "ok") == present, (shape, doc)


# ─── 4 · 装了 / 加载了 / 装了但排不起来 ────────────────────────

def test_an_installed_model_names_which_one(model_dir):
    _install(model_dir)
    row = _structured({"show": ["models"]})["reranker"]
    assert row["state"] == "on_disk", row
    assert row["model"] == ME_MODEL, row


def test_a_loaded_reranker_is_reported_as_loaded(model_dir, monkeypatch):
    _install(model_dir)
    monkeypatch.setattr(reranker, "_reranker", object())
    row = _structured({"show": ["models"]})["reranker"]
    assert row["state"] == "loaded", row


def test_the_process_ledger_rides_with_the_verdict(model_dir, monkeypatch):
    """「盘上有但加载失败」与「盘上有但还没试过」是两种处方：账本要一起交回。"""
    _install(model_dir)
    monkeypatch.setattr(reranker, "_load_attempts", 1)
    monkeypatch.setattr(reranker, "_last_attempt_ms", 2383.4)
    monkeypatch.setattr(reranker, "_reranker_unavailable_reason",
                        "reranker init failed: onnx session boom")
    row = _structured({"show": ["models"]})["reranker"]
    assert row["state"] == "on_disk", row
    assert row["load_attempts"] == 1, row
    assert row["last_attempt_ms"] == 2383, row
    assert "onnx session boom" in row["recorded_reason"], row
    assert "2383" in json.dumps(row), row


def test_a_process_that_never_tried_says_zero_not_a_number(model_dir):
    _install(model_dir)
    row = _structured({"show": ["models"]})["reranker"]
    assert row["load_attempts"] == 0, row
    assert row["last_attempt_ms"] == 0, row


# ─── 5 · 门：抹不在这一类里，`all` 也不带它 ────────────────────

def test_erasing_the_model_class_is_refused_with_its_own_reason(model_dir):
    out = _structured({"clear": ["models"]})
    assert "show-only" in out["error"], out
    # 不许复用 config 那句操作员侧出路（那是另一类东西的门）
    assert "config unset" not in out["error"], out["error"]
    assert not out.get("cleared"), out  # 空回执上线即被摘掉（wire 的压缩），"没抹掉任何一类"由缺键说


def test_models_is_outside_the_all_expansion(model_dir):
    """与 `config` 同律：`all` 说的是这颗工具管得到的那几类，点名才问得出模型。"""
    _install(model_dir)
    out = _structured({"show": ["all"]})
    assert "models" not in out["message"], out["message"]


# ─── 6 · 上了线就要在册、在册就要看得见 ────────────────────────

def test_the_show_parameter_names_the_new_class():
    """宿主免审批读的是 tools/list：词表里多一类而描述没写，等于没有那一类。"""
    defs = MasterFetchServer._TOOL_DEFS
    sys_state = [t for t in defs if t["name"] == "sys_state"][0]
    show_desc = json.dumps(sys_state["inputSchema"], ensure_ascii=False)
    assert "models" in show_desc, show_desc[:300]


def test_the_new_field_is_in_the_manual():
    from dhole_mcp import guide
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    assert "show=['models']" in corpus or "show=[\"models\"]" in corpus, corpus[:200]


# ============================================================================
# 来源：test_a_feed_reply_arrives_in_the_same_envelope_as_every_other_tool.py
# ============================================================================
# `feed_read` 的回信也要有信封：几个源、几条、什么时候拿的、是不是旧的、下一步做什么。

FEED = ("<?xml version='1.0'?><rss version='2.0'><channel><title>{title}</title>"
        "{items}</channel></rss>")
ITEM = ("<item><title>{t}</title><link>https://blog.test/{t}</link>"
        "<pubDate>Tue, 14 Oct 2026 09:00:00 GMT</pubDate><description>d</description></item>")


def _feed(titles: list[str], title: str = "Blog") -> bytes:
    return FEED.format(title=title, items="".join(ITEM.format(t=t) for t in titles)).encode()


PAGE = (b"<html><head><link rel='alternate' type='application/rss+xml' "
        b"href='https://blog.test/real-feed.xml'></head><body>hi</body></html>")


def _serve(monkeypatch, bodies: dict[str, object], clock: dict | None = None):
    """假网络：{url: 文档字节 或 错误句}。返回被取过的 URL 顺序（可重跑、零网络）。

    `clock` 是与 `patch_clock` **同一只**计数器：钟按「第几发抓取」走，所以文档到达的
    时刻与信封装配的时刻不是同一个数 —— 盖错了地方当场就看得出来（发现那一支先取网页）。
    """
    calls: list[str] = []

    async def fake_grab(url, timeout, headers=None):
        calls.append(url)
        if clock is not None:
            clock["grabbed"] = int(clock.get("grabbed", 0)) + 1
        got = bodies.get(url)
        if isinstance(got, str):
            return b"", 0, "", got, {}
        return got, 200, "utf-8", "", {"content-type": "application/rss+xml"}

    monkeypatch.setattr(feed_mod, "_grab", fake_grab)
    return calls


def _stamps() -> list[datetime]:
    """秒级分开的时刻：线上的 `fetched_at` 本来就被剪到一秒精度，用微秒区分两发
    只会让断言去比一个已经被剪掉的数量（那样这条锁就是在比剪子，不是比盖戳的地方）。
    """
    base = datetime(2026, 10, 15, 12, 0, 0, 500_000, tzinfo=timezone.utc)
    return [base.replace(microsecond=500_000) + timedelta(seconds=i) for i in range(20)]


@pytest.fixture()
def patch_clock(monkeypatch):
    """可控的钟：第 n **次抓取**读到第 n 个时刻（实现里唯一的取时刻来源）。"""
    stamps = _stamps()
    state = {"grabbed": 0}

    def _utc_now():
        # 抓取计数由 `_serve` 推进，所以这里吃的是"已经取了几发"，不是"被调了几次"。
        return stamps[max(0, int(state["grabbed"]) - 1)]

    monkeypatch.setattr(feed_mod, "_utc_now", _utc_now)
    return state


def _payload(tool: str, args: dict) -> dict:
    out = asyncio.run(MasterFetchServer()._dispatch(tool, args))
    return out[1] if isinstance(out, tuple) and len(out) == 2 else {}


def _feed_payload(args: dict) -> dict:
    return _payload("feed_read", args)


URL_A = "https://blog.test/a.xml"
URL_B = "https://blog.test/b.xml"
URL_DEAD = "https://blog.test/dead.xml"


# ─── 1 · 批次级信封 ─────────────────────────────────────────────

def test_a_feed_reply_names_how_many_sources_and_items(monkeypatch):
    _serve(monkeypatch, {URL_A: _feed(["one", "two"]), URL_B: _feed(["three"])})
    payload = _feed_payload({"urls": [URL_A, URL_B]})
    summary = payload.get("summary", "")
    assert summary, f"顶层仍只有 `feeds`：{sorted(payload)}"
    assert "2 URLs asked" in summary, summary
    assert "3 items" in summary, summary
    assert "holds no cache" in summary, summary


def test_the_summary_numbers_cannot_disagree_with_the_rows(monkeypatch):
    """三发里两发有条目、一发报错：信封上那几颗数必须能按行复核。"""
    _serve(monkeypatch, {URL_A: _feed(["one", "two"]), URL_B: _feed(["three"]),
                         URL_DEAD: "HTTP 410 Gone"})
    payload = _feed_payload({"urls": [URL_A, URL_B, URL_DEAD]})
    rows = payload["feeds"]
    summary = payload["summary"]
    items = sum(len(r.get("items") or []) for r in rows)
    errored = sum(1 for r in rows if r.get("error"))
    asked = re.search(r"(\d+) URLs asked", summary)
    got = re.search(r"(\d+) items", summary)
    answered = re.search(r"(\d+) rows answered", summary)
    assert asked and got and answered, summary
    assert int(asked.group(1)) == int(answered.group(1)) == len(rows) == 3, summary
    assert int(got.group(1)) == items == 3, (summary, items)
    assert f"{errored} rows errored" in summary, summary


def test_a_batch_that_delivered_items_pays_no_next_action(monkeypatch):
    """有交付就没有建议：一次干净成功不付这句话（11/16 那一族不付费）。"""
    _serve(monkeypatch, {URL_A: _feed(["one"])})
    payload = _feed_payload({"urls": [URL_A]})
    assert "next_action" not in payload, payload


def test_a_batch_where_every_source_failed_says_what_to_do_next(monkeypatch):
    _serve(monkeypatch, {URL_A: "HTTP 503 Service Unavailable",
                         URL_B: "timed out"})
    payload = _feed_payload({"urls": [URL_A, URL_B]})
    action = payload.get("next_action", "")
    assert action, payload
    assert "diagnosis" in action, action          # 逐行的结构化判据才是下一步的出处
    assert "web_read" in action, action           # feed 不在了就去读那一站的页面


def test_a_quiet_batch_says_the_sources_published_nothing(monkeypatch):
    """每一行都拿到了文档、条数却是 0：这一封要说「没有新东西」，不是「失败了」。"""
    _serve(monkeypatch, {URL_A: _feed([]), URL_B: _feed([])})
    payload = _feed_payload({"urls": [URL_A, URL_B]})
    action = payload.get("next_action", "")
    assert action, payload
    assert "error" not in action.lower(), action


def test_a_poll_with_nothing_new_is_not_reported_as_a_failure(monkeypatch):
    """`since` 筛空了是「没有增量」：这一格不许与「源报错」共用一句话。"""
    _serve(monkeypatch, {URL_A: _feed(["old"])})
    payload = _feed_payload({"urls": [URL_A], "since": "2027-01-01"})
    row = payload["feeds"][0]
    assert row["items_older_than_since"] == 1, row
    action = payload.get("next_action", "")
    assert action and "since" in action.lower(), payload
    assert "fail" not in action.lower(), action   # 不是失败，是安静


# ─── 2 · 每行的取回时刻 ─────────────────────────────────────────

def test_each_row_names_the_moment_its_document_arrived(monkeypatch, patch_clock):
    _serve(monkeypatch, {URL_A: _feed(["one"])}, clock=patch_clock)
    row = _feed_payload({"urls": [URL_A]})["feeds"][0]
    assert row.get("fetched_at") == "2026-10-15T12:00:00Z", row


def test_the_stamp_is_the_documents_moment_not_the_pages(monkeypatch, patch_clock):
    """发现那一支先取网页、再取 feed：盖在第一发就是假话（那一刻拿到的是 HTML）。"""
    calls = _serve(monkeypatch, {"https://blog.test/": PAGE,
                                 "https://blog.test/real-feed.xml": _feed(["one"])},
                   clock=patch_clock)
    row = _feed_payload({"urls": ["https://blog.test/"]})["feeds"][0]
    assert len(calls) == 2, calls
    assert row["discovered_from"] == "https://blog.test/", row
    assert row["fetched_at"] == "2026-10-15T12:00:01Z", row


def test_a_row_with_no_document_carries_no_stamp(monkeypatch, patch_clock):
    """没有文档就没有「它是什么时候拿的」：留空并被线压缩摘掉，比补一个此刻诚实。"""
    _serve(monkeypatch, {URL_DEAD: "HTTP 410 Gone"}, clock=patch_clock)
    row = _feed_payload({"urls": [URL_DEAD]})["feeds"][0]
    assert row.get("error"), row
    assert "fetched_at" not in row, row


def test_the_feed_path_holds_no_cache_and_says_so(monkeypatch):
    """她的刁难点：拿了一份缓存旧版必须明说。这一条路的真话是「没有缓存」——
    可执行证明：两次调用之间源换了内容，第二次必须交回新内容。"""
    _serve(monkeypatch, {URL_A: _feed(["one"])})
    first = _feed_payload({"urls": [URL_A]})
    assert "one" in json.dumps(first, ensure_ascii=False)

    _serve(monkeypatch, {URL_A: _feed(["one", "two"])})
    second = _feed_payload({"urls": [URL_A]})
    titles = [i["title"] for i in second["feeds"][0]["items"]]
    assert titles == ["two", "one"] or "two" in titles, titles
    assert "cache" in second["summary"].lower(), second["summary"]


def test_a_batch_that_came_back_short_says_so(monkeypatch):
    """空白 URL 被方法滤掉是旧行为，本刀改的是**可见性**：`asked` 高于 `answered`
    就是产品在自己说「有几颗没换成行」。说不出是哪一颗 —— 那一半另记 R02-053。
    """
    _serve(monkeypatch, {URL_A: _feed(["one"])})
    payload = _feed_payload({"urls": ["", URL_A, "   "]})
    summary = payload["summary"]
    assert "3 URLs asked" in summary, summary
    assert "1 rows answered" in summary, summary
    assert len(payload["feeds"]) == 1, payload


# ─── 3 · 同一套信封，被拒绝那一发也不例外 ───────────────────────

def test_a_refused_call_shares_the_same_top_level_shape(monkeypatch):
    """ValueError 那一支早就有 error/next_action，缺的是 summary：一发也没出的时候
    「几个源、几条」同样要说得出（不许多出一颗假的 fetched_at）。

    2026-10-19（R02-133 · F114）这一格从四格变五格：多出来的正是 `diagnosis` —— 手册
    （`references/fields.md:67`）对每副戴 `error` 的回执都承诺把手那一格，从前这一扇的门里面
    偏偏没有。这一枚钉的是"同一副骨架"，不是"恰好四格"，所以跟改的是名单而不是判据；
    「不许多出一颗假的 fetched_at」那一条一字未动，它量的还是那一件事。
    """
    payload = _feed_payload({"urls": []})
    assert set(payload) == {"feeds", "error", "next_action", "summary", "diagnosis"}, \
        sorted(payload)
    assert payload["feeds"] == []
    assert "no" in payload["summary"].lower(), payload["summary"]
    # 一发都没出去：不许有一颗"取回时刻"混进这一封（顶层与行都不该有）
    assert "fetched_at" not in payload, payload


# ─── 4 · 上了线就要在册 ─────────────────────────────────────────

def test_both_new_reads_are_documented():
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    assert "feed_read" in corpus
    # 每行的 fetched_at 在 feed 这条路上是什么意思（没有缓存可问），要写清
    assert "no cache" in corpus.lower() or "holds no cache" in corpus.lower(), (
        "手册没说 feed_read 这条路不缓存：读者会把每行的 fetched_at 当成回放时刻")
    assert "URLs asked" in corpus, (
        "批次级 summary 的形状没进手册：只读手册的人不知道有这一行")


# ============================================================================
# 来源：test_web_act_b5.py
# ============================================================================
# B5-14b：`web_act` —— 页面动作从 web_read 里搬出来（§4 第 2 行，第 17 颗工具）。

URL = "https://example.com/feed"
CHAIN = [{"scroll": 2}, {"click": "button.load-more"}]


def _defs() -> dict:
    return {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}


def _capture_read(monkeypatch, **call):
    """走一次真实分发，抓读的那条管线实际收到的参数（动作链只能由它执行）。"""
    seen: dict = {}

    async def fake(self, url, **kwargs):
        seen["url"] = url
        seen.update(kwargs)
        from dhole_mcp.server import ResponseModel
        return ResponseModel(url=url, status=200, content=["body"],
                             fetcher_used="stealthy")

    monkeypatch.setattr(MasterFetchServer, "web_read", fake)
    args = {"url": URL, "actions": CHAIN}
    args.update(call)
    asyncio.run(MasterFetchServer()._dispatch("web_act", args))
    return seen


def _dispatch(tool: str, args: dict) -> dict:
    _content, structured = asyncio.run(MasterFetchServer()._dispatch(tool, args))
    return structured


class TestTheToolExists:

    def test_it_is_registered_with_the_names_it_can_honour(self):
        assert "web_act" in _defs(), sorted(_defs())
        # `pin`（刀9-7 · F9）是这一扇门的公开参数：驻留的话必须在连接期说得出，
        # sys_guide 那一卷救不回来（参数不在 tools/list 上就根本传不进来）。
        assert server_mod._TOP_LEVEL_ARGS["web_act"] == frozenset({
            "url", "actions", "extraction_type", "max_content_chars", "focus",
            "timeout", "anti_detect", "session_id", "cdp_url", "confirm",
            "check_login", "headed", "pin"})

    def test_both_required_names_are_on_the_schema(self):
        schema = _defs()["web_act"]["inputSchema"]
        assert schema["required"] == ["url", "actions"]
        assert set(schema["properties"]) == set(server_mod._TOP_LEVEL_ARGS["web_act"])

    def test_it_is_a_write_on_the_wire_but_not_the_irreversible_one(self):
        """readOnly/idempotent 都是 False；destructive 落回 spec 默认（键不出现）。

        动作链会改页面，但真正不可逆的那一步（submit）不带 confirm 就暂停 —— 客户端
        要按"每次都要问"来标它，就会把 scroll 也一起问一遍，那道门的设计意义全没了。
        2026-10-07 修正：destructiveHint 曾显式写 False，而 SDK 规范语义是
        "If false, the tool performs only additive updates … Default: true" ——
        submit 门自己承认不可逆，写 False 是主动错报（hint 的读者是宿主不是
        confirm 门）。删键落回默认 true，声明从错变保守；本测试改为钉"键不出现"，
        不许它再以任何显式值回来。
        """
        ann = _defs()["web_act"]["annotations"]
        assert ann["readOnlyHint"] is False and ann["idempotentHint"] is False
        assert ann["openWorldHint"] is True
        assert "destructiveHint" not in ann

    def test_the_three_rules_came_along_with_the_parameter(self):
        """搬工具不许搬走规矩：snapshot 先数 / submit 先问 / 验证码三次就转交。"""
        desc = _defs()["web_act"]["description"]
        for needle in ('"snapshot"', "PAUSES", "at most 3", "hand off",
                       "never from cache", "metadata.actions"):
            assert needle in desc, needle

    def test_the_door_it_points_at_exists(self):
        assert "sys_guide(topic='behavior', section='Page interaction')" \
            in _defs()["web_act"]["description"]


class TestWhatItRequires:

    def test_a_missing_chain_says_what_is_missing_and_stops(self, monkeypatch):
        seen = _capture_read(monkeypatch, actions=None)
        out = _dispatch("web_act", {"url": URL})
        blob = json.dumps(out).lower()
        assert not seen, "没有动作链却已经把页面动了一遍"
        assert "required" in blob and "actions" in blob, out
        assert "typeerror" not in blob, "缺必填炸成栈异常，等于把契约换了个形状"

    def test_an_empty_chain_refuses_too(self, monkeypatch):
        seen = _capture_read(monkeypatch, actions=[])
        out = _dispatch("web_act", {"url": URL, "actions": []})
        assert not seen, "空链也发出去了"
        assert "actions" in json.dumps(out).lower(), out

    def test_a_missing_url_refuses_before_anything_is_sent(self, monkeypatch):
        seen = _capture_read(monkeypatch, url="")
        out = _dispatch("web_act", {"actions": CHAIN})
        assert not seen and "url" in json.dumps(out).lower(), out


class TestEveryParameterArrives:
    """11 颗名字逐颗到达到管线 —— 少转一颗就是能力在 wire 上不存在。"""

    def test_the_chain_and_the_url_are_forwarded_by_name(self, monkeypatch):
        seen = _capture_read(monkeypatch)
        assert seen["url"] == URL and seen["actions"] == CHAIN, seen

    def test_an_action_call_never_reads_the_cache(self, monkeypatch):
        """链后的正文只在动作之后存在：从缓存答就等于答了一个没被问过的页面。"""
        seen = _capture_read(monkeypatch)
        assert seen["cache_ttl"] == 0, seen

    @pytest.mark.parametrize("key,value", [
        ("extraction_type", "html"), ("max_content_chars", 1234),
        ("focus", "quotes"), ("timeout", 45000), ("anti_detect", "off"),
        ("session_id", "sess1"), ("cdp_url", "http://127.0.0.1:9222"),
        ("confirm", True), ("check_login", True),
    ])
    def test_each_optional_name_reaches_the_pipeline(self, monkeypatch, key, value):
        seen = _capture_read(monkeypatch, **{key: value})
        assert seen.get(key) == value, seen

    def test_an_unsent_name_is_absent_not_none(self, monkeypatch):
        """没传的键不许被递成 None —— 那会盖掉管线自己那个非 None 的默认。"""
        seen = _capture_read(monkeypatch)
        for key in ("extraction_type", "focus", "anti_detect", "session_id",
                    "cdp_url"):
            assert key not in seen or seen[key] is None, key


class TestTheReadSideLostTheChain:

    def test_web_read_no_longer_advertises_actions(self):
        props = _defs()["web_read"]["inputSchema"]["properties"]
        assert "actions" not in props
        assert "actions" not in server_mod._TOP_LEVEL_ARGS["web_read"]
        assert "actions" not in _defs()["web_read"]["description"]

    def test_the_old_spelling_names_the_new_door(self):
        with pytest.raises(ValueError, match="Unsupported argument") as e:
            _dispatch("web_read", {"url": URL, "actions": CHAIN})
        msg = str(e.value)
        assert "actions" in msg and "web_act" in msg, msg

    def test_the_dispatcher_cannot_smuggle_a_chain_back_into_the_read(self):
        """调度器不许把 `actions` 悄悄塞回 web_read：那条通道一开，两扇门又是一扇。

        形状而不是单词 —— 这一段里"actions"作为散文出现是正常的（元素截图那颗
        注记就在讲它），被**传下去**才是问题。
        """
        src = inspect.getsource(MasterFetchServer._dispatch)
        branch = src.split('if name == "web_read":')[1].split("elif name ==")[0]
        for shape in ("actions=", '"actions"', "'actions'"):
            assert shape not in branch, (shape, branch)

    def test_the_pipeline_still_takes_the_chain(self):
        """公开面搬走，管线不动：web_act 走这一条。

        拆门的意义是"一扇门上不说两套规矩"，不是把执行链拆成两条 —— 真拆了，回放和
        登录流就各自长出一套动作语义。
        """
        assert "actions" in inspect.signature(MasterFetchServer.web_read).parameters
        assert "actions" in inspect.signature(MasterFetchServer.web_act).parameters


class TestARungIsNamedOrRefused:
    """第五档不存在，所以".ultra"要被拒并列出那四颗 —— 而不是炸一个 KeyError。

    14b 拆出这颗门的时候踩过一次：rung 的归一化写在 `name == "web_read"` 那一个
    分支里，web_act 转发的值没人问就进了 `_ANTI_DETECT[level]`。 Literal 只存在于
    schema，读参数的还是这台服务器自己。
    """

    @pytest.mark.parametrize("tool", ["web_read", "web_act"])
    def test_a_fifth_rung_is_refused_naming_the_four(self, tool):
        """问这一颗的是 `_validate_tool_args` —— 那才是 wire 上真的那道门。

        Literal 只到客户端为止；`_dispatch` 是门后面的分发器，测试打在门上才是
        客户端会碰到的行为。
        """
        with pytest.raises(ValueError, match="must be one of") as exc:
            server_mod._validate_tool_args(tool, {"anti_detect": "ultra"})
        msg = str(exc.value)
        for rung in ("off", "basic", "auto", "aggressive"):
            assert rung in msg, (rung, msg)

    def test_the_rung_list_has_one_home(self):
        """名单只有一份：加了会读档位的工具，参数面与那道门一起跟上。"""
        for tool in server_mod._RUNG_TOOLS:
            assert "anti_detect" in server_mod._TOP_LEVEL_ARGS[tool], tool

    @pytest.mark.parametrize("tool,base", [
        ("web_read", {"url": URL}),
        ("web_act", {"url": URL, "actions": [{"wait": 1}]}),
    ])
    def test_an_absent_rung_stays_absent_so_the_stored_one_can_answer(self, tool, base):
        """`anti_detect` 缺席不等于 "auto"：缺席才是"用 cred_defaults 存的那一档"。"""
        seen = {}

        async def fake_read(self, url, **kwargs):
            seen.update(kwargs)
            from dhole_mcp.server import ResponseModel
            return ResponseModel(url=url, status=200, content=["body"],
                                 fetcher_used="http")

        monkey = pytest.MonkeyPatch()
        try:
            monkey.setattr(MasterFetchServer, "web_read", fake_read)
            asyncio.run(MasterFetchServer()._dispatch(tool, base))
        finally:
            monkey.undo()
        assert seen.get("anti_detect", None) is None, seen


class TestScreenshotPixelsReachTheWire:
    """web_act 的截图像素必须真的到 content —— 回执只留 `image: true` 这个名字。

    web_act 把链委托给 web_read 执行，`_report_action_outcomes` 在那里把 base64
    从回执挪进 `_action_images`（PrivateAttr，不进 JSON），像素再往前一步变成
    ImageContent 是 dispatch 层的活。那条线此前只接在 web_read 分支上 —— web_act
    分支把它们丢在了原地：回执说「有图」，客户端却什么都看不到。真实协议层才测得
    到（单元层一直只看回执形状，而这根管子在 dispatch）。2026-10-05 那次真实使用
    全真模拟第一次过 wire 时抓到。
    """

    def test_pixels_ride_the_content_list(self, monkeypatch):
        from mcp.types import ImageContent

        async def fake_act(self, url, actions=None, **kwargs):
            from dhole_mcp.server import ResponseModel
            result = ResponseModel(url=url, status=200, content=["body"],
                                   fetcher_used="stealthy")
            result._action_images = [{"data": "cGl4ZWw=", "mime_type": "image/png"}]
            return result

        monkeypatch.setattr(MasterFetchServer, "web_act", fake_act)
        content, _structured = asyncio.run(MasterFetchServer()._dispatch(
            "web_act", {"url": URL, "actions": CHAIN}))
        imgs = [c for c in content if isinstance(c, ImageContent)]
        assert imgs and imgs[0].data == "cGl4ZWw=", (
            "web_act 的截图像素在 dispatch 处被丢掉 —— "
            "_wire_result 少了 images= 那一参")


# ============================================================================
# 来源：test_the_receipt_does_not_spell_this_machines_paths.py
# ============================================================================
# 回执可以说"你按什么顺序试了哪几处"，不可以说"这台机器把东西放在哪"。

def _parse(**kw):
    import asyncio
    return asyncio.run(MasterFetchServer().doc_parse(**kw))


def _roots_as_a_uri_spells_them():
    """本机根在 `url` 里的两种拼法。

    `Path.as_uri()` 会把分隔符换成 `/`，所以拿 `os.path.expanduser("~")`
    （`C:\\Users\\ouli`）去 `in r.url`（`file:///C:/Users/ouli/...`）**永远为假** ——
    这条断言在修复前也是绿的，等于没测。实测踩过：把遮蔽器换成恒等函数之后，
    `home not in r.url` 照样通过。所以这里两种拼法都列上。
    """
    roots = []
    for root in (os.path.expanduser("~"), os.getcwd()):
        if root and root not in ("~", "/", "\\"):
            roots.append(root)
            roots.append(root.replace("\\", "/"))
    return roots


def _spells_a_machine_root(text: str) -> str:
    """``text`` 里逐字出现的本机根（空串表示没有）。"""
    for root in _roots_as_a_uri_spells_them():
        if root in text:
            return root
    return ""


@pytest.fixture
def a_readable_file(tmp_path):
    f = tmp_path / "report.txt"
    f.write_text("第一行\n第二行\n", encoding="utf-8")
    return f


# ─── 1 · 失败那一句 ──────────────────────────────────────────────

def test_a_missing_relative_file_does_not_spell_the_server_cwd():
    """D11 的原形状：两个候选都是机器自己造的，一个都不许原样出去。"""
    r = _parse(file_path="definitely-missing-d11.docx")
    assert r.error.startswith("File not found"), r.error
    assert not _spells_a_machine_root(r.error), r.error


def test_the_names_are_there_so_the_order_is_still_readable():
    """反向：遮蔽不许退化成"把路径删掉"。

    调用方要的是"你按什么顺序试了哪几处" —— 少了名字，这句话就只剩"没找到"，而
    `next_action` 让它去传 `cwd=`，它却不知道自己原本的候选落在哪一支上。
    """
    r = _parse(file_path="definitely-missing-d11.docx")
    assert paths.MASKED_CWD in r.error, r.error
    assert os.path.join(paths.MASKED_CWD, "definitely-missing-d11.docx") in r.error, \
        r.error


def test_a_cwd_whose_name_carries_the_username_does_not_ride_along():
    """调用方自己给的 `cwd=` 里，**机器身份**那一格不许出去。

    这一格从前写的是「临时目录整条不许出现在 error 里」，而它成立的条件没写在句子里：
    临时目录恰好住在主目录下，于是它作为"主目录的一段"被遮掉了。评审 R02-090 量到的
    正是这个假设的另一面 —— 把 `TMP` 指到别的盘，整条路径原样回显，目录名里还带着用户名
    （pytest 自己的 `pytest-of-<user>` 就是这个形状）。这条承诺的对象从来是**身份**，
    不是"临时目录"：所以这里造一颗名字里带用户名的目录来撞，并把判据写成身份不许出去。
    """
    import tempfile
    user = os.path.basename(os.path.expanduser("~"))
    with tempfile.TemporaryDirectory() as base:
        d = os.path.join(base, f"probe-of-{user}")
        os.mkdir(d)
        r = _parse(file_path="gone.txt", cwd=d)
        assert r.error.startswith("File not found"), r.error
        assert user.lower() not in r.error.lower(), r.error
        assert "gone.txt" in r.error, r.error


def test_a_foreign_cwd_the_caller_wrote_is_still_readable_back(tmp_path):
    """反向守卫：遮蔽不许吞掉调用方自己写的路径 —— 那句承诺写在遮蔽器的 docstring 里。

    这一格刻意用一颗**名字里没有身份**的目录：没有身份、又不撞任何机器根的 `cwd=` 是调用方
    的字符串，回执里要逐字读得回来。把它一起遮掉就退化成"把路径删掉"（同一反面在
    `test_the_names_are_there_so_the_order_is_still_readable` 里钉过）。
    """
    d = tmp_path / "caller-eyes-only"
    d.mkdir()
    r = _parse(file_path="gone.txt", cwd=str(d))
    assert r.error.startswith("File not found"), r.error
    assert "caller-eyes-only" in r.error, r.error
    assert "gone.txt" in r.error, r.error


# ─── 2 · 成功那一句（更常出现的那一处）──────────────────────────

def test_a_successful_parse_does_not_spell_the_resolved_path(a_readable_file):
    r = _parse(file_path=str(a_readable_file))
    assert r.status == 200 and not r.error, r.error
    assert not _spells_a_machine_root(r.url), r.url


def test_the_masked_url_still_names_the_file(a_readable_file):
    """`url` 是回执的身份字段：遮过之后仍要答得出"读的是哪一份"。

    从前这一格断言 `MASKED_HOME in r.url` —— 那句里的"遮过"其实是环境给的（临时目录恰好
    住在主目录下）。评审 R02-090 之后判据写成它本来的样子：**名字要在**，**机器身份不许在**。
    两条都不依赖 `TMP` 被指到哪个盘。
    """
    r = _parse(file_path=str(a_readable_file))
    user = os.path.basename(os.path.expanduser("~"))
    assert r.url.endswith("report.txt"), r.url
    assert not _spells_a_machine_root(r.url), r.url
    assert user.lower() not in r.url.lower(), r.url


def test_the_masked_url_is_still_a_usable_uri(a_readable_file):
    """下游按 URL 解析它（`quality` 取 path 判扩展名与登录墙信号），形状不能坏。"""
    r = _parse(file_path=str(a_readable_file))
    parsed = urlparse(r.url)
    assert parsed.scheme == "file", r.url
    assert parsed.path.endswith("report.txt"), r.url


# ─── 3 · 遮蔽不许碰到真读文件的那条路 ───────────────────────────

def test_the_real_path_still_reaches_the_filesystem(a_readable_file):
    """只遮回执。真去读的还是那个绝对路径 —— 正文与字节数都得对得上。"""
    r = _parse(file_path=str(a_readable_file))
    assert r.status == 200
    assert "第一行" in "\n".join(r.content), r.content
    assert r.total_size_bytes == a_readable_file.stat().st_size


def test_the_suffix_decides_on_the_real_name_not_the_masked_one(a_readable_file):
    """`_PARSE_CONTENT_TYPES.get(Path(target).suffix)` 吃的是真路径。

    遮蔽器把根换成一个带尖括号的名字，如果顺手把它接到 suffix 那一步，`.txt` 就没了
    —— 这条用例把它钉住：扩展名从名字里读得出来，与根是谁无关。
    """
    csv = a_readable_file.with_suffix(".csv")
    csv.write_text("a,b\n1,2\n", encoding="utf-8")
    r = _parse(file_path=str(csv))
    assert r.status == 200 and r.content_type == "text/csv", r.content_type


# ─── 4 · 两条路说的是同一件事 ───────────────────────────────────

def test_both_branches_name_a_root_the_same_way(tmp_path):
    """同一个根在成功与失败两条路上必须是同一个名字。

    这正是本仓库反复点名的那类缺陷（"同一件事在两条路上两种说法"）：一处写
    `<server cwd>`、另一处写 `~\\...\\<server cwd>`，读的人会以为那是两个地方。

    判据不许写成"那一格一定是 `~`"（评审 R02-090 的教训）：那个名字由**这个根今天住在哪个盘**
    决定 —— 主目录下是 `~`，别的根就是它自己的拼法。两条路一致这件事与它一致与否无关，
    所以这里比的是"两条路用的是不是同一个名字"。
    """
    good = tmp_path / "report.txt"
    good.write_text("x\n", encoding="utf-8")
    ok = _parse(file_path="report.txt", cwd=str(tmp_path))
    bad = _parse(file_path="missing.txt", cwd=str(tmp_path))
    assert ok.status == 200, ok.error
    assert bad.error.startswith("File not found"), bad.error

    user = os.path.basename(os.path.expanduser("~"))
    #: 两条路要认的是**同一个名字**，而不是"那个名字一定是 `~`"。遮蔽之后的调用方根就是
    #: 那个名字 —— 成功格把它写成 POSIX 拼法、失败格写成反斜杠拼法（这一条差异本身记在
    #: R02-090 的 repro 里），所以这里按分隔符归一之后再比。
    def _norm(s: str) -> str:
        return s.replace("\\", "/").lower()

    named = _norm(paths.mask_machine_paths(str(tmp_path)))
    assert named and named in _norm(ok.url), ok.url
    assert named in _norm(bad.error), bad.error
    # 反过来：两条路上都不许出现机器自己的拼法，也不许出现身份
    assert not _spells_a_machine_root(ok.url), ok.url
    assert not _spells_a_machine_root(bad.error), bad.error
    assert user.lower() not in (ok.url + bad.error).lower(), (ok.url, bad.error)


# ─── 5 · 那两格下游不许被这次修补改坏 ───────────────────────────

def test_the_path_advice_survives_the_masking():
    """`quality` 按 `startswith("File not found")` 认这一格，而遮蔽发生在句子内部。

    原本这一格钉的是 `"cwd=" in next_action`。R02-062 之后那句出路只有一位作者（server 按
    `os.path.isabs` 写一次，进 `error`），下游改为指回 `error` 而不复述 —— 所以这里钉的
    还是同一件事（那句出路没被遮蔽改动），只是去它现在住的地方钉，并且钉的是**整句原文**：
    这一发传的是相对写法，两句出路必须逐字还在。
    """
    r = _parse(file_path="definitely-missing-d11.docx")
    assert (r.diagnosis or {}).get("code") == "file_not_found", r.diagnosis
    assert "Pass an absolute path, or pass cwd=<working directory> so relative paths " \
           "resolve against it." in r.error, r.error
    assert "error" in r.next_action.lower(), r.next_action
    assert "PATH was refused" not in r.next_action, r.next_action


def test_a_path_the_caller_invented_outside_every_root_reads_back(tmp_path):
    """不过度遮蔽：本机根之外的路径是调用方自己写的，回执里原样读得回来。

    这里用 `_masked_file_uri` 直接问，因为要在 e2e 里造一个"既不在 cwd 下也不在主
    目录下"的真实文件，得往盘根写东西 —— 那是测试不该做的事。
    """
    from dhole_mcp import server as server_mod
    foreign = (r"D:\somewhere\else\report.csv" if os.name == "nt"
               else "/srv/data/report.csv")
    assert server_mod._masked_file_uri(foreign) == Path(foreign).as_uri()
