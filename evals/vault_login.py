"""凭据来源（vault）+ 独立登录流端到端场景 —— 计划第七章。

单元测试（``tests/test_vault.py`` / ``tests/test_login_flow.py``）钉的是语义；
这个脚本走**完整工具调用链**，验的是那些语义在真页面 + 真 CLI 边界上还成立：

1. `vault(op='status')` 认得装在 PATH 上的管理器，并报出锁没锁；
2. `vault(op='match')` 按主机找到凭据，**响应里没有密码值**；
3. `login` 不带 confirm 时**一个字节都不填**，只回计划（含识别出的字段索引）；
4. `login` 带 confirm 时填 + 提交，`logged_in` 为真、cookie 名回得来，
   **响应里依然没有密码值**；
5. 登录态真的留住了：换回 HTTP 层、带同一个 session_id 取登录后的页面能拿到；
6. `confirm=true` 但没给 session_id 时**在开浏览器之前**被拒（共享会话不能承接
   登录态）。

CLI 边界用一个**真的假 `bw`**（临时目录里生成的可执行脚本 + 临时 PATH），所以
`vault._run` → 子进程 → JSON 解析这条链是真的在跑，不是被 monkeypatch 掉的。

    python evals/vault_login.py          # 需要靶站在 127.0.0.1:8399
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8399"

SECRET = "eval-secret-P@ssw0rd"
USERNAME = "evaluser"

# 一次性的 DHOLE_HOME：这个脚本会写 cookie jar，绝不能落在用户的 ~/.dhole 里。
os.environ["DHOLE_HOME"] = tempfile.mkdtemp(prefix="dhole-vault-")
os.environ["DHOLE_VAULT"] = "1"
os.environ["DHOLE_IGNORE_ROBOTS"] = "1"
sys.path.insert(0, str(HERE.parent / "src"))

from dhole_mcp.server import MasterFetchServer  # noqa: E402

PASS, FAIL = "通过", "**未通过**"
results: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"[{PASS if ok else FAIL}] {name}\n    {detail}")


# ─── 一个真的假 `bw` ─────────────────────────────────────────────────────

_FAKE_BW_PY = '''\
import json, sys
ITEM = {{
  "id": "eval-item-1",
  "name": "fixture",
  "login": {{
    "username": "{user}",
    "password": "{secret}",
    "uris": [{{"uri": "{base}/"}}],
  }},
}}
args = sys.argv[1:]
if args[:1] == ["status"]:
    print(json.dumps({{"status": "unlocked"}})); raise SystemExit(0)
if args[:2] == ["list", "items"]:
    print(json.dumps([ITEM])); raise SystemExit(0)
if args[:2] == ["get", "item"]:
    print(json.dumps(ITEM)); raise SystemExit(0)
print("fake bw: unsupported args " + repr(args), file=sys.stderr)
raise SystemExit(2)
'''


def install_fake_bw() -> Path:
    """Put an executable `bw` on PATH so vault talks to a real subprocess."""
    d = Path(tempfile.mkdtemp(prefix="dhole-fakebw-"))
    script = d / "fake_bw.py"
    script.write_text(
        _FAKE_BW_PY.format(user=USERNAME, secret=SECRET, base=BASE),
        encoding="utf-8")
    py = sys.executable
    if os.name == "nt":
        shim = d / "bw.cmd"
        shim.write_text(f'@echo off\r\n"{py}" "{script}" %*\r\n',
                        encoding="ascii")
    else:
        shim = d / "bw"
        shim.write_text(f'#!/bin/sh\nexec "{py}" "{script}" "$@"\n',
                        encoding="ascii")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP
                   | stat.S_IXOTH)
    os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    return d


def _blob(*objs) -> str:
    from dhole_mcp.server import _wire_json

    parts = []
    for o in objs:
        if o is None:
            continue
        parts.append(o.model_dump_json() if hasattr(o, "model_dump_json")
                     else json.dumps(o, ensure_ascii=False, default=str))
        if hasattr(o, "model_fields"):
            parts.append(_wire_json(o)[0])
    return "\n".join(parts)


async def main() -> int:
    fake = install_fake_bw()
    print(f"靶站 {BASE}　·　假 bw {fake}　·　库 {os.environ['DHOLE_HOME']}\n")
    s = MasterFetchServer()

    # 1. 状态
    st = await s.vault(op="status", source="bitwarden")
    row = (st.sources or [{}])[0]
    record("vault 认得 PATH 上的管理器并报出未锁定",
           row.get("available") is True and row.get("unlocked") is True,
           f"sources={st.sources}")

    # 2. 按主机匹配
    m = await s.vault(op="match", url=f"{BASE}/login", source="bitwarden")
    blob = _blob(m)
    # 密码值绝不出现；用户名**按设计**会出现（标识符，不是秘密 —— 见 vault.py
    # 的模块 docstring）。这条只钉前者。
    record("vault(op='match') 按主机找到凭据，且响应里没有密码值",
           m.total == 1 and SECRET not in blob
           and m.items[0]["has_password"] is True,
           f"total={m.total} host={m.host!r} 含密码值={SECRET in blob} "
           f"（用户名按设计会回：{USERNAME in blob}）")

    # 3. 审批闸：不带 confirm
    plan = await s.login(url=f"{BASE}/login", credential="fixture",
                         source="bitwarden", session_id="evalsess",
                         allow_private=True)
    pblob = _blob(plan)
    record("login 不带 confirm 时一个字节都不填，只回计划",
           plan.pending_approval is True and plan.filled == 0
           and plan.submitted is False
           and bool((plan.fields or {}).get("password"))
           and SECRET not in pblob,
           f"pending={plan.pending_approval} filled={plan.filled} "
           f"fields={plan.fields} error={plan.error!r} 含密码值={SECRET in pblob}"
           f"\n    plan: {plan.plan}")

    # 4. 确认执行
    got = await s.login(url=f"{BASE}/login", credential="fixture",
                        source="bitwarden", session_id="evalsess",
                        allow_private=True, confirm=True, timeout=60000)
    gblob = _blob(got)
    record("login 带 confirm 时填 + 提交，判定已登录，响应里没有密码值",
           got.submitted is True and got.logged_in is True
           and "sid" in (got.cookie_names or [])
           and SECRET not in gblob and USERNAME not in gblob,
           f"submitted={got.submitted} filled={got.filled} "
           f"logged_in={got.logged_in} cookies={got.cookie_names} "
           f"error={got.error!r} 含密码值={SECRET in gblob}\n"
           f"    message: {got.message}")

    # 5. 登录态真的留住了：回到 HTTP 层，同一个 session_id
    after = await s.smart_fetch(url=f"{BASE}/account", session_id="evalsess",
                                cache_ttl=0, allow_private=True)
    text = "\n".join(after.content or [])
    record("登录态留住了：同 session_id 取登录后页面拿到登录版内容",
           "欢迎回来" in text,
           f"content_ok={after.content_ok} 正文片段={text.strip()[:60]!r}")

    # 6. 没点名会话就不许执行
    refused = await s.login(url=f"{BASE}/login", credential="fixture",
                            source="bitwarden", allow_private=True, confirm=True)
    record("confirm=true 但没给 session_id 时被拒",
           bool(refused.error) and "session_id" in refused.error
           and refused.submitted is False,
           f"error={refused.error!r}")

    ok = sum(1 for _, o, _ in results if o)
    print(f"\n===== {ok}/{len(results)} 通过 =====")
    for name, o, _ in results:
        if not o:
            print(f"  未通过：{name}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
