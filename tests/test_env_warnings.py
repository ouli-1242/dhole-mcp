"""W4 · unrecognised env values reach the shared warning list.

The plan's core claim: "the same failure class as an ignored unknown
argument, one level up" (server.py:4834-4836) - a typo in env should be visible
in one place, not scattered across silent fallbacks. These tests are the
minimum bar W2 depends on before it can print the config audit; the warning
list is what makes doctor able to name the problem.
"""

from __future__ import annotations

import pytest

from dhole_mcp import env_registry, robots, sessions
from dhole_mcp import server as server_mod


@pytest.fixture(autouse=True)
def _clean_warnings():
    env_registry.clear_warnings()
    yield
    env_registry.clear_warnings()


# ─── _env_int (W4 test 1) ─────────────────────────────────────────────
def test_env_int_returns_default_but_records_warning(monkeypatch):
    """The 5 W4 tests, part 1: ``_env_int("X", 7)`` with ``X="abc"`` must
    still return 7 AND leave one warning naming ``abc`` on the list.
    """
    monkeypatch.setenv("DHOLE_W4_X", "abc")
    got = server_mod._env_int("DHOLE_W4_X", 7)
    assert got == 7
    snaps = env_registry.warnings_snapshot()
    assert any(w.name == "DHOLE_W4_X" and "abc" in w.message for w in snaps)


def test_env_int_valid_value_leaves_no_warning(monkeypatch):
    monkeypatch.setenv("DHOLE_W4_X", "12")
    assert server_mod._env_int("DHOLE_W4_X", 7) == 12
    assert env_registry.warnings_snapshot() == ()


# ─── _env_flag (W4 test 2 + 3) ────────────────────────────────────────
def test_env_flag_unrecognised_value_stays_on_and_warns(monkeypatch):
    """W4.1 preserves ``_env_flag``'s "anything not in the off-list -> ON"
    behaviour so ``DHOLE_MULTIMODAL=yes-please`` keeps working; the new part
    is only that the typo is recorded."""
    monkeypatch.setenv("DHOLE_W4_Y", "yes-please")
    assert server_mod._env_flag("DHOLE_W4_Y") is True
    snaps = env_registry.warnings_snapshot()
    assert any(w.name == "DHOLE_W4_Y" and "yes-please" in w.message for w in snaps)


@pytest.mark.parametrize("value", ["1", "true", "on", "YES"])
def test_env_flag_recognised_on_values_no_warning(monkeypatch, value):
    monkeypatch.setenv("DHOLE_W4_Y", value)
    assert server_mod._env_flag("DHOLE_W4_Y") is True
    assert env_registry.warnings_snapshot() == ()


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "none", "null", ""])
def test_env_flag_recognised_off_values_no_warning(monkeypatch, value):
    monkeypatch.setenv("DHOLE_W4_Y", value)
    assert server_mod._env_flag("DHOLE_W4_Y") is False
    assert env_registry.warnings_snapshot() == ()


# ─── cross-module visibility (W4 test 4) ──────────────────────────────
def test_a_retired_cookie_ttl_knob_is_ignored_without_warnings(monkeypatch):
    """17.0: the TTL is a fixed constant and the knob is retired — a stale or
    nonsensical value in the env block must not move it, must not crash, and
    must NOT land in the warning list (that list is for knobs that are read;
    retired ones are only ever named by --doctor's dynamic scan)."""
    monkeypatch.setenv("DHOLE_COOKIE_TTL_S", "abc")
    assert sessions._cookie_ttl() == sessions.COOKIE_TTL_S
    assert env_registry.warnings_snapshot() == ()


def test_default_engines_typo_degrades_gracefully(monkeypatch):
    """A mistyped engine name is skipped (and logged), the rest of the pool
    still answers — an operator typo must not take search down."""
    from dhole_mcp import search_metasearch as ms
    monkeypatch.setenv("DHOLE_DEFAULT_ENGINES", "bing,not-a-real-engine")
    out = ms._configured_default_backends()
    assert "bing" in out and "not-a-real-engine" not in out


# ─── D1: safety gates change direction (W4 gate list) ───────────────
def test_ignore_robots_typo_keeps_compliance_on(monkeypatch):
    """D1: a typo on DHOLE_IGNORE_ROBOTS now fails in the safe direction
    (compliance stays ON) and warns; previously it silently turned compliance
    OFF."""
    monkeypatch.setenv("DHOLE_IGNORE_ROBOTS", "flase")
    assert robots.env_disabled() is False
    snaps = env_registry.warnings_snapshot()
    assert any(w.name == "DHOLE_IGNORE_ROBOTS" and "OFF" in w.message for w in snaps)


@pytest.mark.parametrize("value", ["1", "true", "yes", "on"])
def test_ignore_robots_recognised_on_disables(monkeypatch, value):
    """Regression: the ON-token path is unchanged."""
    monkeypatch.setenv("DHOLE_IGNORE_ROBOTS", value)
    assert robots.env_disabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "none", "null", "  "])
def test_ignore_robots_recognised_off_still_keeps_compliance(monkeypatch, value):
    monkeypatch.setenv("DHOLE_IGNORE_ROBOTS", value)
    assert robots.env_disabled() is False
    assert env_registry.warnings_snapshot() == ()


def test_dns_recheck_knob_is_gone_measurement_decides(monkeypatch):
    """17.0: the knob is retired; skip is decided ONLY by the fake-ip probe.
    An explicit off value must NOT relax the guard any more — that escape hatch
    was the last manual way around a fail-closed guard."""
    from dhole_mcp import security as sec
    monkeypatch.setattr(sec, "probe_fake_ip", lambda: ("public", "", ""))
    for value in ("0", "false", "off", "no", "flase", ""):
        monkeypatch.setenv("DHOLE_SSRF_DNS_RECHECK", value)
        assert sec._dns_recheck_enabled() is True, value
    assert env_registry.warnings_snapshot() == ()


def test_cdp_gate_has_no_env_switch_left(monkeypatch, tmp_path):
    """CDP_ATTACH/CDP_HOSTS are retired: the fence is the persisted host set,
    and a stale env value can neither enable attach nor silence the ask."""
    for name in ("DHOLE_CDP_ATTACH", "DHOLE_CDP_HOSTS"):
        monkeypatch.setenv(name, "portal.example.com,1")
    (tmp_path / "cdp_hosts.json").unlink(missing_ok=True)
    server_mod._cdp_hosts_cache, server_mod._cdp_hosts_stamp = frozenset(), None
    err, allowed = server_mod._cdp_attach_gate("http://127.0.0.1:9222",
                                               targets=["https://read.example.test/"])
    assert err and "cdp_authorization_required" in err
    assert allowed is None
    assert env_registry.warnings_snapshot() == ()


def test_server_prewarm_has_no_env_switch_left(monkeypatch):
    """NO_BROWSER_PREWARM is retired: the warm-up always runs."""
    import inspect
    monkeypatch.setenv("DHOLE_NO_BROWSER_PREWARM", "1")
    assert not hasattr(server_mod, "_browser_prewarm_enabled"), (
        "the reader must be gone, not just overridable")
    src = inspect.getsource(server_mod.MasterFetchServer._prewarm_stealthy)
    assert "DHOLE_NO_BROWSER_PREWARM" not in src


# ─── W4 test 5 - regression: pre-existing _env_flag tests unchanged ──
def test_existing_server_env_flag_semantics_are_intact(monkeypatch):
    """The plan's W4 "回归" clause: existing tests asserting _env_flag
    behaviour must pass unchanged. Replaying test_prewarm_env_flag_variants
    and the flag-ON semantics inline is the cheapest proof; if either of
    these flip, doctor's "restart the host" wording lies to the user."""
    # _env_flag: unset / "" / known-off -> False
    monkeypatch.delenv("DHOLE_W4_Z", raising=False)
    assert server_mod._env_flag("DHOLE_W4_Z") is False
    monkeypatch.setenv("DHOLE_W4_Z", "")
    assert server_mod._env_flag("DHOLE_W4_Z") is False
    monkeypatch.setenv("DHOLE_W4_Z", "off")
    assert server_mod._env_flag("DHOLE_W4_Z") is False
    # Known-on / unknown-on -> True (unchanged)
    monkeypatch.setenv("DHOLE_W4_Z", "1")
    assert server_mod._env_flag("DHOLE_W4_Z") is True
    monkeypatch.setenv("DHOLE_W4_Z", "surprise")
    assert server_mod._env_flag("DHOLE_W4_Z") is True


def test_search_deadline_is_a_fixed_constant(monkeypatch):
    """17.0: the whole-search deadline is a fixed 16s; the knob is retired and
    a stale value neither moves it nor records anything."""
    from dhole_mcp import search_metasearch
    monkeypatch.setenv("DHOLE_SEARCH_DEADLINE", "not-a-number")
    env_registry.clear_warnings()
    try:
        assert search_metasearch._SEARCH_DEADLINE == 16.0
        assert env_registry.warnings_snapshot() == ()
    finally:
        env_registry.clear_warnings()
