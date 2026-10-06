"""cred_defaults 的错误面是全库唯一不过 redact 的 —— auth.type 原文无界回显，cookies 传错类型直接砸穿。

症状（2026-10-06 实测，全假值直调）：

    _identity_value_error('auth', {'type': 'Bearer ALICE-TOKEN-abc123'})
    → "auth: unsupported auth type 'Bearer ALICE-TOKEN-abc123' — it is an object: …"
      （原文回显，来自 server.py:2402 的 {auth.get('type')!r}，无长度帽）
    _identity_value_error('cookies', 42)
    → TypeError: 'int' object is not iterable（_safe_cookie_dict 直接迭代）
      —— handler 没接住，砸到 :7650 的 300 字符裸兜底，违反 docstring
      "the error names the seven keys" 的契约

对照：server.py 其余错误面共 10 个 redact_api_key 调用点，唯独 cred_defaults 拼
CredDefaultsModel.error 的这条路没有。诚实限定：回显的值来自本次调用参数（已在
转录），非新泄露；但注入可诱导模型把 token 贴进 auth.type，使错误文本无界携带
—— 放大格而非新泄露格。

判据：形状错误里不再回显调用方给的值（形状句保留、三 kinds 点名）；cookies
非可迭代返回既有 "cookies:" 形状句而不是 TypeError；cred_defaults 的 error 文本
与 redact_api_key(同文) 逐字一致。断言停在错误文本层，不构造真实凭据。
"""

from __future__ import annotations

import pytest

from dhole_mcp.security import redact_api_key
from dhole_mcp.server import MasterFetchServer, _identity_value_error


def test_a_malformed_auth_type_echoes_nothing_it_was_given():
    err = _identity_value_error("auth", {"type": "Bearer ALICE-TOKEN-abc123"})
    assert "ALICE-TOKEN" not in err
    assert err  # 仍是可行动的形状错误
    assert "bearer" in err and "basic" in err and "header" in err


def test_cookies_that_cannot_be_iterated_return_the_shape_error():
    err = _identity_value_error("cookies", 42)
    assert err.startswith("cookies:")
    assert "nothing usable" in err


@pytest.mark.asyncio
async def test_the_handler_returns_the_shape_error_not_a_transport_crash():
    """cookies=42 曾经砸穿 handler，落到 :7650 的 300 字符裸兜底。"""
    server = MasterFetchServer()
    r = await server.cred_defaults(set={"cookies": 42, "host": "api.example.com"})
    assert r.error
    assert r.error.startswith("cookies:")
    assert r.next_action  # 形状错误自带走向，不是裸兜底


@pytest.mark.asyncio
async def test_the_cred_defaults_error_face_passes_redact():
    """全库不变式：错误面全量过 redact —— cred_defaults 不再是唯一例外。"""
    server = MasterFetchServer()
    r = await server.cred_defaults(set={"auth": {"type": "sk-tinyfish-abcdef123456abcdef"}})
    assert r.error
    assert r.error == redact_api_key(r.error)
    assert "sk-tinyfish-abcdef123456abcdef" not in r.error
