"""G6 回归：引擎池的退避要能调，冷却要有人主动去看。

两件事各自解决一个实测到的问题：

1. **退避写死在源码里。** 60s / 600s 这两档是在某台机器、某个网络、某个反爬窗口上
   定的。公网 IP 被短暂限速的人想要 15 秒，硬墙后面的人想要一小时，同一台机器上跑
   两个 agent 的人想干脆别探。可调用 `DHOLE_ENGINE_*` 之后仍要**夹紧**：
   `DHOLE_ENGINE_COOLDOWN=60000` 是 16 小时的死池，没有人是这个意思。

2. **熔断是被动的。** 引擎只能等自己的窗口到点、并且恰好有人再搜一次。测到的反面
   是 so360 那种「静默 20 分钟后仍回校验页」——所以巡检必须守两条规矩：**失败不续
   惩罚**（探一次就把 10 分钟重新计满，等于自己把墙续期），以及**有间隔**（默认 5
   分钟，`DHOLE_ENGINE_HEARTBEAT=0` 关掉）。

`updater._cooldown_config` 是为了 `dhole engines list` 在精简安装上也能跑而另写的
一份（不 import 搜索层）；那条重复是有意的，最后一组测试钉住两份的默认值不许漂移。
"""

from __future__ import annotations

import asyncio
import time as _time

import pytest

from dhole_mcp import search_metasearch as ms
from dhole_mcp.updater import _cooldown_config


@pytest.fixture(autouse=True)
def _clean_pool_state(monkeypatch):
    """熔断状态是进程全局的：不清掉就会跨用例（也跨真实 ~/.dhole）互相污染。"""
    monkeypatch.delenv("DHOLE_ENGINE_COOLDOWN", raising=False)
    monkeypatch.delenv("DHOLE_ENGINE_CHALLENGE_COOLDOWN", raising=False)
    monkeypatch.delenv("DHOLE_ENGINE_CONN_COOLDOWN", raising=False)
    monkeypatch.delenv("DHOLE_ENGINE_HEARTBEAT", raising=False)
    ms._BACKEND_HEALTH.clear()
    ms._CHALLENGE_COUNTS.clear()
    ms._CONN_FAIL_COUNTS.clear()
    ms._heartbeat_last_at = 0.0
    ms._heartbeat_running = False
    ms._heartbeat_log.clear()
    yield
    ms._BACKEND_HEALTH.clear()


class _Engine:
    """A stand-in backend, constructed exactly the way metasearch builds one.

    `answers` / `raises` are CLASS attributes on purpose: the variants below set
    them in their own class body, and an instance attribute in __init__ would
    shadow that and make "this engine failed" tests pass for the wrong reason.
    """

    disabled = False
    instances: list = []
    answers = True
    raises = False
    built_with: dict = {}

    def __init__(self, *, proxy=None, timeout=None, verify=True):
        type(self).instances.append(self)
        type(self).built_with = {"proxy": proxy, "timeout": timeout, "verify": verify}
        self.queries = 0

    def search(self, query, **kwargs):
        self.queries += 1
        if self.raises:
            raise RuntimeError("blocked by upstream")
        return [object()] if self.answers else []


class TestTheBackoffIsTunable:

    def test_defaults_unchanged(self):
        assert ms._cooldown_seconds("block") == (60.0, "")
        assert ms._cooldown_seconds("challenge") == (600.0, "")
        assert ms._cooldown_seconds("connection") == (600.0, "")

    def test_an_env_value_is_honoured(self, monkeypatch):
        monkeypatch.setenv("DHOLE_ENGINE_COOLDOWN", "15")
        assert ms._cooldown_seconds("block") == (15.0, "")

    def test_a_misread_value_is_clamped_and_said(self, monkeypatch):
        """60000 秒 = 16 小时的死池。夹紧之外还得说出来，不然旋钮本身成了静默降级。"""
        monkeypatch.setenv("DHOLE_ENGINE_COOLDOWN", "60000")
        seconds, note = ms._cooldown_seconds("block")
        assert seconds == 1800.0
        assert "outside the supported 5-1800s range" in note

    @pytest.mark.parametrize("raw,expect_note", [("abc", True), ("", False),
                                                ("   ", False)])
    def test_a_non_number_falls_back_to_the_default(self, monkeypatch, raw, expect_note):
        monkeypatch.setenv("DHOLE_ENGINE_COOLDOWN", raw)
        seconds, note = ms._cooldown_seconds("block")
        assert seconds == 60.0
        assert bool(note) is expect_note
        if expect_note:
            assert "not a number" in note

    def test_the_recorded_cooldown_actually_uses_the_value(self, monkeypatch):
        monkeypatch.setenv("DHOLE_ENGINE_COOLDOWN", "30")
        before = _time.time()
        ms._record_block("bing")
        assert 29 <= ms._BACKEND_HEALTH["bing"] - before <= 32

    def test_a_challenge_uses_its_own_tier(self, monkeypatch):
        monkeypatch.setenv("DHOLE_ENGINE_CHALLENGE_COOLDOWN", "120")
        for _ in range(ms._CHALLENGE_THRESHOLD):
            ms._record_block("brave", challenge=True)
        assert ms._BACKEND_HEALTH["brave"] - _time.time() <= 122

    def test_connection_failures_use_their_own_tier(self, monkeypatch):
        monkeypatch.setenv("DHOLE_ENGINE_CONN_COOLDOWN", "90")
        for _ in range(ms._CONN_FAIL_THRESHOLD):
            ms._record_conn_failure("yandex")
        assert ms._BACKEND_HEALTH["yandex"] - _time.time() <= 92


class TestTheHeartbeatRunsItself:

    @pytest.mark.asyncio
    async def test_a_cooling_engine_that_answers_comes_back_now(self, monkeypatch):
        cls = type("Ok", (_Engine,), {"instances": []})
        monkeypatch.setattr(ms, "_TEXT_ENGINES", {"bing": cls})
        ms._BACKEND_HEALTH["bing"] = _time.time() + 500

        out = await ms.engine_heartbeat()

        assert out["recovered"] == ["bing"]
        assert "bing" not in ms._BACKEND_HEALTH

    @pytest.mark.asyncio
    async def test_a_failed_probe_does_not_extend_the_punishment(self, monkeypatch):
        """巡检失败的代价必须是零：把 10 分钟重新计满，就是自己把墙续期。"""
        cls = type("Bad", (_Engine,), {"instances": [], "raises": True})
        monkeypatch.setattr(ms, "_TEXT_ENGINES", {"so360": cls})
        until = _time.time() + 60
        ms._BACKEND_HEALTH["so360"] = until

        out = await ms.engine_heartbeat()

        assert out["still_cooling"] == ["so360"]
        assert ms._BACKEND_HEALTH["so360"] == pytest.approx(until, abs=0.6)

    @pytest.mark.asyncio
    async def test_an_empty_answer_also_keeps_the_original_expiry(self, monkeypatch):
        cls = type("Quiet", (_Engine,), {"instances": [], "answers": False})
        monkeypatch.setattr(ms, "_TEXT_ENGINES", {"brave": cls})
        until = _time.time() + 42
        ms._BACKEND_HEALTH["brave"] = until
        await ms.engine_heartbeat()
        assert ms._BACKEND_HEALTH["brave"] == pytest.approx(until, abs=0.6)

    @pytest.mark.asyncio
    async def test_a_healthy_pool_costs_no_queries(self, monkeypatch):
        cls = type("Ok2", (_Engine,), {"instances": []})
        monkeypatch.setattr(ms, "_TEXT_ENGINES", {"bing": cls})
        out = await ms.engine_heartbeat()
        assert out == {"checked": [], "recovered": [], "still_cooling": []}
        assert cls.instances == []

    @pytest.mark.asyncio
    async def test_a_probe_uses_the_same_engine_construction_as_the_pool(self, monkeypatch):
        cls = type("Ok7", (_Engine,), {"instances": []})
        monkeypatch.setattr(ms, "_TEXT_ENGINES", {"bing": cls})
        ms._BACKEND_HEALTH["bing"] = _time.time() + 60
        await ms.engine_heartbeat()
        assert {"proxy", "timeout", "verify"} == set(cls.built_with)
        assert cls.built_with["verify"] is True


class TestTheSchedulingGate:

    @pytest.mark.asyncio
    async def test_it_fires_only_when_something_is_cooling(self, monkeypatch):
        cls = type("Ok3", (_Engine,), {"instances": []})
        monkeypatch.setattr(ms, "_TEXT_ENGINES", {"bing": cls})
        ms.schedule_engine_heartbeat()
        await asyncio.sleep(0)
        assert cls.instances == []

    @pytest.mark.asyncio
    async def test_the_interval_is_respected(self, monkeypatch):
        cls = type("Ok4", (_Engine,), {"instances": []})
        monkeypatch.setattr(ms, "_TEXT_ENGINES", {"bing": cls})
        ms._BACKEND_HEALTH["bing"] = _time.time() + 60
        ms.schedule_engine_heartbeat()
        await asyncio.sleep(0.05)
        assert len(cls.instances) == 1
        ms._BACKEND_HEALTH["bing"] = _time.time() + 60
        ms.schedule_engine_heartbeat()   # still inside the 300s window
        await asyncio.sleep(0.02)
        assert len(cls.instances) == 1

    @pytest.mark.asyncio
    async def test_the_interval_can_be_tuned_or_switched_off(self, monkeypatch):
        monkeypatch.setenv("DHOLE_ENGINE_HEARTBEAT", "0")
        cls = type("Ok5", (_Engine,), {"instances": []})
        monkeypatch.setattr(ms, "_TEXT_ENGINES", {"bing": cls})
        ms._BACKEND_HEALTH["bing"] = _time.time() + 60
        ms.schedule_engine_heartbeat()
        await asyncio.sleep(0.02)
        assert cls.instances == []
        assert ms.heartbeat_settings()["enabled"] is False

    @pytest.mark.asyncio
    async def test_two_overdue_rounds_do_not_stack_probes(self, monkeypatch):
        cls = type("Ok6", (_Engine,), {"instances": []})
        monkeypatch.setattr(ms, "_TEXT_ENGINES", {"bing": cls})
        ms._BACKEND_HEALTH["bing"] = _time.time() + 60
        # Claim the lock by hand and hold it: the gate under test is
        # `_heartbeat_running`, and a real probe would have to be timed to overlap.
        ms._heartbeat_running = True
        ms.schedule_engine_heartbeat()
        ms.schedule_engine_heartbeat()
        await asyncio.sleep(0.02)
        assert cls.instances == []
        ms._heartbeat_running = False
        ms.schedule_engine_heartbeat()
        await asyncio.sleep(0.1)
        assert len(cls.instances) == 1


class TestTheConfigIsVisible:

    def test_cooldown_settings_names_the_env_var_per_tier(self):
        cfg = ms.cooldown_settings()
        assert cfg["block"]["env"] == "DHOLE_ENGINE_COOLDOWN"
        assert cfg["block"]["seconds"] == 60.0

    def test_a_clamp_is_reported_in_settings(self, monkeypatch):
        monkeypatch.setenv("DHOLE_ENGINE_COOLDOWN", "2")
        assert "clamped" in ms.cooldown_settings()["block"]["note"].replace(
            "outside the supported", "clamped")

    def test_the_cli_copy_agrees_with_the_real_defaults(self):
        """两份实现是刻意的（list 命令不能拖进搜索层），默认值不许漂移。"""
        real = ms.cooldown_settings()
        cli = _cooldown_config()
        assert cli["tiers"] == {k: v["seconds"] for k, v in real.items()}
        assert cli["heartbeat"] == ms._heartbeat_interval()[0]

    def test_the_cli_reports_an_override(self, monkeypatch):
        monkeypatch.setenv("DHOLE_ENGINE_COOLDOWN", "20")
        assert _cooldown_config()["tiers"]["block"] == 20.0
        monkeypatch.setenv("DHOLE_ENGINE_COOLDOWN", "99999")
        cfg = _cooldown_config()
        assert cfg["tiers"]["block"] == 1800.0 and cfg["note"]

    @pytest.mark.parametrize("raw,expected", [("0", 0.0), ("60", 60.0), ("1e9", 86400.0)])
    def test_the_heartbeat_interval_is_clamped(self, monkeypatch, raw, expected):
        monkeypatch.setenv("DHOLE_ENGINE_HEARTBEAT", raw)
        assert ms._heartbeat_interval()[0] == expected

    def test_engines_list_prints_the_in_force_values(self, monkeypatch, capsys):
        from dhole_mcp.server import _cmd_engines

        monkeypatch.setenv("DHOLE_ENGINE_COOLDOWN", "20")
        assert _cmd_engines(["list"]) == 0
        out = capsys.readouterr().out
        assert "backoff: blocked 20s" in out
        assert "heartbeat" in out
