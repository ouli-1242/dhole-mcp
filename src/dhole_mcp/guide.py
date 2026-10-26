"""``sys_guide`` 工具背后的按需文档层。

分工以及为什么这么画线：``tools/list`` + ``instructions`` 是每个注册 dhole 的
客户端**每次连接**都要付的成本，而一次工具调用只由发问的那一个调用方付一次。
所以常连文本承载路由与硬约束，解释性正文放进随包发布的手册卷
（``skills/dhole-web/``），由本模块原样送出。

同一份语料的两扇门：``dhole skill install`` 把同样的文件复制到
``~/.agents/skills``，供会加载技能的宿主使用；``sys_guide`` 则是不需要安装、
不需要配置、不需要重启的那扇门。这就是重点 —— 一个 agent 发现不了的能力等于
一个需要 README 的能力，而 agent 不读 README。

主题注册表是唯一手写的清单；``tests/test_guide.py`` 双向核对它与磁盘上的文件，
所以"加了卷却没加主题"（不可达）和"主题指向已删除的卷"（空头承诺）都会大声失败。
"""

from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parent / "skills" / "dhole-web"

#: topic -> (相对技能根目录的路径, 什么情况下值得一次调用)
TOPICS: dict[str, tuple[str, str]] = {
    "start": ("references/start.md",
              "the one-page route map: three paths, hard constraints, where to read next"),
    "manual": ("SKILL.md",
               "the full handbook: tool-selection tables, signal triage, config quick reference"),
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

#: agent 凭猜测会输入的名字：卷自身的文件名。
ALIASES: dict[str, str] = {pathlib.Path(p).stem.lower(): t for t, (p, _) in TOPICS.items()}

# topic -> 能解析到它的文件名写法。半张表反过来才是主题行要的：
# 读的人想知道"我猜的那个名字通向哪一卷"，而 `ALIASES.get(topic)` 给的是反方向的
# 答案 —— 对不是别名的主题它就是 None，于是列表里打出过 `(aka None)`（评审 R3刀3）。
_ALIASES_OF: dict[str, list[str]] = {}
for _alias, _topic in ALIASES.items():
    if _alias != _topic:
        _ALIASES_OF.setdefault(_topic, []).append(_alias)

_HEADING = re.compile(r"^## +(.+?)\s*$", re.M)


def _resolve(topic: str) -> str | None:
    key = (topic or "").strip().strip("'\"").lower()
    if key in TOPICS:
        return key
    return ALIASES.get(key)


def _volume_chars(rel: str) -> int:
    """一卷的字符数，与 read() 页眉里报的同一个口径。"""
    try:
        return len((_ROOT / rel).read_text(encoding="utf-8").strip())
    except OSError:
        return 0


def index(reason: str = "") -> str:
    """主题清单；也是坏主题的答案 —— 被拒的调用若不说清有什么可选，就白多一轮往返。

    参数:
        reason: 放在清单前的说明（通常是被拒主题的原因）；空串则不加。

    返回:
        可直接作为工具输出返回的清单文本，每行带该卷的字符数。

    每行都带着该卷的大小（G25 附注二/M2）："behavior" 比整张连接期清单还大，
    而 agent 在把 3.6 万字符读进上下文之前，唯一能知道这点的地方就是这里 ——
    大小把"读这一卷"变成"先传 section="，避免一次本会花掉整卷的猜测。
    读不到卷时字符数按 0 计，从不抛异常。
    """
    head = (f"{reason}\n\n" if reason else "")
    lines = [head + "sys_guide(topic) returns one handbook volume per call. "
             "Known topics (aliases in parentheses):"]
    for topic, (path, when) in TOPICS.items():
        others = _ALIASES_OF.get(topic) or []
        alias = f" (aka {', '.join(repr(a) for a in others)})" if others else ""
        lines.append(f"- topic='{topic}'{alias} ({_volume_chars(path):,} chars): {when}")
    lines.append("")
    lines.append("Pass section='<heading text>' to get one section of a volume instead of "
                 "all of it — prefer that on any volume over ~10k chars.")
    return "\n".join(lines)


def _chunks(text: str) -> list[tuple[str, str]]:
    """``[(heading, body)]``，按二级标题切分；'' 表示前言部分。"""
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
    """一次请求的 ``(header, body)``。从不抛异常。

    参数:
        topic: 主题名或别名（卷文件名）；空串返回主题清单。
        section: 二级标题的片段（大小写、引号不敏感）；空串返回整卷。

    返回:
        ``(页眉, 正文)``。未知主题、缺失文件、匹配不到任何标题的 section，
        正文都是"实际有什么"（清单或本卷标题列表），而不是一串错误 —— 调用方
        在导航文档，猜错时最有用的结果就是地图。
    """
    resolved = _resolve(topic)
    if resolved is None:
        _why = (f"unknown topic {topic!r}." if (topic or "").strip()
                else "no topic given.")
        if (section or "").strip():
            # 这一页的页尾教人 `Pass section='<heading text>'`，而这一发正是用了它 —— 没有
            # volume 可切，这颗就被丢了，整页一个字都没说（评审 R02-101 · 真机
            # `r124_section_object.json`：`sys_guide(section='timeout')` 与裸问逐字节相同）。
            # 有 volume 那条路上 `section=` 不管中不中都有话说，这一支不许比它沉默。
            _why += (f" section={section!r} did nothing here - a section cuts one volume, "
                     "and this call named no volume.")
        return "sys_guide: topics", index(_why)

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
        # 标题列表只列一遍：先逐条列出，再在同一回答里把它们用 " | " 重排一次，
        # 是让一次"没猜中"付两遍字数（评审 R3刀3）。
        return (f"{head}\nsection={section!r} matched no heading",
                f"No section matches {section!r}. Headings in this volume:\n"
                + "\n".join(f"- {h}" for h, _ in parts if h)
                + f"\n\nTo see them all with their text, re-read without section= "
                  f"(the whole volume is {len(text):,} chars).")

    toc = [h for h, _ in parts if h]
    if not toc:
        return head, text
    return head, text + "\n\nSections here (pass section= for one): " + " | ".join(toc)
