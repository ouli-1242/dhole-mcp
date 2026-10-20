"""一份被拒的凭据不许把"该长什么样"说两遍（2026-10-08 验收 · D-01）

现场（真实调用，值是假的、也没存进去）：

    cred_defaults(set={auth: "Bearer dummy-not-a-real-token", host: "api.example.test"})
      error       : "auth: auth must be an object: {type:'basic',username,password} /
                     {type:'bearer',token} / {type:'header',name,value} — it is an object:
                     {type:'basic',username,password}, {type:'bearer',token} or
                     {type:'header',name,value}"

同一个字符串里两份全文 —— 一份来自 raise 的那位，一份是调用方自己追加的。web_read
那颗的 `error` + `next_action` 是同一件事的两个作者（实测 `invalid auth: auth must be
an object: …` 之后 next_action 又把三形状列一遍）。重复不增加任何信息，只让读到第二
份的那位怀疑自己漏了什么，而且给"两份会不会有一天不一样"留了口子。

修法：三形状列表有了**唯一作者**（`server._AUTH_SHAPES`），谁 raise 谁说一次；
`_identity_value_error` 与那颗 `next_action` 不再自带副本，改为指回同一份回执里的
`error`。不回显调用方原文的那条安全性质（"unsupported auth type" 固定句）一并锁住。
"""

from __future__ import annotations

import pytest

from dhole_mcp.server import (_AUTH_SHAPES, MasterFetchServer, _auth_request_headers,
                              _identity_value_error)

FAKE = "Bearer dummy-not-a-real-token"
#: 列表里最不像别处会出现的一段 —— 数它出现几次就是数列表被说了几遍。
PROBE = "{type:'bearer',token}"


def _said_once(text: str, where: str) -> None:
    assert text.count(PROBE) == 1, f"{where} 把形状列表说了 {text.count(PROBE)} 遍: {text}"


class TestTheRefusalNamesTheShapeOnce:

    @pytest.mark.parametrize("bad", [
        FAKE,                       # 一个字符串
        42,                         # 一个数
        ["a"],                      # 一个列表
        {"type": FAKE},             # type 里贴了 token（原文不许回显）
        {"typ": "basic"},           # 认不出的键
    ], ids=["string", "int", "list", "token-in-type", "unknown-key"])
    def test_a_rejected_auth_value_is_described_exactly_once(self, bad):
        err = _identity_value_error("auth", bad)
        assert err, "拒了却不说是为什么"
        _said_once(err, "cred_defaults 的 error")

    @pytest.mark.asyncio
    async def test_the_tool_receipt_carries_no_second_copy(self):
        """整条 handler 路径（真实方法调用，值仍是假的）：一份回执一处形状句。"""
        r = await MasterFetchServer().cred_defaults(
            set={"auth": FAKE, "host": "api.example.test"})
        _said_once(r.error, "cred_defaults 回执的 error")
        assert PROBE not in r.next_action, r.next_action
        # 评审 R02-102：这一扇从前两种说法并存（门内写 `Nothing was stored.`、装配层的陌生
        # 参数名那颗听见 `Nothing was fetched or changed.`）。现在整扇说它自己那句
        # `Nothing was stored or forgotten.` —— 这一格守的仍是"拒绝要说这一颗没进库"，
        # 词换成那位唯一作者的句子。
        assert "Nothing was stored or forgotten." in r.next_action, r.next_action

    @pytest.mark.asyncio
    async def test_a_refusal_stores_nothing(self):
        """修文案不能顺手改判据：被拒的这次仍然一颗都没存。"""
        server = MasterFetchServer()
        await server.cred_defaults(set={"auth": FAKE, "host": "api.example.test"})
        r = await server.cred_defaults(show=True)
        assert "api.example.test" not in str(r.rows), r.rows


class TestTheReadPathPointsInsteadOfRepeating:
    """`auth` 在 17.0 已经不从调用参数进来了 —— 它是 cred_defaults 存着的那一份，
    由 `_identity_for` 交回。这里代掉那一步，测的是读路径上的那颗拒门。"""

    @staticmethod
    def _stored_auth(monkeypatch, value):
        real = MasterFetchServer._identity_for

        def patched(self, url, **kw):
            values, note = real(self, url, **kw)
            values = dict(values)
            values["auth"] = value
            return values, note

        monkeypatch.setattr(MasterFetchServer, "_identity_for", patched)

    @pytest.mark.asyncio
    async def test_error_holds_the_shape_and_next_action_holds_the_way_out(self, monkeypatch):
        self._stored_auth(monkeypatch, FAKE)
        r = await MasterFetchServer().web_read(url="https://example.test/")
        assert "invalid auth:" in r.error, r.error
        _said_once(r.error + " " + r.next_action, "web_read 的整份回执")
        assert "No request was made" in r.next_action, r.next_action
        assert "`error` names the shape" in r.next_action, r.next_action
        assert r.status == 0 and not r.content, "形状错必须在任何一次外发之前停住"

    @pytest.mark.asyncio
    async def test_a_kind_specific_refusal_still_says_what_it_needs(self, monkeypatch):
        """只给了 username 的 basic：这一条本来就不需要全表，但也不能凭空少半句。"""
        self._stored_auth(monkeypatch, {"type": "basic", "username": "u"})
        r = await MasterFetchServer().web_read(url="https://example.test/")
        assert "username and password" in r.error, r.error
        assert PROBE not in r.error + r.next_action, r.error
        assert "No request was made" in r.next_action, r.next_action


class TestTheSingleAuthorStillCoversTheCases:

    def test_the_constant_is_the_only_place_the_list_is_written(self):
        import inspect

        import dhole_mcp.server as srv
        src = inspect.getsource(srv)
        assert src.count(PROBE) == 1, "形状列表又有了第二个作者"
        assert _AUTH_SHAPES.count(PROBE) == 1

    def test_every_raise_path_that_needs_the_menu_has_it(self):
        """没有形状句的那几条，都是已经点名的：它说得清自己缺什么。"""
        named_fields = {"type": "basic", "username": "u"}
        with pytest.raises(ValueError) as raised:
            _auth_request_headers(named_fields, None)
        assert "username and password" in str(raised.value)
        with pytest.raises(ValueError) as raised:
            _auth_request_headers({"type": "bearer"}, None)
        assert "non-empty string 'token'" in str(raised.value)
        with pytest.raises(ValueError) as raised:
            _auth_request_headers({"type": "header", "name": "X-Key"}, None)
        assert "non-empty string 'value'" in str(raised.value)

    @pytest.mark.parametrize("bad", [FAKE, {"type": "oauth"}], ids=["string", "bad-kind"])
    def test_the_menu_shows_up_in_the_message_that_fired(self, bad):
        with pytest.raises(ValueError) as raised:
            _auth_request_headers(bad, None)
        _said_once(str(raised.value), "raise 的原文")

    def test_the_refusal_never_echoes_what_it_was_given(self):
        """文案并了作者，安全性质不能跟着并掉：type 原文仍然不进错误面。"""
        err = _identity_value_error("auth", {"type": FAKE})
        assert "dummy-not-a-real-token" not in err, err

    def test_a_valid_shape_is_still_accepted(self):
        """反向锁：这一刀只搬文案，不动判据。"""
        headers, kind, names, ignored = _auth_request_headers(
            {"type": "bearer", "token": "t"}, None)
        assert headers == {"Authorization": "Bearer t"}
        assert (kind, names, ignored) == ("bearer", ("authorization",), None)
