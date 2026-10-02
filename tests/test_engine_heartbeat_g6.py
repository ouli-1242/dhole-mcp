"""G6 → 17.0 回归：引擎池的退避**写死**，冷却要有人主动去看。

17.0 之前的两条设计（可调的退避档位、可关的巡检）被大众化一刀切收掉：
冷却时长与巡检节奏都是**能算清的数**（60s / 600s / 600s；巡检 300s），
``DHOLE_ENGINE_*`` 四个旋钮注销 —— 运行时静默忽略，`dhole engines list`
展示固定值。留下来的行为测试一条不删：熔断按**拒绝的形态**分档、巡检
**失败不续惩罚**（探一次就把窗口重新计满等于自己把墙续期）、巡检只在
有引擎冷却时才动。

`updater._cooldown_config` 是为了 `dhole engines list` 在精简安装上也能跑而
另写的一份（不 import 搜索层）；那条重复是有意的，最后一组测试钉住两份的
值不许漂移。
"""

from __future__ import annotations

import asyncio
import time as _time

import pytest

from dhole_mcp import search_metasearch as ms
from dhole_mcp.updater import _cooldown_config


@pytest.fixture(autouse=True)
def _clean_pool_state(monkeypatch):
    """熔断状态是进程全局的：不清掉就会跨用例（也跨真实 ~/.dhole）互相污染。
    顺手把已注销的旋钮设上脏值 —— 任何用例因此变红，就说明旋钮复活了。"""
    monkeypatch.setenv("DHOLE_ENGINE_COOLDOWN", "15")
    monkeypatch.setenv("DHOLE_ENGINE_CHALLENGE_COOLDOWN", "1")
    monkeypatch.setenv("DHOLE_ENGINE_CONN_COOLDOWN", "1")
    monkeypatch.setenv("DHOLE_ENGINE_HEARTBEAT", "0")
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


class TestTheBackoffIsFixed:

    def test_the_fixed_tiers(self):
        assert ms._cooldown_seconds("block") == (60.0, "")
        assert ms._cooldown_seconds("challenge") == (600.0, "")
        assert ms._cooldown_seconds("connection") == (600.0, "")

    def test_a_stale_env_value_is_silently_ignored(self):
        """fixture 里已经设了 DHOLE_ENGINE_COOLDOWN=15：60s 必须原封不动。
        注销的旋钮不许复活成一道暗闸。"""
        assert ms._cooldown_seconds("block") == (60.0, "")

    def test_the_recorded_cooldown_uses_the_fixed_tier(self):
        before = _time.time()
        ms._record_block("bing")
        assert 59 <= ms._BACKEND_HEALTH["bing"] - before <= 62

    def test_a_challenge_uses_its_own_tier(self):
        for _ in range(ms._CHALLENGE_THRESHOLD):
            ms._record_block("brave", challenge=True)
        assert 598 <= ms._BACKEND_HEALTH["brave"] - _time.time() <= 602

    def test_connection_failures_use_their_own_tier(self):
        for _ in range(ms._CONN_FAIL_THRESHOLD):
            ms._record_conn_failure("yandex")
        assert 598 <= ms._BACKEND_HEALTH["yandex"] - _time.time() <= 602


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
    async def test_the_fixed_interval_is_respected(self, monkeypatch):
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
    async def test_the_stale_knob_cannot_switch_it_off(self, monkeypatch):
        """fixture 已把 DHOLE_ENGINE_HEARTBEAT=0 设上：巡检照样调度。"""
        cls = type("Ok5", (_Engine,), {"instances": []})
        monkeypatch.setattr(ms, "_TEXT_ENGINES", {"bing": cls})
        ms._BACKEND_HEALTH["bing"] = _time.time() + 60
        ms.schedule_engine_heartbeat()
        await asyncio.sleep(0.02)
        assert len(cls.instances) == 1
        assert ms.heartbeat_settings()["enabled"] is True

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

    def test_cooldown_settings_reports_the_fixed_tiers(self):
        cfg = ms.cooldown_settings()
        assert cfg["block"]["seconds"] == 60.0
        assert cfg["challenge"]["seconds"] == 600.0
        assert cfg["connection"]["seconds"] == 600.0
        assert all("env" not in v for v in cfg.values()), (
            "旋钮已注销：settings 不该再暗示它由 env 控制")

    def test_the_cli_copy_agrees_with_the_real_values(self):
        """两份实现是刻意的（list 命令不能拖进搜索层），数值不许漂移。"""
        real = ms.cooldown_settings()
        cli = _cooldown_config()
        assert cli["tiers"] == {k: v["seconds"] for k, v in real.items()}
        assert cli["heartbeat"] == ms._heartbeat_interval()[0]

    def test_the_cli_reports_no_override_note(self):
        assert _cooldown_config()["note"] == ""

    def test_the_heartbeat_interval_is_fixed(self):
        assert ms._heartbeat_interval() == (300.0, "")
        assert ms.heartbeat_settings()["interval_s"] == 300.0

    def test_engines_list_prints_the_in_force_values(self, capsys):
        from dhole_mcp.server import _cmd_engines

        assert _cmd_engines(["list"]) == 0
        out = capsys.readouterr().out
        assert "backoff: blocked 60s" in out
        assert "challenge x3 600s" in out
        assert "heartbeat" in out
        assert "DHOLE_ENGINE" not in out, "注销的旋钮不该再出现在面向用户的行里"
