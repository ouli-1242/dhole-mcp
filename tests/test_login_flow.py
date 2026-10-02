"""独立登录流（计划第七章「独立登录流工具」）—— 表单识别 + 编排 + 凭据不出境。

三组断言，对应三个真实风险：

1. **认表单不靠猜**（`find_fields`）：`type=password` 是事实，"请登录"是一句话。
   用户名取密码框前**最近**的那个文本输入框（表单上方可能有搜索框）。
2. **审批闸是真的闸**：`confirm=false` 时一个字节都不填 —— 填本身就是不可逆的
   （页面上的输入事件能把值发出去），所以它和提交一起等在闸后。
3. **密码不出境**：动作链里带明文密码是不可避免的（浏览器要它），但它的每个
   出口都必须只记**目标**不记**文本** —— 响应回执（`action_label`）、失败日志、
   `metadata.actions`。这里的用例直接钉住那两条出口。

浏览器用桩替换，所以不需要真的起 Chromium。
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from dhole_mcp import login as login_mod
from dhole_mcp import vault as vault_mod
from dhole_mcp.actions import action_label, build_page_action
from dhole_mcp.server import LoginModel, MasterFetchServer, ResponseModel

SECRET = "hunter2-correct-horse"
USER = "alice@example.com"

# 靶站 /login 的元素行（label 不在 INTERACTIVE_SELECTOR 里，所以只有三个）。
LOGIN_ROWS = [
    {"index": 0, "tag": "input", "role": "textbox", "name": "用户名",
     "input_type": "text"},
    {"index": 1, "tag": "input", "role": "textbox", "name": "密码",
     "input_type": "password"},
    {"index": 2, "tag": "button", "role": "button", "name": "登录"},
]


def _run(coro):
    return asyncio.run(coro)


# ─── 表单识别（纯逻辑） ──────────────────────────────────────────────────

class TestFindFields:
    def test_the_fixture_login_form_is_recognised(self):
        got = login_mod.find_fields(LOGIN_ROWS)
        assert got["found"] is True
        assert got["password"] == {"index": 1, "name": "密码"}
        assert got["username"] == {"index": 0, "name": "用户名"}
        assert got["submit"] == {"index": 2, "name": "登录"}
        assert got["submit_kind"] == "element"

    def test_no_password_field_is_found_false_with_a_reason(self):
        got = login_mod.find_fields(
            [{"index": 0, "tag": "input", "role": "textbox", "name": "搜索",
              "input_type": "text"}])
        assert got["found"] is False
        assert "password" in got["reason"]
        assert got["password"] is None

    def test_an_empty_snapshot_is_found_false(self):
        assert login_mod.find_fields([])["found"] is False
        assert login_mod.find_fields(None)["found"] is False

    def test_a_disabled_password_field_is_ignored(self):
        rows = [dict(LOGIN_ROWS[1], disabled=True)]
        assert login_mod.find_fields(rows)["found"] is False

    def test_the_username_is_the_closest_text_field_before_the_password(self):
        """表单上方还有一个搜索框时，取的是紧挨着密码的那个。"""
        rows = [
            {"index": 0, "tag": "input", "role": "searchbox", "name": "搜索站点",
             "input_type": "search"},
            {"index": 1, "tag": "input", "role": "textbox", "name": "邮箱",
             "input_type": "email"},
            {"index": 2, "tag": "input", "role": "textbox", "name": "密码",
             "input_type": "password"},
        ]
        got = login_mod.find_fields(rows)
        assert got["username"] == {"index": 1, "name": "邮箱"}

    def test_a_text_field_after_the_password_is_not_the_username(self):
        rows = [
            {"index": 0, "tag": "input", "role": "textbox", "name": "密码",
             "input_type": "password"},
            {"index": 1, "tag": "input", "role": "textbox", "name": "验证码",
             "input_type": "text"},
        ]
        assert login_mod.find_fields(rows)["username"] is None

    def test_an_input_type_submit_wins_over_the_name_heuristic(self):
        rows = LOGIN_ROWS[:2] + [
            {"index": 2, "tag": "button", "role": "button", "name": "注册"},
            {"index": 3, "tag": "input", "role": "button", "name": "",
             "input_type": "submit"},
        ]
        assert login_mod.find_fields(rows)["submit"] == {"index": 3, "name": ""}

    def test_no_submit_control_falls_back_to_enter(self):
        got = login_mod.find_fields(LOGIN_ROWS[:2])
        assert got["submit"] is None
        assert got["submit_kind"] == "enter"

    @pytest.mark.parametrize("name", ["Sign in", "LOG IN", "登录", "提交", "Continue"])
    def test_common_submit_words_are_recognised(self, name):
        rows = LOGIN_ROWS[:2] + [
            {"index": 2, "tag": "button", "role": "button", "name": name}]
        assert login_mod.find_fields(rows)["submit"]["index"] == 2

    def test_a_nearby_button_with_no_submit_word_is_not_the_submit(self):
        rows = LOGIN_ROWS[:2] + [
            {"index": 2, "tag": "button", "role": "button", "name": "忘记密码"}]
        got = login_mod.find_fields(rows)
        assert got["submit"] is None and got["submit_kind"] == "enter"

    def test_malformed_rows_are_ignored_not_crashed_on(self):
        rows = [None, "junk", {"name": "no index"}, *LOGIN_ROWS]
        assert login_mod.find_fields(rows)["password"]["index"] == 1


class TestBuildActions:
    def test_the_chain_is_fill_fill_submit(self):
        fields = login_mod.find_fields(LOGIN_ROWS)
        got = login_mod.build_actions(fields, USER, SECRET, confirm=True)
        assert [list(a) for a in got] == [["fill"], ["fill"], ["submit"]]
        assert got[0]["fill"] == {"index": 0, "text": USER, "expect": "用户名"}
        assert got[1]["fill"] == {"index": 1, "text": SECRET, "expect": "密码"}
        assert got[2]["submit"] == {"index": 2, "expect": "登录", "confirm": True}

    def test_no_submit_control_becomes_enter_in_the_password_box(self):
        fields = login_mod.find_fields(LOGIN_ROWS[:2])
        got = login_mod.build_actions(fields, USER, SECRET, confirm=True)
        assert got[-1] == {"press": {"index": 1, "key": "Enter"}}

    def test_an_empty_username_skips_the_username_fill(self):
        fields = login_mod.find_fields(LOGIN_ROWS)
        got = login_mod.build_actions(fields, "", SECRET, confirm=True)
        assert [list(a) for a in got] == [["fill"], ["submit"]]

    def test_the_chain_passes_the_executor_s_own_validator(self):
        """构造出来的链必须是执行器接受的那种 —— 否则登录会以验证错误收场。"""
        from dhole_mcp.actions import validate_actions

        fields = login_mod.find_fields(LOGIN_ROWS)
        chain = login_mod.build_actions(fields, USER, SECRET, confirm=True)
        assert validate_actions(chain) == chain


class TestDescribePlan:
    def test_it_names_the_fields_and_the_submit(self):
        fields = login_mod.find_fields(LOGIN_ROWS)
        text = login_mod.describe_plan(fields, "https://x.test/login", "Example")
        assert "https://x.test/login" in text and "'Example'" in text
        assert "password -> [1]" in text and "submit -> [2]" in text
        assert SECRET not in text

    def test_the_enter_fallback_is_stated(self):
        fields = login_mod.find_fields(LOGIN_ROWS[:2])
        text = login_mod.describe_plan(fields, "https://x.test/", "Example")
        assert "Enter" in text


# ─── 值不出境 ────────────────────────────────────────────────────────────

class TestTheSecretNeverLeaves:
    def test_action_label_carries_the_target_not_the_text(self):
        """回执（响应 + metadata.actions + 写审计行）用的就是这个字符串。"""
        label = action_label({"fill": {"index": 1, "text": SECRET}})
        assert SECRET not in label
        assert "fill" in label and "[1]" in label

    def test_the_failure_log_carries_the_target_not_the_text(self, caplog):
        """失败日志曾经打印整个动作字典 —— 那会把密码写进日志。

        这是登录流的前置安全修复：响应、回执、审计行都小心不写值，唯独这条
        谁都不审的通道在泄漏。
        """
        class _Loc:
            def nth(self, i):
                return self

            @property
            def first(self):
                return self

            async def fill(self, text, timeout=None):
                raise RuntimeError("no such element")

        class _Page:
            async def evaluate(self, js, arg=None):
                return 9

            def locator(self, sel):
                return _Loc()

        async def run():
            action = build_page_action([{"fill": {"index": 1, "text": SECRET}}])
            await action(_Page())

        with caplog.at_level(logging.WARNING, logger="dhole-mcp.actions"):
            _run(run())
        assert caplog.text, "这个用例要先确认日志确实产生了，否则它什么都没验"
        assert SECRET not in caplog.text
        assert "[1]" in caplog.text


# ─── 编排（server.login） ────────────────────────────────────────────────

class _StubFetch:
    """Stand in for smart_fetch: answers the detect pass, then the act pass."""

    def __init__(self, elements=LOGIN_ROWS, login_check=None, submit_ok=True):
        self.elements = elements
        self.login_check = login_check
        self.submit_ok = submit_ok
        self.calls: list[dict] = []

    async def __call__(self, *args, **kw):
        # Set as a CLASS attribute, so it is not bound: `self.smart_fetch(...)`
        # arrives here with no positional server instance. `*args` swallows it
        # either way.
        self.calls.append(kw)
        url = kw.get("url", "")
        actions = kw.get("actions") or []
        if actions and actions[0].get("snapshot"):
            return ResponseModel(
                status=200, content=["请登录"], url=url, content_ok=True,
                metadata={"actions": [{"action": "snapshot interactive elements",
                                       "ok": True, "elements": self.elements}]})
        receipts = []
        for a in actions:
            (key, val), = a.items()
            if key == "fill":
                receipts.append({"action": f"fill [{val['index']}]",
                                 "ok": self.submit_ok})
            elif key == "submit":
                receipts.append({"action": f"submit [{val['index']}]", "ok": True,
                                 "submitted": True})
            elif key == "press":
                receipts.append({"action": f"press {val['key']!r}",
                                 "ok": True, "submitted": True})
        return ResponseModel(status=200, content=["欢迎回来"], url=url,
                             content_ok=True, metadata={"actions": receipts},
                             login_check=self.login_check)

    def act_calls(self):
        return [c for c in self.calls
                if c.get("actions") and not c["actions"][0].get("snapshot")]

    def detect_calls(self):
        return [c for c in self.calls
                if c.get("actions") and c["actions"][0].get("snapshot")]


@pytest.fixture
def wired(monkeypatch):
    """Stub both the browser and the vault; hand back (server, stub, secret_calls)."""
    def build(secret=None, **stub_kw):
        stub = _StubFetch(**stub_kw)
        monkeypatch.setattr(MasterFetchServer, "smart_fetch", stub)
        calls: list[tuple] = []

        def fake_secret(ref, source="auto"):
            calls.append((ref, source))
            if isinstance(secret, dict):
                return secret
            return {"username": USER, "password": SECRET, "totp": "",
                    "item": ref, "source": "bitwarden"}

        monkeypatch.setattr(vault_mod, "secret_for", fake_secret)
        return MasterFetchServer(cache_ttl=0), stub, calls
    return build


class TestTheApprovalGate:
    def test_confirm_false_fills_nothing_and_only_snapshots(self, wired):
        srv, stub, _ = wired()
        got = _run(srv.login(url="https://x.test/login", credential="Example"))
        assert got.pending_approval is True
        assert got.filled == 0 and got.submitted is False
        assert got.plan and "password -> [1]" in got.plan
        assert got.fields["password"] == {"index": 1, "name": "密码"}
        assert len(stub.calls) == 1, "未确认时只能有那一次只读快照"
        assert stub.detect_calls() and not stub.act_calls()

    def test_confirm_true_without_a_session_is_refused_before_any_browser_work(self, wired):
        srv, stub, _ = wired()
        got = _run(srv.login(url="https://x.test/login", credential="Example",
                             confirm=True))
        assert "session_id" in got.error
        assert stub.calls == [], "拒绝必须发生在开浏览器之前"

    def test_the_gate_message_tells_the_agent_to_ask_the_user(self, wired):
        srv, _, _ = wired()
        got = _run(srv.login(url="https://x.test/login", credential="Example"))
        assert "approve" in got.next_action.lower()
        assert "confirm=true" in got.next_action

    def test_a_page_with_no_password_field_never_reaches_the_gate(self, wired):
        srv, stub, _ = wired(elements=[
            {"index": 0, "tag": "input", "role": "textbox", "name": "搜索",
             "input_type": "text"}])
        got = _run(srv.login(url="https://x.test/", credential="Example"))
        assert got.error and "password" in got.error
        assert not stub.act_calls()
        assert "snapshot" in got.next_action, "要给出 agent 自己看快照的出路"


class TestTheConfirmedRun:
    def test_it_fills_submits_and_reports_logged_in(self, wired):
        srv, stub, _ = wired(login_check={"logged_in": True,
                                          "signals": ["logout control"],
                                          "cookie_names": ["sid"]})
        got = _run(srv.login(url="https://x.test/login", credential="Example",
                             session_id="acct", confirm=True))
        assert got.submitted is True and got.filled == 2
        assert got.logged_in is True
        assert got.cookie_names == ["sid"]
        assert got.session_id == "acct"
        assert got.next_action == "", "登录成功了就没有下一步"
        acts = stub.act_calls()
        assert len(acts) == 1
        chain = acts[0]["actions"]
        assert [list(a) for a in chain] == [["fill"], ["fill"], ["submit"]]
        assert acts[0]["check_login"] is True
        assert acts[0]["session_id"] == "acct"

    def test_explicit_indices_skip_the_detection_pass(self, wired):
        srv, stub, _ = wired(login_check={"logged_in": True})
        got = _run(srv.login(url="https://x.test/login", credential="Example",
                             session_id="acct", confirm=True,
                             username_index=0, password_index=1, submit_index=2))
        assert got.submitted is True
        assert not stub.detect_calls(), "给了索引就不该再花一次快照"
        assert len(stub.act_calls()) == 1

    def test_logged_in_false_is_a_verdict_not_a_crash(self, wired):
        srv, _, _ = wired(login_check={"logged_in": False,
                                       "signals": ["password field"]})
        got = _run(srv.login(url="https://x.test/login", credential="Example",
                             session_id="acct", confirm=True))
        assert got.logged_in is False and got.submitted is True
        assert "CAPTCHA" in got.next_action or "2FA" in got.next_action

    def test_logged_in_null_is_preserved_not_flattened_to_false(self, wired):
        """null（探针答不了）≠ false（没登录）—— 与 watch 的 changed 同一条纪律。"""
        srv, _, _ = wired(login_check={"logged_in": None})
        got = _run(srv.login(url="https://x.test/login", credential="Example",
                             session_id="acct", confirm=True))
        assert got.logged_in is None
        assert "probe could not answer" in got.next_action

    def test_a_failed_fill_is_reported_with_the_way_out(self, wired):
        """填失败但提交照跑（执行器逐条动作，一条失败不打断后面）—— 失败要说出来。

        这条同时钉住分支顺序：探针答不了（logged_in=None）**不能**把"有动作没
        跑成"这条更具体的信号盖掉。
        """
        srv, _, _ = wired(submit_ok=False, login_check={"logged_in": None})
        got = _run(srv.login(url="https://x.test/login", credential="Example",
                             session_id="acct", confirm=True))
        assert got.filled == 0, "失败的 fill 不算填上"
        assert "re-detect" in got.next_action
        assert "fill [1]" in got.next_action, "要指名哪一条动作没跑成"

    def test_a_successful_login_outranks_a_failed_action(self, wired):
        """登录判定为真时，一条没跑成的动作不该把结论盖成"去重新识别"。"""
        srv, _, _ = wired(submit_ok=False,
                          login_check={"logged_in": True, "cookie_names": ["sid"]})
        got = _run(srv.login(url="https://x.test/login", credential="Example",
                             session_id="acct", confirm=True))
        assert got.logged_in is True and got.next_action == ""

    def test_a_missing_credential_is_clean_and_opens_no_browser(self, wired):
        srv, stub, _ = wired(secret={"error": "no credential manager is available"})
        got = _run(srv.login(url="https://x.test/login", credential="nope",
                             session_id="acct", confirm=True))
        assert got.error and "no credential manager" in got.error
        assert stub.calls == []

    def test_the_credential_is_looked_up_by_name_with_the_source(self, wired):
        srv, _, calls = wired()
        _run(srv.login(url="https://x.test/login", credential="Example",
                       source="onepassword"))
        assert calls == [("Example", "onepassword")]

    def test_an_empty_url_or_credential_is_refused(self, wired):
        srv, stub, _ = wired()
        assert "url" in _run(srv.login(url="", credential="Example")).error
        assert "credential" in _run(
            srv.login(url="https://x.test/", credential="")).error
        assert stub.calls == []


class TestNoSecretInTheResponse:
    def _blobs(self, model) -> str:
        from dhole_mcp.server import _wire_json

        parts = [model.model_dump_json()]
        text, _payload = _wire_json(model)
        parts.append(text)
        return "\n".join(parts)

    def test_the_pending_plan_has_no_password(self, wired):
        srv, _, _ = wired()
        got = _run(srv.login(url="https://x.test/login", credential="Example"))
        blob = self._blobs(got)
        assert SECRET not in blob
        assert USER not in blob, "用户名也不回 —— 回执点名的是 vault 条目名"

    def test_the_confirmed_receipt_has_no_password(self, wired):
        srv, _, _ = wired(login_check={"logged_in": True, "cookie_names": ["sid"]})
        got = _run(srv.login(url="https://x.test/login", credential="Example",
                             session_id="acct", confirm=True))
        blob = self._blobs(got)
        assert SECRET not in blob and USER not in blob
        assert got.credential == "Example"

    def test_the_login_model_has_no_field_that_could_hold_the_username_or_password(self):
        fields = set(LoginModel.model_fields)
        assert not {f for f in fields
                    if "password" in f or "username" in f or "secret" in f}, \
            f"LoginModel 出现了可能承载凭据的字段：{sorted(fields)}"


class TestWireGating:
    def test_the_credential_tools_are_registered_by_default(self):
        """17.0：没有 opt-in 组 —— vault/login 默认注册，许可纪律在工具内部
        （login 无 confirm 必暂停；值永不回显）。"""
        from dhole_mcp.server import _ENABLED_TOOLS

        assert {"vault", "login"} <= _ENABLED_TOOLS

    def test_the_vault_and_login_tool_defs_are_advertised(self):
        from dhole_mcp.server import MasterFetchServer

        names = [td["name"] for td in MasterFetchServer._enabled_tool_defs()]
        for tool in ("vault", "login"):
            assert tool in names, f"{tool} 应该出现在默认 wire 里"

    def test_every_tool_def_argument_is_a_known_top_level_arg(self):
        """schema 里宣传的参数必须真的会被 _dispatch 读 —— 否则是一个安静的谎。"""
        from dhole_mcp.server import _TOP_LEVEL_ARGS, MasterFetchServer

        for td in MasterFetchServer._TOOL_DEFS:
            if td["name"] not in ("vault", "login"):
                continue
            declared = set(td["inputSchema"]["properties"])
            accepted = set(_TOP_LEVEL_ARGS[td["name"]])
            assert declared <= accepted, (
                f"{td['name']} 宣传了 dispatcher 不接受的参数："
                f"{sorted(declared - accepted)}")

    def test_the_dispatcher_forwards_every_advertised_argument(self):
        """反向：每个被接受的参数都要有地方被读走。

        用 JSON 往返比对"宣传的名字集合 == 方法签名里的名字集合"，两边漏一个
        都会被抓住。
        """
        import inspect

        from dhole_mcp.server import _TOP_LEVEL_ARGS

        for tool in ("vault", "login"):
            sig = set(inspect.signature(getattr(MasterFetchServer, tool)).parameters)
            sig.discard("self")
            assert sig == set(_TOP_LEVEL_ARGS[tool]), \
                f"{tool} 的方法签名与 _TOP_LEVEL_ARGS 不一致"


class TestDispatchReachesTheTools:
    """`_dispatch` 才是线上真正的入口。

    一个方法存在、schema 宣传、但 `_dispatch` 里没有分支的工具，在 wire 上是
    「看得见、够不着」—— 这条断言走完整入口，不走方法。
    """

    @pytest.fixture
    def opted_in(self, monkeypatch):
        from dhole_mcp import server as sm

        monkeypatch.setattr(sm, "_ENABLED_TOOLS",
                            sm._ENABLED_TOOLS | {"vault", "login"})

    def test_vault_status_goes_through_dispatch(self, opted_in, wired):
        srv, _, _ = wired()
        _content, payload = _run(srv._dispatch("vault", {"op": "status"}))
        assert payload["op"] == "status"
        assert isinstance(payload.get("sources"), list)

    def test_login_goes_through_dispatch_and_the_gate_still_holds(self, opted_in, wired):
        srv, stub, _ = wired()
        _content, payload = _run(srv._dispatch(
            "login", {"url": "https://x.test/login", "credential": "Example"}))
        assert payload.get("pending_approval") is True
        assert not stub.act_calls(), "入口层也不能漏掉闸"

    def test_an_unknown_argument_is_refused_at_the_entry(self, opted_in, wired):
        srv, _, _ = wired()
        with pytest.raises(ValueError):
            _run(srv._dispatch("vault", {"op": "status", "password": "x"}))

    def test_a_disabled_credential_tool_is_refused_with_the_flag(self, wired, monkeypatch):
        """DHOLE_TOOLS 白名单点名之外的工具，入口要拒绝并点名那道白名单。"""
        from dhole_mcp import server as sm
        monkeypatch.setattr(sm, "_ENABLED_TOOLS", frozenset({"smart_fetch"}))
        srv, _, _ = wired()
        with pytest.raises(ValueError) as e:
            _run(srv._dispatch("vault", {"op": "status"}))
        assert "not enabled" in str(e.value) and "DHOLE_TOOLS" in str(e.value)
