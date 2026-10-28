"""空闲关浏览器的秒数必须可调 —— 它曾经被写死，且旧旋钮被静默忽略。

常量在 import 期求值，所以这条契约只能在**全新进程**里验证：同一进程内
reload ``dhole_mcp.server`` 会换掉别的测试持有的类对象。这里用一个子进程
把五种输入一次问完，代价约一次 import。
"""


from __future__ import annotations


import pytest

import json
import os
import subprocess
import sys
from pathlib import Path

pytestmark = pytest.mark.smoke

REPO = Path(__file__).resolve().parents[1]


def test_the_idle_timeout_is_read_from_the_environment_with_a_floor_and_a_ceiling():
    script = r"""
import json, os, sys
cases = [None, "0", "60", "999999", "abc"]
out = {}
for raw in cases:
    if raw is None:
        os.environ.pop("DHOLE_BROWSER_IDLE_TIMEOUT", None)
    else:
        os.environ["DHOLE_BROWSER_IDLE_TIMEOUT"] = raw
    for name in [m for m in sys.modules if m.startswith("dhole_mcp")]:
        del sys.modules[name]
    from dhole_mcp.server import AUTO_SESSION_IDLE_TIMEOUT as value
    out["unset" if raw is None else raw] = value
print(json.dumps(out))
"""
    env = {k: v for k, v in os.environ.items() if k != "DHOLE_BROWSER_IDLE_TIMEOUT"}
    env["PYTHONPATH"] = str(REPO / "src")
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", script],
        capture_output=True, text=True, env=env, timeout=180, cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    got = json.loads(proc.stdout.strip().splitlines()[-1])

    assert got["unset"] == 1800, "默认值变了：调用间隔超过半小时就要重开浏览器"
    assert got["0"] == 0, "0 必须是合法值 —— 它表示永久保活"
    assert got["60"] == 60, "合法覆盖没有生效"
    assert got["999999"] == 86400, "超上限没有夹到一天"
    assert got["abc"] == 1800, "非法值必须回退到默认，而不是崩溃或变成 0"


def test_the_knob_is_declared_so_doctor_and_config_can_name_it():
    from dhole_mcp import env_registry

    entry = env_registry.by_name("DHOLE_BROWSER_IDLE_TIMEOUT")
    assert entry is not None, "未声明的旋钮不会出现在 dhole config / doctor 里"
    assert entry.default == "1800"
    assert entry.bounds == (0, 86400)
    assert entry.read_at == "import", "常量在 import 期求值，改完必须重启宿主"
