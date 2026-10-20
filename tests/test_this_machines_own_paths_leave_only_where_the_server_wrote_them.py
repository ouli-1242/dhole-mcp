# -*- coding: utf-8 -*-
"""这台机器自己的路径：服务器**拼出来**的格子必须遮，调用方**自己写的**那格必须原样（R02-048 那条轴收口 · 2026-10-18 · r223c）.

普查是把这条轴**反过来做**的（`review/.runtime/p1/r223c_mask_outlet_census.py` · 19 面 × 递归扫整副
回执的每一个字符串 · HEAD 762d0f3 · server pid 22828 · 回环门开→量→关→读回「Nothing is stored」）。
读数分成两半，界限干净得没有一格骑墙：

| 那一格里的字节是谁写的 | 面 | 读数 |
| --- | --- | --- |
| **服务器拼的**（`doc_parse` 的 `url = "file://" + …`、`File not found` 那句、目录面、两根之外的用户名段、成功面与拒绝面各 4 发） | 8 发 | **全部遮过**：`file://~\\…`、`file://<server cwd>\\…`、`probe-of-<user>` |
| **调用方自己写的**（`web_read.url` / `web_crawl.start_url` / 逐行 `pages[].url` / `feeds[].source_url`，成功面与拒绝面都有） | 10 发 | **全部原样回声**：`url <- HOME`、`start_url <- USER-SEG` …

⇒ 判据不是「回执里不许出现本机路径」，而是**「这台机器的自我信息只许由服务器自己交出去，而它交出的
每一格都遮过」**。`url` 那一格答的是「这一发去看的是哪个地址」，那是调用方上一条消息里自己打的字：
把它遮成别的样子，既不减少档案里已有的信息（他刚亲手写过），又让回执对不上他发的那一发 ——
`pages[].url` 正是逐行对上页的唯一把手。**这条判决写在** `review/decisions.md`（2026-10-18 · r223c 的判决）。

这一把尺两头都咬：
  * 遮的那一半 —— 若哪天有人把 `mask_machine_paths` 摘掉或漏一个新格子，第一组立刻红（本文件落地时
    当场验过：把遮蔽器换成恒等函数 ⇒ 三格全红）；
  * 回声的那一半 —— 若哪天有人「为了隐私」把请求身份也遮掉，第二组红（那是一句关于「你问了什么」的假话）。
还有一颗反例钉住**不许遮过头**：调用方自己编的、不属于这两根的绝对路径（`D:\\qoder-r224-elsewhere\\…`，
本文件现场造现场删）必须原样交回 —— 那是他的地址，不是这台机器的。

顺带记一笔量具自己的错（A 层，档案留着）：r223 第一版把用户名针写成 `/<user>/`（前后都要分隔符），
而 `web_crawl` 会把起点规范化掉尾斜杠（`…/users/ouli`），于是那一格被判成 clean —— 针看不见**结尾那段**；
同一个形状也看不见 `probe-of-<user>` 与 `%2F<user>%2F`。r223c 改成按段认，本文件再改成与遮蔽器同族的
**整串认**（`_identity_names` 就是把这个名字 wherever it sits 换掉）—— 针必须与被测对象的规矩同族，
否则「没报红」会被读成「都干净」（B-79）。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile

import pytest

from dhole_mcp import paths as paths_mod
from dhole_mcp.server import MasterFetchServer

HOME = os.path.expanduser("~")
CWD = os.getcwd()
USER = (os.environ.get("USERNAME") or os.path.basename(HOME)).strip()
#: 用户名按**子串**数 —— 与遮蔽器自己的规矩同族（`_identity_names` 是整串替换，
#: 「出现在任何一段里都要折」，R02-090）。r223 那把针要求尾分隔符，于是看不见
#: `probe-of-<user>` 与 `%2F<user>%2F` 这两种形状，把假绿当成了读数（B-79）。
USER_SEG = re.compile(re.escape(USER), re.I)
FOREIGN = "D:\\qoder-r224-elsewhere\\report.csv"   # 不属于这两根，也不是别台机器的根：调用方自己编的


def _needles(node, path=""):
    out = []
    if isinstance(node, dict):
        for k, v in node.items():
            out += _needles(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += _needles(v, f"{path}[{i}]")
    elif isinstance(node, str):
        low = node.lower()
        for root in (HOME, HOME.replace("\\", "/"), CWD, CWD.replace("\\", "/")):
            if root and root.lower() in low:
                out.append((path, root))
                break
        if USER_SEG.search(node):
            out.append((path, f"/{USER}/"))
    return out


def _dump(obj) -> dict:
    return json.loads(json.dumps(
        {k: v for k, v in obj.model_dump().items() if k not in ("content",)},
        default=str))


def _local(path: str):
    return asyncio.run(MasterFetchServer().doc_parse(path))


def test_a_file_under_the_home_reads_back_with_the_home_folded(tmp_path):
    """成功面：`doc_parse` 的 `url` 由服务器拼，家目录那一段不许跟着出门（F25 那格的正面）。"""
    p = os.path.join(HOME, "qoder-r224-probe.txt")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("AAA1 BBB2 CCC3 DDD4 EEE5 FFF6\n" + "q" * 60)
    try:
        r = _local(p)
        assert not r.error, r.error
        assert _needles(_dump(r)) == [], _needles(_dump(r))
        assert "~" in (r.url or ""), r.url
    finally:
        os.remove(p)


def _probe_base_outside_both_roots():
    """挑一个「两根之外」的落脚处，并记下哪一层是我们自己造的（B-82）。

    这一格的判据要求用户名住在**两根之外**（R02-090 那根针测的就是这件事），所以不能简单挪进
    `tmp_path`：本仓跑套件时 `TMP` 常指着家目录底下，那颗名字就落回两根之一，针于是量不到它本来
    要量的东西。从前这里写死 `D:\\tmp\\qoder-r224\\probe-of-<user>`，读数是对的，但它**跑完不删**
    —— 与本仓 conftest 那条「测试不许在别人的机器上留东西」同一条轴。现在两个都要：造出来的层
    由调用方在 finally 里收回去。
    """
    roots = [os.path.realpath(r).lower() for r in (HOME, CWD)]

    def outside(p):
        real = os.path.realpath(p).lower()
        return not any(real.startswith(root) or root.startswith(real) for root in roots)

    cands = [tempfile.gettempdir()]
    home_drive = os.path.splitdrive(os.path.realpath(HOME))[0].rstrip(":\\").upper()
    for letter in ("D", "E", "F", "G"):
        if f"{letter}:" != home_drive and os.path.exists(f"{letter}:\\"):
            cands.append(f"{letter}:\\dhole-test-probe")
            break
    for cand in cands:
        if not cand or not outside(cand):
            continue
        if os.path.isdir(cand):
            return cand, False
        try:
            os.makedirs(cand, exist_ok=True)
        except OSError:
            continue
        return cand, True
    return None, False


def test_a_username_segment_outside_both_roots_is_folded_on_the_success_face():
    """R02-090 那根针在**成功面**也成立：用户名住在两根之外（别的盘的临时根）也要折。"""
    base, created = _probe_base_outside_both_roots()
    if base is None:
        pytest.skip("这台机器上找不到既在两根之外、又能建的落脚处 —— 这一格量不到它要量的东西，"
                    "不许改判到家目录里")
    d = os.path.join(base, f"probe-of-{USER}")
    p = os.path.join(d, "y.txt")
    try:
        os.makedirs(d, exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("CCC3 DDD4 EEE5 FFF6\n" + "w" * 40)
        r = _local(p)
        assert not r.error, r.error
        assert _needles(_dump(r)) == [], _needles(_dump(r))
        assert "<user>" in (r.url or "").lower(), r.url
    finally:
        shutil.rmtree(d, ignore_errors=True)
        if created:
            try:
                os.rmdir(base)
            except OSError:
                pass


def test_a_missing_path_under_the_home_stays_folded_all_the_way_down():
    """拒绝面：`error` 遮过而 `url` 没遮，正是 R02-048 当年那一封的形状 —— 两头一起数。"""
    r = _local(os.path.join(HOME, "nope-224.docx"))
    assert str(r.error or "").startswith("File not found"), r.error
    assert _needles(_dump(r)) == [], _needles(_dump(r))


def test_a_path_that_is_not_this_machine_comes_back_verbatim():
    """不许遮过头：调用方自己编的地址（不属于这两根）原样交回 —— 遮它等于改他的问题。"""
    r = _local(FOREIGN)
    assert str(r.error or "").startswith("File not found"), r.error
    assert FOREIGN.replace("\\", "/") in (r.url or "").replace("\\", "/"), r.url
    low = (r.url or "").lower().replace("\\", "/")
    assert "qoder-r224-elsewhere/report.csv" in low, r.url
    assert "<server cwd>" not in low and "~" not in low, (
        f"调用方自己编的地址被遮过了: {r.url}")


@pytest.mark.parametrize("door,field", [
    ("web_read", "url"), ("web_crawl", "start_url"), ("web_discover", "start_url"),
])
def test_the_identity_field_answers_what_that_call_looked_at(door, field):
    """请求身份那一格交回调用方自己打的原文（判决的另一半：把它遮成别的样子是假话）。

    打的是 `file://` —— 网络门在拨号前就拒（scheme 闸），所以这一格不需要网络也不需要私网豁免，
    而 `url` 那一格照样要答「这一发去看的是哪个地址」。
    """
    target = "file:///" + os.path.join(HOME, "r224-identity.txt").replace("\\", "/")
    fn = getattr(MasterFetchServer(), door)
    r = asyncio.run(fn(target)) if door == "web_read" else asyncio.run(fn(url=target))
    got = str(getattr(r, field, "") or "")
    assert got.replace("\\", "/") == target.replace("\\", "/"), (door, field, got)
    assert "file" in str(r.error or "").lower(), r.error


def test_the_masker_is_the_single_author_of_the_folded_face():
    """归属锁（F93 通则）：把遮蔽器换成恒等函数，拼出来的那几格要跟着漏 —— 漏了才知道尺子在数谁。"""
    p = os.path.join(HOME, "qoder-r224-probe2.txt")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("AAA1 BBB2\n" + "e" * 60)
    original = paths_mod.mask_machine_paths
    try:
        paths_mod.mask_machine_paths = lambda text: text
        r = _local(p)
        leaked = _needles(_dump(r))
    finally:
        paths_mod.mask_machine_paths = original
    assert leaked, "恒等遮蔽器下这格居然没漏 ⇒ 这一格根本不是由遮蔽器写的（尺子数错了作者）"
    clean = _needles(_dump(_local(p)))
    assert clean == [], f"还原之后仍漏: {clean}"
