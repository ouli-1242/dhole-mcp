"""R02-003：401/403 的 diagnosis.recover_with 与 next_action 必须同一句话。

现场（2026-10-26 · P02 杜蘅 · S3 · review/round-02）：WSJ 401 →
`diagnosis.recover_with=''` 而同封回执的 next_action 教 cred_defaults——
机器可读孪生与散文出路打架。同一张 diagnosis 表里 `auth_required` 那行明明
有杠杆，状态码族却交白卷。

附带裁决（不修码）：401 不触发 archive 档是 in-the-open 的设计
（quality._should_try_archive docstring：login-gated 内容 archive 也不会有；
_ARCHIVE_FALLBACK_STATUSES=(404,410,451)）——真缺口是 web_read 的工具描述
「on a hard block」超承诺，本文件同时钉住描述要点名状态族。

契约（本文件钉住）：
  * 401 的杠杆 = cred_defaults 存凭据（与 next_action 同源）；
  * 403 的杠杆 = stealthy 重试 / 凭据 / archive / 换源；
  * web_read 描述的 archive 第三档要点名 404/410/451（不许「hard block」含糊）。
"""

from __future__ import annotations

from dhole_mcp.quality import row_diagnosis, status_verdict


def test_a_401_names_the_credential_lever():
    retryable, lever = status_verdict(401)
    assert retryable is False
    assert "cred_defaults" in lever, lever


def test_a_403_names_its_lever():
    retryable, lever = status_verdict(403)
    assert retryable is False
    assert lever, "403 的杠杆不该是空串"


def test_the_row_twin_carries_the_lever():
    d = row_diagnosis("http_error_401: server returned error status")
    assert d is not None and d["code"] == "http_error_401"
    assert "cred_defaults" in (d.get("recover_with") or ""), d


def test_other_4xx_keep_their_honest_empty_lever():
    _, lever404 = status_verdict(404)
    assert lever404 == "", "404 重发改变不了什么——空杠杆是对的，不许顺手填"


def test_the_tool_description_names_the_archive_statuses():
    from dhole_mcp.server import MasterFetchServer
    read_def = next(d for d in MasterFetchServer._TOOL_DEFS if d["name"] == "web_read")
    desc = read_def["description"]
    assert "404/410/451" in desc, "描述要点名 archive 档触发的状态族"
