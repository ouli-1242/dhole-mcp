"""R01-009 — the private-host door gets named only where it actually stands.

`allow_private` lives in `cred_defaults` with no host scope, so `_identity_for` listed
it in `identity_applied` for every target after it had been stored once. Round 01
measured five reproductions on public pages: `web_read` of an ibm.com article, of a
cnblogs robots.txt, of two PDFs and of a cached article each answered
`identity_applied="allow_private"`. A user who stores that waiver for their own dev
service and then reads public sites is told, in the field the manual calls the
authority on identity, that public pages were fetched under a private-host waiver —
and one of the five reproductions had prose that no longer mentioned private while
the structured field still did.

Geography decides the claim, so the fix reads geography: the name appears for a
loopback/private literal target and for a host the stored value actually names.
`values` is untouched — the gate still ships on every call, which matters for the
targets whose privacy DNS has to answer (a public name behind a TUN resolver, a
`.local` host), and those answers are made deeper in `validate_url`.
"""

import pytest

from dhole_mcp import identity, security
from dhole_mcp.server import MasterFetchServer

PUBLIC = "https://www.ibm.com/think/topics/saas"
LOOPBACK = "http://127.0.0.1:1/"
NAMED = "http://my-service.local/api"


def _note(url: str) -> str:
    return MasterFetchServer()._identity_for(url)[1]


# ─── the note ───────────────────────────────────────────────────────────────

def test_a_public_target_does_not_name_the_private_door():
    identity.set_value("allow_private", True)
    assert "allow_private" not in _note(PUBLIC)


def test_a_public_target_does_not_name_a_door_that_names_another_host():
    identity.set_value("allow_private", ["my-service.local"])
    assert "allow_private" not in _note(PUBLIC)


def test_a_loopback_target_still_names_the_door():
    """Over-correcting must not cost the honest half: the waiver really is what
    makes a loopback target reachable, and the user needs to see it ran on it."""
    identity.set_value("allow_private", True)
    assert "allow_private" in _note(LOOPBACK)


def test_a_host_the_stored_list_names_still_gets_the_door():
    """`my-service.local` is not a private *literal* — the whole point of naming a
    host is that its geography is unknown until DNS answers, and the door is the
    reason the attempt is made at all."""
    identity.set_value("allow_private", ["my-service.local"])
    assert "allow_private" in _note(NAMED)


def test_the_other_stored_keys_are_unaffected():
    identity.set_value("allow_private", True)
    identity.set_value("proxy", "http://127.0.0.1:7890")
    note = _note(PUBLIC)
    assert "proxy" in note, note
    assert "allow_private" not in note


def test_nothing_stored_notes_nothing():
    assert _note(PUBLIC) == ""
    assert _note(LOOPBACK) == ""


# ─── the gate: this fix must not move it ────────────────────────────────────

def test_the_stored_door_still_ships_for_a_public_target():
    """Silencing the NAME must not silence the value: dropping it from `values`
    would change which targets validate_url lets through — a public name resolving
    into a TUN fake-ip range still needs the stored decision to exist downstream."""
    identity.set_value("allow_private", True)
    values, _note_text = MasterFetchServer()._identity_for(PUBLIC)
    assert values["allow_private"] is True


@pytest.mark.parametrize("value,url,expected", [
    (True, LOOPBACK, True),
    (True, NAMED, False),
    (["my-service.local"], NAMED, True),
    (["my-service.local"], PUBLIC, False),
    (True, PUBLIC, False),
    ("192.168.1.50", "http://192.168.1.50/x", True),
    (None, LOOPBACK, False),
    ("", PUBLIC, False),
])
def test_the_door_covers_a_target_by_geography_or_by_name(value, url, expected):
    assert security.allow_private_covers(value, url) is expected


def test_the_cover_check_never_raises_on_a_broken_url():
    assert security.allow_private_covers(True, "not a url") is False
    assert security.allow_private_covers(True, "") is False
    assert security.allow_private_covers(True, "file:///etc/passwd") is False
