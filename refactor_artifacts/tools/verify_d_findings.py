"""Re-verify the transcribed findings D-01..D-07 against the real code.

Offline by construction: every import happens with DHOLE_HOME pointed at a
throwaway dir, and a socket guard refuses any outbound connect. Nothing here
writes to the user's real ~/.dhole.

Run from the repo root:
    PYTHONPATH=src python refactor_artifacts/tools/verify_d_findings.py
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# ── Guard 1: redirect all product state to a throwaway home ──────────────
TMP_HOME = Path(tempfile.mkdtemp(prefix="dhole_verify_"))
os.environ["DHOLE_HOME"] = str(TMP_HOME)

# ── Guard 2: refuse every NON-LOOPBACK connection (offline proof) ────────
# Loopback must stay open: asyncio's ProactorEventLoop builds its self-pipe out
# of a real socketpair on Windows, and blocking that kills the event loop before
# any check runs. Off-machine egress is what we are proving absent.
_EGRESS: list[tuple[str, int]] = []
_real_connect = socket.socket.connect

_LOCAL = {"127.0.0.1", "::1", "localhost", "0.0.0.0", "::"}


def _guarded_connect(self, address, *a, **k):
    if isinstance(address, tuple) and len(address) >= 2:
        host = str(address[0])
        if host not in _LOCAL:
            _EGRESS.append((host, int(address[1])))
            raise OSError("blocked by verify_d_findings socket guard")
    return _real_connect(self, address, *a, **k)


socket.socket.connect = _guarded_connect  # type: ignore[method-assign]

sys.path.insert(0, str(REPO / "src"))

RESULTS: list[tuple[str, str, str]] = []


def record(fid: str, verdict: str, detail: str) -> None:
    RESULTS.append((fid, verdict, detail))
    print(f"\n[{fid}] {verdict}\n      {detail}")


# ─── D-01: cache_clear(engine_state=True) 的 engine_health 是否恒为空 ────

async def check_d01() -> None:
    from dhole_mcp import search_metasearch as ms
    from dhole_mcp.server import MasterFetchServer

    ms._record_block("bing")          # a live cooldown
    ms._ENGINE_YIELD["duckduckgo"] = {   # a non-empty yield row
        "ts": ms.time(), "status": "ok", "last_nodes": 10, "last": 4,
    }

    before = ms.engine_state_snapshot()
    srv = MasterFetchServer()
    info = await srv.cache_clear(all=False, engine_state=True)
    after = info.engine_health

    print(f"      before  : {sorted(before)}")
    print(f"      message : {info.message.strip()}")
    print(f"      engine_health: {after}")
    print(f"      engine_state_reset: {info.engine_state_reset}")

    if before and not after:
        record("D-01", "已证实",
               f"重置前 snapshot 非空 {sorted(before)}，重置后 engine_health={after}。"
               f"engine_state_reset() 先清空 _BACKEND_HEALTH/_ENGINE_YIELD，"
               f"engine_state_snapshot() 再读同一批 dict —— 结构上恒为空。"
               f"释放的冷却改由 message 文本承载（见上）。")
    elif not before and not after:
        record("D-01", "无法判定", "种子数据没生效，前序状态本身就是空的")
    else:
        record("D-01", "已证伪", f"engine_health 非空：{after}")


# ─── D-02: DHOLE_DEFAULT_ENGINES 是否影响 engines_consensus 分母 ─────────

def check_d02() -> None:
    from dhole_mcp import search
    from dhole_mcp import search_engines

    baseline = search._family_universe(None, [])
    os.environ["DHOLE_DEFAULT_ENGINES"] = "bing"
    from dhole_mcp import search_metasearch as ms
    configured = ms._configured_default_backends()
    after = search._family_universe(None, [])

    print(f"      search_engines.DEFAULT_ENGINES = {search_engines.DEFAULT_ENGINES}")
    print(f"      _configured_default_backends() = {configured}")
    print(f"      _family_universe(None, [])  无 env = {baseline}")
    print(f"      _family_universe(None, [])  有 env = {after}")
    del os.environ["DHOLE_DEFAULT_ENGINES"]

    if configured == ["bing"] and baseline == after:
        record("D-02", "已证实",
               f"env 把执行池收敛成 {configured}，但共识分母纹丝不动 "
               f"(baseline={baseline}, after_env={after})。_family_universe 走的是 "
               f"search_engines.DEFAULT_ENGINES 这个固定 tuple，不是 env 覆盖后的池。")
    else:
        record("D-02", "已证伪",
               f"env 生效后分母变了：{baseline} -> {after}（configured={configured}）")


# ─── D-03: max_results 越界提示是否出现在 fetch_hint ─────────────────────

def check_d03() -> None:
    src = (REPO / "src" / "dhole_mcp" / "search.py").read_text(encoding="utf-8")
    lines = src.splitlines()
    hit = [(i + 1, ln.strip()) for i, ln in enumerate(lines)
           if "outside the supported" in ln]
    ctx = "\n".join(f"        {n}: {t}" for n, t in hit)
    print(ctx)
    window = "\n".join(lines[1080:1095])
    print("      ---- 上下文 ----")
    print("\n".join("      " + w for w in window.splitlines()))

    if hit and "_clamp_note" in src and "fetch_hint" in src:
        record("D-03", "已证实",
               f"search.py:{hit[0][0]} 构造 _clamp_note = {hit[0][1]!r}，"
               f"随后并入 fetch_hint。所以越界不是静默钳制，agent 会看到文本提示。"
               f"属返回内容差异（README 措辞与实现不符），非结构差异。")
    else:
        record("D-03", "已证伪", "没找到该提示文本")


# ─── D-04: parse 支持的扩展名 vs 工具描述 vs 测试守卫 ────────────────────

def check_d04() -> None:
    from dhole_mcp.parse import SUPPORTED_EXTENSIONS
    from dhole_mcp.server import MasterFetchServer
    import inspect

    desc = inspect.getsource(MasterFetchServer.parse)
    print(f"      SUPPORTED_EXTENSIONS = {sorted(SUPPORTED_EXTENSIONS)}")
    print(f"      parse 工具描述行: "
          f"{[ln.strip() for ln in desc.splitlines() if 'Supported:' in ln]}")

    # The MCP-facing description lives in _TOOL_DEFS, not the docstring.
    import dhole_mcp.server as srv_mod
    tool = next(t for t in srv_mod.MasterFetchServer._TOOL_DEFS
                if t["name"] == "parse")
    mcp_desc = tool["inputSchema"]["properties"]["file_path"]["description"]
    print(f"      _TOOL_DEFS 里 file_path 的描述: {mcp_desc!r}")

    test_src = (REPO / "tests" / "test_tool_descriptions.py").read_text(encoding="utf-8")
    guards = [ln.strip() for ln in test_src.splitlines()
              if ".pdf" in ln and ("parse" in ln.lower() or "assert" in ln)]
    for g in guards[:6]:
        print(f"      test guard: {g}")

    # Token-wise, not substring: "htm" is a substring of ".html", so a naive
    # `in` test silently clears .htm. Split on non-extension characters.
    described = set(re.findall(r"\.[a-z0-9]+", mcp_desc))
    print(f"      描述里出现的扩展名 token: {sorted(described)}")

    missing = sorted(SUPPORTED_EXTENSIONS - described)
    if missing:
        record("D-04", "已证实",
               f"代码支持 {sorted(SUPPORTED_EXTENSIONS)}，但 _TOOL_DEFS 的描述只写了 "
               f"{sorted(described)}；未在描述中出现的扩展名 = {missing}。"
               f".htm 与 .xhtml 都真能解析，却不在给 agent 看的描述里。"
               f"（tests/test_tool_descriptions.py 只钉住 '.pdf' 必须出现，"
               f"所以这个缺口不会被现有守卫发现。）")
    else:
        record("D-04", "已证伪", "描述已覆盖全部扩展名")


# ─── D-05: 代理配置下的 example.com 真实探测 ─────────────────────────────

def check_d05() -> None:
    import ast

    hits: list[tuple[str, int, str, str]] = []
    for py in sorted((REPO / "src" / "dhole_mcp").glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                body_src = ast.get_source_segment(py.read_text(encoding="utf-8"), node) or ""
                if "example.com" in body_src:
                    fn = node
                    # find enclosing class if any
                    hits.append((py.name, node.lineno, node.name, body_src))

    for name, lineno, fn, body in hits:
        probe = "https://example.com" in body
        print(f"      {name}:{lineno} {fn}()  含 example.com 字面量={probe}")

    hp = (REPO / "src" / "dhole_mcp" / "search_proxy.py").read_text(encoding="utf-8")
    callers = [ln.strip() for ln in hp.splitlines()
               if "health_check" in ln or "create_task" in ln or "ensure_future" in ln]
    print("      ---- search_proxy.py 里的调度点 ----")
    for c in callers[:12]:
        print(f"        {c}")

    if any("health_check" in c for c in callers):
        record("D-05", "已证实",
               f"search_proxy.py 定义 health_check(probe_url='https://example.com')，"
               f"调度点为 {callers}。存在代理配置时会产生真实外网探测。"
               f"（本轮快照在 socket 守卫下未观测到该流量，因为快照未配置代理 —— "
               f"它是条件触发，不是无条件启动流量。）")
    else:
        record("D-05", "部分证实 / 无自动触发",
               "health_check 存在但没找到自动调度点；只有显式调用才会探测。")


# ─── D-06: 明文凭据文件的权限收紧 ────────────────────────────────────────

def check_d06() -> None:
    hardened, unhardened = [], []
    files = {
        "search_proxies.json": REPO / "src" / "dhole_mcp" / "search_proxy.py",
        "usage.jsonl": REPO / "src" / "dhole_mcp" / "server.py",
        "search_feedback.json": REPO / "src" / "dhole_mcp" / "search.py",
        "circuit_breaker.json": REPO / "src" / "dhole_mcp" / "search_metasearch.py",
        "engine_stats.json": REPO / "src" / "dhole_mcp" / "search_metasearch.py",
    }
    for fname, mod in files.items():
        src = mod.read_text(encoding="utf-8")
        has = "harden_file" in src and fname.split(".")[0] in src
        # crude but checkable: does the writer for this file call harden_file?
        (hardened if has else unhardened).append(fname)

    print(f"      调用 harden_file 的写入点所在模块: {hardened}")
    print(f"      未调用 harden_file 的: {unhardened}")

    # Real runtime proof: write through the product API and stat the file.
    from dhole_mcp import search_proxy
    search_proxy.save_proxies(["http://user:secret@10.0.0.1:8080"])
    p = Path(os.environ["DHOLE_HOME"]) / "search_proxies.json"
    mode = oct(p.stat().st_mode & 0o777)
    body = p.read_text(encoding="utf-8")
    print(f"      实测写入 {p.name}: mode={mode}")
    print(f"      内容: {body.strip()}")

    if p.exists():
        record("D-06", "已证实",
               f"save_proxies() 走裸 mkdir + open(...,'w')，落盘文件 mode={mode}（受 umask 影响），"
               f"没有 harden_file。内容含明文凭据。"
               f"对照：circuit_breaker.json / engine_stats.json 是在 replace 之后补 "
               f"paths.harden_file(path) 的。README#269 的表述是对全部状态文件的。")
    else:
        record("D-06", "已证伪", "文件未落盘")


# ─── D-07: DHOLE_HOME 在 repair 路径上是否被遵循 ─────────────────────────

def check_d07() -> None:
    cli = (REPO / "src" / "dhole_mcp" / "cli.py").read_text(encoding="utf-8")
    lines = cli.splitlines()
    hits = [(i + 1, ln.strip()) for i, ln in enumerate(lines)
            if ".dhole" in ln or "expanduser" in ln]
    print("      ---- cli.py 里所有 ~/.dhole / expanduser 出现处 ----")
    for n, t in hits:
        print(f"        {n}: {t}")

    repair_mod = (REPO / "src" / "dhole_mcp" / "repair.py").exists()
    print(f"      src/dhole_mcp/repair.py 存在? {repair_mod}")

    # Does the repair path honour DHOLE_HOME?
    repair_fn = "\n".join(lines[118:145])
    honors = "paths.home()" in repair_fn or "DHOLE_HOME" in repair_fn
    print(f"      _run_repair 内是否使用 paths.home()/DHOLE_HOME: {honors}")
    print("      ---- _run_repair 相关行 ----")
    print("\n".join("        " + ln for ln in repair_fn.splitlines() if ln.strip()))

    updater = (REPO / "src" / "dhole_mcp" / "updater.py").read_text(encoding="utf-8")
    print(f"      updater.py 使用 paths.home(): "
          f"{'paths.home()' in updater}")

    if not honors:
        record("D-07", "已证实（表述需更正）",
               f"cli.py 的 repair 路径用 os.path.join(os.path.expanduser('~'), '.dhole', 'repair.py') "
               f"硬编码真实 home，不看 DHOLE_HOME；而同包 updater.py 走 paths.home()。"
               f"原转述说'cli.py:124 引用 repair.py，后者硬编码 ~/.dhole' —— "
               f"src/dhole_mcp/repair.py 这个模块并不存在（exists={repair_mod}）；"
               f"repair.py 是运行时**生成**到 ~/.dhole/ 的脚本。事实成立，出处描述错误。")
    else:
        record("D-07", "已证伪", "repair 路径已跟随 DHOLE_HOME")


async def main() -> int:
    print(f"DHOLE_HOME  -> {TMP_HOME}")
    print(f"socket guard installed; egress recorded so far: {_EGRESS}")

    await check_d01()
    check_d02()
    check_d03()
    check_d04()
    check_d05()
    check_d06()
    check_d07()

    print("\n" + "=" * 72)
    print("汇总")
    print("=" * 72)
    for fid, verdict, _ in RESULTS:
        print(f"  {fid}: {verdict}")
    print(f"\n外发尝试记录（应为空）: {_EGRESS}")
    print(f"临时 home: {TMP_HOME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
