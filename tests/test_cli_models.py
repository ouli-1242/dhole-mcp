"""`dhole models fetch` 的命令层：同步下载、进度、退出码与 JSON 回执。

网络与真实模型都不在这里：`reranker.fetch_model` 换成假回执。真下载的续传与
sha256 校验由 `test_reranker_models.py` / `test_the_model_download_can_resume.py`
守。
"""

from __future__ import annotations

import json

import pytest

from dhole_mcp import ops
from dhole_mcp import reranker

pytestmark = pytest.mark.smoke


def _report(ok: bool = True, already_present: bool = False,
            status: str = "downloaded", error: str = "") -> dict:
    return {
        "model": "bge-zh",
        "dir": "C:/tmp/models/bge-zh",
        "ok": ok,
        "already_present": already_present,
        "verified": "publisher sha256" if ok else "",
        "files": [
            {"name": "model.onnx", "path": "C:/tmp/models/bge-zh/model.onnx",
             "bytes": 278_825_308, "status": status},
            {"name": "tokenizer.json", "path": "C:/tmp/models/bge-zh/tokenizer.json",
             "bytes": 17_098_079, "status": "present"},
        ],
        "error": error,
        "duration_ms": 12.3,
    }


def test_models_fetch_downloads_and_reports_files(monkeypatch, capsys):
    seen = []

    def _fake(model=None, progress=None):
        seen.append((model, progress))
        return _report()

    monkeypatch.setattr(reranker, "fetch_model", _fake)
    assert ops._cmd_models(["fetch"]) == 0
    captured = capsys.readouterr()
    assert seen and seen[0][0] is None
    assert "model.onnx" in captured.out
    assert "downloaded" in captured.out
    assert "verified against the publisher sha256" in captured.out
    assert "ready:" in captured.out


def test_models_fetch_draws_progress_on_stderr(monkeypatch, capsys):
    def _fake(model=None, progress=None):
        progress("model.onnx", 50, 100)
        return _report()

    monkeypatch.setattr(reranker, "fetch_model", _fake)
    assert ops._cmd_models(["fetch"]) == 0
    captured = capsys.readouterr()
    assert "50%" in captured.err
    assert "50%" not in captured.out, "progress must never pollute stdout"


def test_models_fetch_quiet_suppresses_progress(monkeypatch, capsys):
    def _fake(model=None, progress=None):
        assert progress is None, "--quiet should not install a progress printer"
        return _report()

    monkeypatch.setattr(reranker, "fetch_model", _fake)
    assert ops._cmd_models(["fetch", "--quiet"]) == 0
    assert "50%" not in capsys.readouterr().err


def test_models_fetch_json_is_the_whole_receipt(monkeypatch, capsys):
    monkeypatch.setattr(reranker, "fetch_model",
                        lambda model=None, progress=None: _report())
    assert ops._cmd_models(["fetch", "--json"]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["model"] == "bge-zh" and payload["ok"] is True
    assert payload["files"][0]["status"] == "downloaded"
    assert not captured.out.startswith("  Dhole"), "no human banner under --json"


def test_models_fetch_failure_is_exit_one_and_names_the_file(monkeypatch, capsys):
    monkeypatch.setattr(
        reranker, "fetch_model",
        lambda model=None, progress=None: _report(
            ok=False, status="failed",
            error="bge-zh: download failed for model.onnx"))
    assert ops._cmd_models(["fetch"]) == 1
    captured = capsys.readouterr()
    assert "download failed for model.onnx" in captured.err
    assert "rerun the same command to resume" in captured.err


def test_models_refuses_an_unknown_model_without_calling_the_downloader(
        monkeypatch, capsys):
    def _explode(model=None, progress=None):
        raise AssertionError("no download should start for an unknown model")

    monkeypatch.setattr(reranker, "fetch_model", _explode)
    assert ops._cmd_models(["fetch", "--model", "bogus"]) == 2
    assert "--model must be one of" in capsys.readouterr().err


def test_models_needs_a_known_subcommand(monkeypatch, capsys):
    def _explode(model=None, progress=None):
        raise AssertionError("no download without fetch")

    monkeypatch.setattr(reranker, "fetch_model", _explode)
    assert ops._cmd_models([]) == 2
    assert "models needs a subcommand" in capsys.readouterr().err
    assert ops._cmd_models(["nope"]) == 2
    assert "unknown subcommand" in capsys.readouterr().err
    assert ops._cmd_models(["fetch", "extra"]) == 2
    assert "takes no positional arguments" in capsys.readouterr().err


def test_models_fetch_normalises_the_model_name(monkeypatch):
    seen = []

    def _fake(model=None, progress=None):
        seen.append(model)
        return _report()

    monkeypatch.setattr(reranker, "fetch_model", _fake)
    assert ops._cmd_models(["fetch", "--model", "MMARCO"]) == 0
    assert seen == ["mmarco"]


def test_models_help_is_answered_before_any_download(monkeypatch, capsys):
    def _explode(model=None, progress=None):
        raise AssertionError("help must not download")

    monkeypatch.setattr(reranker, "fetch_model", _explode)
    assert ops._cmd_models(["--help"]) == 0
    assert "dhole models fetch" in capsys.readouterr().out


def test_progress_marks_restart_for_each_file(capsys):
    """一个文件的 100% 不能把下一个文件的进度门槛顶到 110%。"""
    progress = ops._DownloadProgress()
    progress("model.onnx", 100, 100)
    progress("tokenizer.json", 100, 100)
    progress.close()
    err = capsys.readouterr().err
    assert "model.onnx" in err and "tokenizer.json" in err
