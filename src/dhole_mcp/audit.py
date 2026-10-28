"""Dhole 的本地调用日志：`DHOLE_USAGE_LOG` 那一面，以及只往这里发的安全事件。

从 server.py 原样搬出（17.0 B6 的第五刀，零行为变更）。三颗函数是一份日志的两个写入口：
`_log_tool_call` 记每次调用，`_audit_event` 记 CDP attach 这类"成不成都该看见"的事件，
`_result_error` 把工具自己报的失败从响应里挑出来喂给前两者。它们必须待在一起，
因为共用同一份纪律：默认关、只落本机、不记参数值、best-effort 永不抛。

`_log_query` 是**另一份**日志（`DHOLE_QUERY_LOG`）：调用日志永远不记参数值，而这
一条刻意只记查询词——它是真实查询评测的原料，且同样默认关闭、只落本机、绝不联网。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from dhole_mcp import env_registry, paths
from dhole_mcp.security import redact_api_key

#: 调用日志的滚动上限：超过就把当前文件整体滚到 ``.1``（只留一代）。
#: 这份日志是诊断用的，不是账本 —— 无界追加会让一个只读工具悄悄吃掉磁盘，
#: 而它默认是关的，用户开了之后不会有任何东西提醒他文件在长。
#: 只对 dhole 自己拥有的文件（``DHOLE_USAGE_LOG=1``）生效：用户点名的路径
#: 归用户管，不动他的布置。
USAGE_LOG_MAX_BYTES = 20 * 1024 * 1024


def _rotate_owned_log(target: str) -> None:
    """超过上限就把 ``target`` 滚到 ``target.1``，best-effort 绝不抛。

    只留一代：够回答"最近发生了什么"，又不至于让磁盘长胖。``os.replace``
    在 Windows 上若目标被别的进程打开会失败，那种情况保持原样继续追加 ——
    丢日志比抛异常好，这条纪律与两个写入口一致。
    """
    try:
        if os.path.getsize(target) < USAGE_LOG_MAX_BYTES:
            return
        os.replace(target, target + ".1")
    except OSError:
        pass


def _audit_event(event: str, **fields: Any) -> None:
    """把安全相关事件（CDP attach 尝试）写进选开的本地审计通道。

    与 ``_log_tool_call`` 同一个文件（DHOLE_USAGE_LOG）、同一份纪律：选开、
    只落本机、best-effort、绝不抛出。attach 尝试无论成败都记 —— 被拒绝的
    attach 恰恰是日志里最值得看见的事件。
    """
    target = (env_registry.value_of("DHOLE_USAGE_LOG") or "").strip()
    if not target:
        return
    owned = target.lower() in ("1", "true", "yes", "on")
    if owned:
        target = str(paths.file("usage.jsonl"))
    try:
        parent = os.path.dirname(os.path.abspath(target))
        if parent:
            os.makedirs(parent, exist_ok=True)
        entry: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
        }
        entry.update(fields)
        if owned:
            _rotate_owned_log(target)
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if owned:
            paths.harden_dir(parent)
            paths.harden_file(target)
    except Exception:
        pass


def _result_error(result: Any) -> str:
    """工具自己报出的应用层错误；没报则返回 ``""``。

    调用日志里的 ``ok`` 含义是"调用跑到结束没有抛异常"—— 一个用信封回
    ``error`` 的工具从不抛异常，没有这颗函数，日志就会给明显失败的调用记上
    ``ok: true``。实测：一次被 SSRF 守卫拒绝的 ``feed_read`` 记成了
    ``{"tool": "feed_read", "ok": true, "ms": 22.0}``，拒绝的痕迹一丝都没有。
    这份日志要回答的问题是"我的客户端到底有没有路由到这里、它撑不撑得住？" ——
    而一次读起来像成功的拒绝，正是这个问题扛不住的形状。

    刻意做成加法：``ok`` 保持原义（批量里带一条失败 URL 仍算完成的调用），
    工具自报的错误落在既有的 ``error`` 字段里，于是 ``select(.error)`` 能找出
    每一次工具自己称为失败的调用。
    """
    payload: Any = result
    if isinstance(result, tuple) and len(result) == 2:
        payload = result[1]
    rows = payload if isinstance(payload, list) else [payload]
    for row in rows:
        if isinstance(row, dict):
            err = row.get("error")
            if err:
                return str(err)[:200]
    return ""


def _log_tool_call(name: str, ok: bool, duration_ms: float, error: str = "") -> None:
    """往选开的本地调用日志追加一行（DHOLE_USAGE_LOG=1 或一个路径）。

    默认关闭，且永不离开本机 —— 不上传任何东西。它存在是因为这类工具的头号
    失败模式是无声的：agent 根本没调用它（或调了却无视答案），而"我的客户端
    到底有没有路由到这里、它撑不撑得住？"这个问题没有本地记录就无从回答。
    参数**值**从不写入，只记工具名和结果。``ok`` 的含义是"调用跑到结束没有抛
    异常"—— 不是"工具报告成功"：被拒绝或失败的调用用信封作答而非异常，所以
    工具自己称为失败的会被记进 ``error``（见 ``_result_error``）。干净调用是
    ``{"tool": ..., "ok": true, "ms": ...}``，没有 ``error`` 键；完成但报告了
    失败的调用两者都有，于是 ``select(.error)`` 能找出每一次。Best-effort：
    绝不抛出，绝不阻塞启动。

    当日志放在 dhole home 下（``=1`` 形式）时，home 与日志像其他状态文件一样
    收紧到 0700/0600。用户通过环境变量自选的路径则原样保留，不动他的布置。
    """
    target = (env_registry.value_of("DHOLE_USAGE_LOG") or "").strip()
    if not target:
        return
    owned = target.lower() in ("1", "true", "yes", "on")
    if owned:
        target = str(paths.file("usage.jsonl"))
    try:
        parent = os.path.dirname(os.path.abspath(target))
        if parent:
            os.makedirs(parent, exist_ok=True)
        entry: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "tool": name,
            "ok": bool(ok),
            "ms": round(float(duration_ms), 1),
        }
        if error:
            entry["error"] = redact_api_key(str(error)[:200])
        if owned:
            _rotate_owned_log(target)
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if owned:
            paths.harden_dir(parent)
            paths.harden_file(target)
    except Exception:
        pass


def _log_query(query: str, *, mode: str = "", max_results: int = 0,
               results: int = 0, engines: list[str] | None = None,
               rerank_model: str = "") -> None:
    """把一次真实搜索的查询词写进选开的本地语料库（DHOLE_QUERY_LOG）。

    这是调用日志之外的**第二份**本地日志，因为两份要回答的问题相反：
    ``DHOLE_USAGE_LOG`` 只记"谁被调了、成没成"，参数值一概不写；这一条刻意
    只记查询词——真实查询是评测重排器的唯一可信原料，67 题冻结语料的分辨率
    不够（计划 §2.3：只能分辨 8.6 pp 以上的差异，而模型间差 0.6 pp）。

    纪律：默认关闭、只落本机、明文、不联网；只记查询词与最少元数据
    （模式 / 结果数 / 引擎名 / 重排模型），不记结果、URL、摘要。``=1`` 写
    ``~/.dhole/queries.jsonl``（目录 0700、文件 0600、20 MB 滚动），其它非空值
    按用户给的路径原样写，不动他的布置。Best-effort，绝不抛出。
    """
    raw = (env_registry.value_of("DHOLE_QUERY_LOG") or "").strip()
    if not raw or raw.lower() in env_registry.OFF_TOKENS:
        return
    text = (query or "").strip()
    if not text:
        return
    owned = raw.lower() in env_registry.ON_TOKENS
    target = str(paths.file("queries.jsonl")) if owned else raw
    try:
        parent = os.path.dirname(os.path.abspath(target))
        if parent:
            os.makedirs(parent, exist_ok=True)
        entry: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "query": redact_api_key(text),
            "mode": mode,
            "max_results": int(max_results),
            "results": int(results),
            "engines": list(engines or []),
            "rerank_model": rerank_model,
        }
        if owned:
            _rotate_owned_log(target)
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if owned:
            paths.harden_dir(parent)
            paths.harden_file(target)
    except Exception:
        pass
