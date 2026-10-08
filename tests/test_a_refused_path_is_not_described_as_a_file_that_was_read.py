"""被门挡住的路径，不许被描述成"文件读过了，是内容坏了"。

`quality._agent_hints` 里那一串 `fetcher_used == "parse"` 的分支本来是修另一条的：
四种**内容**失败（坏 JSON、坏 YAML、不是 %PDF 的 .pdf、不支持的扩展名）都被送到"路径
没问题"那句去 —— 那次修复是对的。但它兜住了所有非 `File not found` 的 parse 报错，
于是**路径类**的拒绝也被一起兜了进去。

实测（2026-10-08）：

    doc_parse(file_path='C:\\Windows\\System32\\drivers\\etc\\hosts')
      error       = Access denied: path is in a restricted system directory (c:\\windows)
      diagnosis   = {'code': 'path_restricted',
                     'recover_with': 'choose a path outside the restricted system directory'}
      next_action = "the file was found and read; what failed is its contents...
                     Nothing to fix about the path."

同一份回执里，一处说"换个路径"，一处说"路径没什么可修的"，而**一个字节都没有读过**。
调用方照着 next_action 走就会去复查文件内容 —— 那条路上什么都没有。判据问的是这两句
不许互相矛盾，不是问某一句的措辞。

`File not found` 与四种内容失败各自的出路都保持原样：那三条本来就是对的，这批不许为了
补这一格把它们改坏。
"""
import asyncio
import json
import os

from dhole_mcp.server import MasterFetchServer


def _parse(**kw):
    return asyncio.run(MasterFetchServer().doc_parse(**kw))


def _windows_system_host_file():
    root = os.environ.get("WINDIR") or r"C:\Windows"
    return os.path.join(root, "System32", "drivers", "etc", "hosts")


def test_a_refused_path_says_the_path_was_refused():
    path = _windows_system_host_file()
    if os.name != "nt" or not os.path.exists(path):
        import pytest
        pytest.skip("受限系统目录的判据是 Windows 的形状")
    r = _parse(file_path=path)
    assert r.error.startswith("Access denied: path is in a restricted system directory")
    assert "PATH was refused" in r.next_action, r.next_action
    assert "Nothing to fix about the path" not in r.next_action, (
        "被拒的正是路径，却说路径没什么可修的")
    assert "found and read" not in r.next_action, (
        "一个字节都没读过，不能说我读过了")


def test_the_two_halves_of_the_receipt_agree():
    """diagnosis 与 next_action 必须给同一个下一步 —— 各说一半等于没说。"""
    path = _windows_system_host_file()
    if os.name != "nt" or not os.path.exists(path):
        import pytest
        pytest.skip("受限系统目录的判据是 Windows 的形状")
    r = _parse(file_path=path)
    recover = (r.diagnosis or {}).get("recover_with", "")
    assert "path" in recover and "path" in r.next_action.lower(), (recover, r.next_action)
    assert (r.diagnosis or {}).get("code") == "path_restricted"


def test_an_alternate_data_stream_refusal_names_the_stream(tmp_path):
    """:stream 那种形状走的是另一条拒绝，出路也不同（去掉流名，不是换目录）。

    流要**先存在**才谈得上被拒：不存在的流在路径解析那一步就 answered
    "File not found"（那也是实话，不是这条判据要测的形状）。
    """
    import pytest
    if os.name != "nt":
        pytest.skip("NTFS 备用数据流")
    marker = tmp_path / "report.txt"
    marker.write_text("正文", encoding="utf-8")
    stream_path = str(marker) + ":hidden.json"
    try:
        with open(stream_path, "w", encoding="utf-8") as f:
            f.write("{\"planted\": 1}")
    except OSError as e:                      # 非 NTFS / 卷不支持流
        pytest.skip(f"造不出备用数据流：{e}")
    r = _parse(file_path=stream_path)
    assert r.error.startswith("Access denied: path names an NTFS alternate data stream"), \
        r.error
    assert "stream" in r.next_action, r.next_action
    assert "Nothing to fix about the path" not in r.next_action


def test_a_content_failure_keeps_the_content_advice(tmp_path):
    """反向：那四种**内容**失败原本的出路一个字不许被这次修补改掉。"""
    bad = tmp_path / "broken.json"
    bad.write_text("{\"a\": ,,,}", encoding="utf-8")
    r = _parse(file_path=str(bad))
    assert r.error and "not_found" not in (r.error.lower()[:30])
    assert "found and read" in r.next_action, (
        f"内容失败被送到路径建议那边去了：{r.next_action[:160]}")
    assert "PATH was refused" not in r.next_action


def test_a_missing_file_still_names_the_paths_tried(tmp_path):
    r = _parse(file_path=str(tmp_path / "nope.pdf"))
    assert r.error.startswith("File not found")
    assert "cwd=" in r.next_action and "PATH was refused" not in r.next_action


def test_the_guard_is_not_a_knob():
    """拒绝得说清这是门不是旋钮：没有任何参数能开它。"""
    path = _windows_system_host_file()
    if os.name != "nt" or not os.path.exists(path):
        import pytest
        pytest.skip("受限系统目录的判据是 Windows 的形状")
    r = _parse(file_path=path, cwd=os.path.dirname(path))
    assert "no argument opens it" in r.next_action, r.next_action
    # 换个参数再来一次，答案必须一样（这不是能被绕过的门）
    again = _parse(file_path=path)
    assert again.error == r.error
    assert json.dumps(again.diagnosis, sort_keys=True) == json.dumps(r.diagnosis,
                                                                    sort_keys=True)
