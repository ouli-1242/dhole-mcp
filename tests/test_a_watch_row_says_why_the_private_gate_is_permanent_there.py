"""R02-001：watch 的私网错误行要说清「这一路为什么永远拒」与真出路。

现场（2026-10-26 · P01 何工 · S1 · review/round-02）：`dhole watch add
http://127.0.0.1:8399/changing` 收下 → `watch run` 永远 exit 2，错误行只复述
安全门原话（invalid_request: URL targets internal/private IP）。结构事实：
watch run 在**自己的 CLI 进程**里建 MasterFetchServer，MCP 服务内存里的
cred_defaults 豁免够不着它（默认 memory 模式，凭据不落盘）。add 时不拒是设计
（watch.py:_clean_url 注释在册）——缺的是错误行把这一句结构事实与真出路说出去。

契约（本文件钉住）：
  * 私网族的错误行追加 watch 语境指引（PERSIST 开关 / 换公网 URL）；
  * 其它错误行原样（不许顺手改写）。
"""

from __future__ import annotations

from dhole_mcp.ops import _watch_row_error_hint

PRIVATE = ("invalid_request: URL targets internal/private IP "
           "(127.0.0.1 in 127.0.0.0/8)")


def test_the_private_family_gets_the_watch_context():
    hint = _watch_row_error_hint(PRIVATE)
    assert hint, "私网族要有 watch 语境"
    assert "DHOLE_PERSIST_CREDENTIALS" in hint, hint
    assert "cred_defaults" in hint, hint


def test_other_errors_pass_through_untouched():
    err = "http_error_404: server returned error status"
    assert _watch_row_error_hint(err) == ""


def test_the_hint_is_appended_not_replacing():
    from dhole_mcp.ops import _watch_error_with_hint
    out = _watch_error_with_hint(PRIVATE)
    assert out.startswith(PRIVATE), "原话保留"
    assert "DHOLE_PERSIST_CREDENTIALS" in out
