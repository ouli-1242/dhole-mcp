"""The on-demand documentation layer behind the ``sys_guide`` tool.

Division of labour, and why it is drawn here: ``tools/list`` + ``instructions``
are paid on EVERY connect by EVERY client that registers dhole, while a tool call
is paid once by the single caller that asks. So the always-connected text carries
routing and hard constraints, and the explanatory prose lives in the packaged
handbook volumes (``skills/dhole-web/``), served verbatim from this module.

Two doors to one corpus: ``dhole skill install`` copies these same files to
``~/.agents/skills`` for hosts that load skills, and ``sys_guide`` is the door
that needs no install, no configuration and no restart. That is the point — a
capability an agent cannot discover is a capability that requires a README, and
an agent does not read the README.

The topic registry is the only hand-written list; ``tests/test_guide.py`` keeps
it against the files on disk in both directions, so a volume added without a
topic (unreachable) or a topic pointing at a deleted volume (a broken promise)
both fail loudly.
"""

from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parent / "skills" / "dhole-web"

#: topic -> (path relative to the skill root, when it is worth a call)
TOPICS: dict[str, tuple[str, str]] = {
    "start": ("SKILL.md",
              "which tool for which job, the hard constraints, how to read a result that looks off"),
    "call": ("references/examples.md",
             "writing any call: parameter placement with counterexamples, copy-ready patterns"),
    "fields": ("references/fields.md",
               "reading a response: what each field means and what to do when it is not the default"),
    "behavior": ("references/tools.md",
                 "deep behavior: fetch escalation, page actions, schema extraction, engine matrix, crawl economics"),
    "fix": ("references/troubleshooting.md",
            "something failed: symptom-indexed diagnosis, the per-call fix before the config one"),
    "config": ("references/configuration.md",
               "before touching configuration: every knob by scenario, and which ones need a restart"),
    "recipes": ("references/recipes.md",
                "a multi-step task: sequences with their failure pivots"),
}

#: What an agent types when it guesses: the volume's own file name.
ALIASES: dict[str, str] = {pathlib.Path(p).stem.lower(): t for t, (p, _) in TOPICS.items()}

_HEADING = re.compile(r"^## +(.+?)\s*$", re.M)


def _resolve(topic: str) -> str | None:
    key = (topic or "").strip().strip("'\"").lower()
    if key in TOPICS:
        return key
    return ALIASES.get(key)


def index(reason: str = "") -> str:
    """The topic list. Also the answer to a bad topic: a rejected call that does
    not say what was available costs a second round trip for nothing."""
    head = (f"{reason}\n\n" if reason else "")
    lines = [head + "sys_guide(topic) returns one handbook volume per call. "
             "Known topics (aliases in parentheses):"]
    for topic, (path, when) in TOPICS.items():
        alias = "" if ALIASES.get(topic) == topic else f" (aka {ALIASES.get(topic)})"
        lines.append(f"- topic='{topic}'{alias}: {when}")
    lines.append("")
    lines.append("Pass section='<heading text>' to get one section of a volume instead of all of it.")
    return "\n".join(lines)


def _chunks(text: str) -> list[tuple[str, str]]:
    """``[(heading, body)]`` split on level-2 headings; '' = the preamble."""
    marks = list(_HEADING.finditer(text))
    if not marks:
        return [("", text)]
    out: list[tuple[str, str]] = []
    if marks[0].start():
        out.append(("", text[:marks[0].start()]))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((m.group(1), text[m.start():end]))
    return out


def read(topic: str = "", section: str = "") -> tuple[str, str]:
    """``(header, body)`` for one request. Never raises.

    An unknown topic, a missing file and a section that matches no heading all
    answer with what IS available rather than an error string: the caller is
    navigating documentation, and the useful result of a wrong guess is the map.
    """
    resolved = _resolve(topic)
    if resolved is None:
        return "sys_guide: topics", index(
            f"unknown topic {topic!r}." if (topic or "").strip() else
            "no topic given.")

    rel, when = TOPICS[resolved]
    path = _ROOT / rel
    if not path.is_file():
        return f"sys_guide: {rel} missing", index(
            f"topic '{resolved}' points at {rel}, which is not in the installed package "
            "(reinstall dhole-mcp).")

    text = path.read_text(encoding="utf-8").strip()
    parts = _chunks(text)
    head = (f"topic='{resolved}' from {rel} — {when}"
            f"\n({len(text)} chars, {len(parts)} sections)")

    want = (section or "").strip().strip("'\"").lower()
    if want:
        hits = [(h, b) for h, b in parts if h and want in h.lower()]
        if hits:
            names, bodies = hits[0]
            return (f"{head}\nsection='{names}' of {len(parts)}", bodies.strip())
        titles = ", ".join(h for h, _ in parts if h) or "(none)"
        return (f"{head}\nsection={section!r} matched no heading",
                f"No section matches {section!r}. Headings in this volume:\n"
                + "\n".join(f"- {h}" for h, _ in parts if h)
                + f"\n\n(to see them all in one call: {titles})")

    toc = [h for h, _ in parts if h]
    if not toc:
        return head, text
    return head, text + "\n\nSections here (pass section= for one): " + " | ".join(toc)
