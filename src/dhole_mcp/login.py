"""登录流编排的**纯逻辑**（计划第七章「独立登录流工具」）。

这里只有两件事，都不碰浏览器，所以都能被单元测试直接钉住：

1. :func:`find_fields` —— 从一份 ``{"snapshot": true}`` 的元素行里认出用户名 /
   密码 / 提交控件。这比按页面文案猜表单可靠：``type=password`` 是一个确定的
   事实，"请登录"只是一句话。
2. :func:`build_actions` —— 把字段位置 + 凭据值拼成 fill / fill / submit 动作
   链。

**这个动作链里带着明文密码。** 它是 ``smart_fetch`` 的内部参数，不落任何出口：
响应回执与 ``DHOLE_USAGE_LOG`` 审计行都只记 :func:`actions.action_label`（记目标
不记文本），``actions.py`` 的失败日志同样只记 label，``_log_tool_call`` 从不写参数
值。凭据值能到达的地方只有浏览器输入框。

浏览器那一半在 ``server.py`` 的 ``login`` 方法里（它要 ``self.smart_fetch``），
不在这里 —— 分层的目的是让"认不认得出表单"这件事不需要起浏览器就能测。
"""

from __future__ import annotations

# 提交控件的名字里出现这些词就算提交键。中英各留几个常见写法：按钮上写的是
# "登录"还是"Sign in"不该改变结论。
SUBMIT_WORDS = (
    "登录", "登陆", "登入", "立即登录", "确认登录",
    "sign in", "signin", "sign-in", "log in", "login", "log-in",
    "submit", "continue", "next",
    "提交", "确定", "确认", "继续", "下一步",
)

# 用户名框的候选类型。"" 是没写 type 的 <input>（默认 text）。
_TEXT_LIKE = ("text", "email", "tel", "username", "")


def _looks_like_submit(name) -> bool:
    n = str(name or "").strip().lower()
    return bool(n) and any(w in n for w in SUBMIT_WORDS)


def _ref(row: dict) -> dict:
    """A field row reduced to what an action needs: its index and its name.

    The name rides along as ``expect`` so a stale index fails loudly (naming what
    the element reads now) instead of filling the wrong box with a password.
    """
    return {"index": int(row["index"]), "name": str(row.get("name") or "")[:80]}


def find_fields(elements) -> dict:
    """Locate the login form's controls in a snapshot's element rows.

    ``elements`` is the ``metadata.actions`` receipt of a ``{"snapshot": true}``
    action: ``[{index, tag, role, name, disabled?, input_type?}, ...]``.

    Returns::

        {"found": bool, "reason": str,
         "password": {index, name} | None,
         "username": {index, name} | None,
         "submit":   {index, name} | None,
         "submit_kind": "element" | "enter" | None}

    ``found=False`` means no password field at all — the honest answer for a page
    that is not a login form (or whose form lives in a frame the snapshot does not
    enumerate). ``submit_kind="enter"`` means no submit control was identified, so
    the plan is to press Enter in the password box.
    """
    rows = [e for e in (elements or [])
            if isinstance(e, dict) and isinstance(e.get("index"), int)
            and not isinstance(e.get("index"), bool)]
    rows.sort(key=lambda e: e["index"])

    pw = next((e for e in rows if e.get("input_type") == "password"
               and not e.get("disabled")), None)
    if pw is None:
        return {"found": False,
                "reason": ("no password field among this page's interactive "
                           "elements - it may not be a login form, or the form "
                           "sits in a frame the snapshot does not enumerate"),
                "password": None, "username": None, "submit": None,
                "submit_kind": None}

    # The username box is the LAST text-like input before the password box: a
    # form can carry a search box or a "remember me" above it, and the one that
    # belongs to the pair is the closest.
    user = None
    for e in rows:
        if e["index"] >= pw["index"] or e.get("disabled"):
            continue
        if e.get("tag") == "input" and e.get("input_type") in _TEXT_LIKE:
            user = e

    # An <input type=submit> is unambiguous, so it wins over the name heuristic.
    submit = None
    for e in rows:
        if e["index"] > pw["index"] and not e.get("disabled") \
                and e.get("input_type") == "submit":
            submit = e
            break
    if submit is None:
        for e in rows:
            if e["index"] <= pw["index"] or e.get("disabled"):
                continue
            if e.get("role") in ("button", "link") and _looks_like_submit(e.get("name")):
                submit = e
                break

    return {
        "found": True,
        "reason": "",
        "password": _ref(pw),
        "username": _ref(user) if user else None,
        "submit": _ref(submit) if submit else None,
        "submit_kind": "element" if submit else "enter",
    }


def build_actions(fields: dict, username: str, password: str,
                  confirm: bool = False) -> list[dict]:
    """The fill / fill / submit chain. **Contains the plaintext password.**

    Only ever passed to ``smart_fetch(actions=...)``. Nothing that leaves the
    process serialises this list — see the module docstring.
    """
    actions: list[dict] = []
    u = fields.get("username")
    if u is not None and username:
        actions.append({"fill": {"index": u["index"], "text": username,
                                 **({"expect": u["name"]} if u["name"] else {})}})
    p = fields["password"]
    actions.append({"fill": {"index": p["index"], "text": password,
                             **({"expect": p["name"]} if p["name"] else {})}})
    s = fields.get("submit")
    if s is not None:
        actions.append({"submit": {"index": s["index"], "confirm": bool(confirm),
                                   **({"expect": s["name"]} if s["name"] else {})}})
    else:
        # No submit control found: press Enter in the password box. Enter is
        # just as irreversible as a click, so it lives behind the same gate —
        # the gate is enforced one level up (with confirm=false the chain is
        # never built at all).
        actions.append({"press": {"index": p["index"], "key": "Enter"}})
    return actions


def describe_plan(fields: dict, url: str, credential: str) -> str:
    """One line naming what a confirmed login would do, for the approval gate."""
    if not fields.get("found"):
        return f"no login form was found on {url}"
    bits = []
    if fields.get("username"):
        bits.append(f"username -> [{fields['username']['index']}]")
    bits.append(f"password -> [{fields['password']['index']}]")
    if fields.get("submit"):
        bits.append(f"submit -> [{fields['submit']['index']}]"
                    f" ({fields['submit']['name'] or 'unnamed'})")
    else:
        bits.append("submit -> Enter in the password box")
    return (f"log in to {url} with vault credential {credential!r}: "
            + ", ".join(bits))
