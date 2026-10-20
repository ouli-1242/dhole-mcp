r"""「这台机器的重排器装了没有」在 wire 上问得出吗 —— R02-054（P2 · 组 F）。

现场（P02-05 邱洛 · 券商行业研究员 L3 · S6 冷启动 · 存档
`sessions/P02-05-s11-cold-sys-config.json`）：冷 HOME 上连打两发检索之后专门打了一发
`sys_state(show=['config'])` 想搞清楚这台机器到底怎么了，回来的只有
「persisted config knobs 0 - none are persisted」+ keys `['message','next_action','open_count','sessions']`。
她想问的那件事——**这台机器的语义重排器装了没有、装的是哪一支、装上了为什么还排不了**——
在这颗工具的名册词表里没有对应的一类（`cache / engine_state / sessions / cred_defaults /
cdp_hosts / config / all`，一颗与模型有关的都没有）。

而产品**自己知道**：`reranker.model_present()` / `active_model()` / `unavailable_reason()`
都在（`reranker.py`），CLI 的 `dhole -v` 就在读它们。只用 MCP 的客户端没有那扇门。

先量到第二件事（本刀的落点因此不止"加一颗 token"）：**同一个问题今天有三处各算各的**。
- `reranker.model_present()`：`model.onnx` 存在 **且** 字节数 ≥ `min_bytes` **且**
  `tokenizer.json` 存在 —— 这是唯一会喂给 prewarm/`ensure_reranker` 的那一位。
- `updater.capabilities()`（`dhole -v` 的能力行）：只看两个文件**存不存在**，没有字节数那道闸。
- `updater._append_rerank_model()`（`dhole -v` 的体检行）：只看 `model.onnx` 存不存在，
  连 `tokenizer.json` 都不看。

于是「下载被截断」（onnx 在、半个文件、tokenizer 齐）这一格，CLI 的能力行说 `ready`，
而喂给加载那一侧的判决说**不在**；「半装」（onnx 齐、缺 tokenizer）那一格，体检行说 `ok`，
加载那一侧同样说不在。**两台机器上的两句话互相否认**（与 R02-042 那对"deps/模型状态"同族）。

契约（本文件钉住）：
  * 判决只有一位作者：`reranker.model_status()` 交回一个 `state`
    （`loaded` / `on_disk` / `not_downloaded` / `deps_absent`），CLI 与 wire 都从它说话 ——
    三处 `.exists()` 自理算术的那种形状不许回来；
  * 「装了没有」在 wire 上**点名问得出**：`sys_state(show=['models'])`；
  * 回答要说得出是哪一支（`model` 那颗名字），因为注册表里换过型号（17.0 B8 收敛成一颗）；
  * 没装那一支要说大概多少字节、以及"第一发 neural 检索会去取它" —— 那是这一格唯一的出路；
  * 这一进程的账（试过几次 / 那一次多少毫秒 / 记下过什么原因）一起交回：
    「盘上有但加载失败」与「盘上有但还没试过」是两种处方（F33 那把刀的主语在这里同样成立）；
  * 边界：这是本机信息 —— 磁盘路径**不许**上回执（与 R02-045/048/049 那一族同一道线；
    模型的名字可以给，`~/.dhole/models/<name>` 里那个 `<name>` 就是它全部的出身）；
  * `models` 是 show-only：dhole 不提供"抹掉模型"这一类动作，点名要抹要被拒，且**不许**
    复用 config 那句操作员侧出路（那是另一类东西的门）；
  * 与 `config` 同律：不进 `show=['all']` 的展开（`all` 说的是"这颗工具管得到的那几类"）。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from dhole_mcp import env_registry, reranker, updater
from dhole_mcp.server import MasterFetchServer

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
