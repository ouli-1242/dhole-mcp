"""端到端：CLI 不开 MCP 也拿得到内容。

真子进程（`python -m dhole_mcp`，源码在 PYTHONPATH 上）。读路径走真网络
（example.com，一次取回后两条用例共用缓存）；guide / doc / state / creds 是纯
本地的，不联网也要能跑。e2e 默认不在 CI 里跑，手工用 `pytest -m e2e` 验。
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.e2e

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
URL = "https://example.com"
# 工具把 max_content_chars 钳到 [500, 200000]；剩余不足 MIN_CHUNK_CHARS（500）
# 时又会整页并回。所以点 500 实际最多拿 1000，点 200 也是这条路径。
CAP = 500
MAX_DELIVERED = CAP + 500


def _run(home: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith("DHOLE_")}
    env["DHOLE_HOME"] = str(home)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env["NO_COLOR"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "dhole_mcp", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, env=env)


@pytest.fixture(scope="module")
def fetched(tmp_path_factory):
    """一次真抓取，两条读用例共用（第二次命中缓存）。

    网络不通/被拦时 skip 而不是红：这套 e2e 验的是 CLI，不是当天的网络。
    """
    home = tmp_path_factory.mktemp("cli-read-home")
    proc = None
    for attempt in range(2):
        proc = _run(home, "read", URL, "--max-chars", str(CAP))
        if proc.returncode == 0:
            return home, proc
        if attempt == 0:
            time.sleep(2)
    pytest.skip(f"example.com is unreachable from this machine: {proc.stderr[:300]}")


def test_read_keeps_stdout_for_the_page_and_stderr_for_the_receipt(fetched):
    _, proc = fetched
    assert proc.returncode == 0, proc.stderr
    assert "This domain is for use in documentation examples" in proc.stdout
    assert 0 < len(proc.stdout.strip()) <= MAX_DELIVERED, proc.stdout
    assert "example.com" in proc.stderr
    assert " chars" in proc.stderr


def test_json_is_the_whole_receipt(fetched):
    home, _ = fetched
    proc = _run(home, "read", URL, "--max-chars", str(CAP), "--json")
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["content_ok"] is True, payload.get("error")
    assert payload["url"].startswith(URL)
    assert proc.stderr.strip() == ""


def test_guide_serves_the_bundled_handbook(tmp_path):
    proc = _run(tmp_path / "home", "guide", "start")
    assert proc.returncode == 0, proc.stderr
    assert "start" in proc.stdout.lower()
    assert len(proc.stdout) > 500


def test_doc_parses_a_local_file_without_a_network(tmp_path):
    src = tmp_path / "note.md"
    src.write_text("# Title\n\nHello CLI doc.\n", encoding="utf-8")
    proc = _run(tmp_path / "home", "doc", str(src))
    assert proc.returncode == 0, proc.stderr
    assert "# Title" in proc.stdout and "Hello CLI doc." in proc.stdout
    assert "parse" in proc.stderr


def test_state_and_creds_read_an_empty_home(tmp_path):
    state = _run(tmp_path / "home", "state")
    assert state.returncode == 0, state.stderr
    assert "cache:" in state.stdout
    assert "Nothing is open" in state.stderr

    creds = _run(tmp_path / "home", "creds")
    assert creds.returncode == 0, creds.stderr
    assert "Nothing is stored" in creds.stderr


def test_help_commands_and_completion_need_no_network(tmp_path):
    home = tmp_path / "home"
    helped = _run(home, "help", "read")
    assert helped.returncode == 0, helped.stderr
    assert "--nested-selector" in helped.stdout
    assert "exit codes:" in helped.stdout

    catalog = _run(home, "commands", "--json")
    assert catalog.returncode == 0, catalog.stderr
    data = json.loads(catalog.stdout)
    names = {c["name"] for group in ("commands", "ops", "meta")
             for c in data[group]}
    assert names >= {"read", "search", "call"}

    completion = _run(home, "completion", "bash")
    assert completion.returncode == 0, completion.stderr
    assert "complete -F _dhole dhole" in completion.stdout


def test_generic_call_uses_the_same_read_path(fetched):
    home, _ = fetched
    proc = _run(
        home, "call", "web_read",
        "--args", json.dumps({"url": URL, "max_content_chars": 200}))
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["content_ok"] is True, payload.get("error")
    assert "This domain is for use in documentation examples" in "".join(payload["content"])


def test_global_json_and_ndjson_shapes(fetched):
    home, _ = fetched
    before = _run(home, "--json", "read", URL, "--max-chars", "200")
    assert before.returncode == 0, before.stderr
    assert json.loads(before.stdout)["content_ok"] is True

    rows = _run(home, "read", URL, URL, "--ndjson")
    assert rows.returncode == 0, rows.stderr
    lines = [json.loads(line) for line in rows.stdout.splitlines()]
    assert [line["type"] for line in lines] == ["page", "page"]
    assert all(line["content_ok"] for line in lines)


def test_quiet_leaves_stderr_empty(fetched):
    home, _ = fetched
    proc = _run(home, "read", URL, "--max-chars", "200", "--quiet")
    assert proc.returncode == 0, proc.stderr
    assert "This domain is for use in documentation examples" in proc.stdout
    assert proc.stderr.strip() == ""
