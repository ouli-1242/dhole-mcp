"""--http 绑回环且无 token 时静默 —— 共享是模型固有属性，回环上同样成立。

症状（2026-10-06 复现，同进程双实例＝两个客户端）：B 零配置读走 A 存的
auth/proxy（SECRET-A-TOKEN），census 把 A 的会话名广播给所有客户端，C 无
confirm 抹掉命名会话与整个身份库；即便设了 token 也是全体共享一把。而启动
警告的条件是 `host not in ("127.0.0.1", "localhost", "::1") and not http_token`
—— 回环绑定（默认 `--host`）恰好被豁免，最常见的选择反而一句提醒都没有。

文档说了「默认不带鉴权」（README 传输段），没说「同端口所有客户端共享同一份
身份/会话/缓存」——说出口代替隔离（F-C2-J1 判决：不做 per-client 命名空间）。

判据：警告条件不再豁免回环（--http 且无 token 即打）；警告文本带上单主体共享
声明；真起 loopback 服务的实测属真实环境，停在单测层（源码结构守卫＋现有测试
同款 inspect.getsource 口径）。
"""

from __future__ import annotations

import inspect

from dhole_mcp.server import MasterFetchServer


def test_a_loopback_http_bind_without_a_token_prints_the_same_warning():
    src = inspect.getsource(MasterFetchServer.serve)
    cond = [line for line in src.splitlines() if "not http_token" in line]
    assert cond, "警告条件消失了"
    line = cond[0]
    assert "127.0.0.1" not in line and "localhost" not in line, (
        "回环绑定不应豁免警告 —— 默认 --host 恰恰是最需要听见这一句的场合")


def test_the_warning_names_the_single_principal_share():
    src = inspect.getsource(MasterFetchServer.serve)
    assert "shares one" in src, "警告文本要说出共享的是什么（存储身份/会话/缓存）"


def test_the_token_gate_behavior_is_unchanged():
    """token 设置后的既有行为不动：闸只在设了 token 时包上。"""
    src = inspect.getsource(MasterFetchServer.serve)
    assert "if http_token:" in src and "_TokenGate(app, http_token)" in src
