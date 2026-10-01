"""W4 · unrecognised env values reach the shared warning list.

The plan's core claim: "the same failure class as an ignored unknown
argument, one level up" (server.py:4834-4836) - a typo in env should be visible
in one place, not scattered across silent fallbacks. These tests are the
minimum bar W2 depends on before it can print the config audit; the warning
list is what makes doctor able to name the problem.
"""

from __future__ import annotations

import pytest

from dhole_mcp import env_registry, robots, security, sessions
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
def test_sessions_cookie_ttl_invalid_lands_in_shared_warning_list(monkeypatch):
    """``sessions._cookie_ttl`` is the module the plan picked to prove the
    warning list is not ``server.py``-local: sessions imports neither
    ``server`` nor ``updater``, so if ``doctor`` can print this warning, any
    reader wired to the same list can too."""
    monkeypatch.setenv("DHOLE_COOKIE_TTL_S", "abc")
    assert sessions._cookie_ttl() == sessions.COOKIE_TTL_S
    snaps = env_registry.warnings_snapshot()
    assert any(w.name == "DHOLE_COOKIE_TTL_S" and "abc" in w.message for w in snaps)


def test_sessions_cookie_ttl_below_floor_clamps_and_warns(monkeypatch):
    monkeypatch.setenv("DHOLE_COOKIE_TTL_S", "5")
    got = sessions._cookie_ttl()
    assert got == 60.0
    snaps = env_registry.warnings_snapshot()
    assert any(w.name == "DHOLE_COOKIE_TTL_S" and "below the minimum" in w.message
               for w in snaps)


def test_crawl_neural_focus_typo_records_warning(monkeypatch):
    """Another module (crawl) reaching the same list."""
    from dhole_mcp import crawl
    monkeypatch.setenv("DHOLE_NEURAL_FOCUS", "flase")
    # crawl's flag uses _env_flag-style ON; a typo stays ON, but the warning
    # must be visible to the shared list (W4.1 - semantics unchanged).
    assert crawl._neural_focus_enabled() is True
    snaps = env_registry.warnings_snapshot()
    assert any(w.name == "DHOLE_NEURAL_FOCUS" for w in snaps)


def test_actions_multimodal_typo_records_warning(monkeypatch):
    """Same, from actions.py's own reader (no reverse-import to server)."""
    from dhole_mcp import actions
    monkeypatch.setenv("DHOLE_MULTIMODAL", "yes-please")
    assert actions.multimodal_enabled() is True
    snaps = env_registry.warnings_snapshot()
    assert any(w.name == "DHOLE_MULTIMODAL" and "yes-please" in w.message
               for w in snaps)


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


def test_dns_recheck_typo_warns_and_stays_enabled(monkeypatch):
    """The DNS recheck's short off-list is preserved (W4.1 semantics), but a
    value that is neither an ON nor a short-OFF token now warns."""
    monkeypatch.setenv("DHOLE_SSRF_DNS_RECHECK", "flase")
    assert security._dns_recheck_enabled() is True
    snaps = env_registry.warnings_snapshot()
    assert any(w.name == "DHOLE_SSRF_DNS_RECHECK"
               and "DNS recheck stays enabled" in w.message for w in snaps)


@pytest.mark.parametrize("value", ["0", "false", "off", "no"])
def test_dns_recheck_recognised_off_still_disables(monkeypatch, value):
    """Regression: the four documented "turn it off" tokens still work."""
    monkeypatch.setenv("DHOLE_SSRF_DNS_RECHECK", value)
    assert security._dns_recheck_enabled() is False
    assert env_registry.warnings_snapshot() == ()


def test_dns_recheck_unset_or_empty_is_enabled(monkeypatch):
    """The unset and empty-string paths are unchanged (fail-closed default)."""
    monkeypatch.delenv("DHOLE_SSRF_DNS_RECHECK", raising=False)
    assert security._dns_recheck_enabled() is True
    monkeypatch.setenv("DHOLE_SSRF_DNS_RECHECK", "")
    assert security._dns_recheck_enabled() is True
    assert env_registry.warnings_snapshot() == ()


def test_cdp_attach_typo_warns_and_stays_off(monkeypatch):
    """CDP_ATTACH was already strict-ON; now an unrecognised value is recorded."""
    monkeypatch.setenv("DHOLE_CDP_ATTACH", "flase")
    err, _ = server_mod._cdp_attach_gate("http://127.0.0.1:9222")
    assert err and "cdp_attach_disabled" in err
    snaps = env_registry.warnings_snapshot()
    assert any(w.name == "DHOLE_CDP_ATTACH" for w in snaps)


def test_server_prewarm_typo_warns_and_keeps_prewarm_enabled(monkeypatch):
    monkeypatch.setenv("DHOLE_NO_BROWSER_PREWARM", "f1ase")
    assert server_mod._browser_prewarm_enabled() is True
    snaps = env_registry.warnings_snapshot()
    assert any(w.name == "DHOLE_NO_BROWSER_PREWARM" for w in snaps)


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


def test_search_deadline_invalid_records_warning(monkeypatch):
    """Module-level import read (search_metasearch.py:85) uses read_float,
    so an invalid value falls back to 16.0 AND records - the module is
    imported eagerly, so we re-run the module-level constant computation."""
    import importlib

    from dhole_mcp import search_metasearch
    monkeypatch.setenv("DHOLE_SEARCH_DEADLINE", "not-a-number")
    env_registry.clear_warnings()
    importlib.reload(search_metasearch)
    try:
        assert search_metasearch._SEARCH_DEADLINE == 16.0
        snaps = env_registry.warnings_snapshot()
        assert any(w.name == "DHOLE_SEARCH_DEADLINE" for w in snaps)
    finally:
        monkeypatch.delenv("DHOLE_SEARCH_DEADLINE", raising=False)
        env_registry.clear_warnings()
        importlib.reload(search_metasearch)
