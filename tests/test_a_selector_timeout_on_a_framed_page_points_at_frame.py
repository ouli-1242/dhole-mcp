"""R04-001：selector 超时且页面有子 frame 时，步级错误要指 frame: 寻址。

现场（round-04 P02）：iframe 内元素用裸 selector 三连败，各烧 10s，错误只说
"matched no element"——文档里有 frame: 拼法（actions.py _act_target 三种拼法），
失败现场也拿得到 page。页面有子 frame 是当场可知的一般事实：超时错误该把
「顶层没有、可能在 frame 里」这半句说出去。非 selector 失败、无 frame 页面，
一个字不加。
"""
from dhole_mcp.actions import _action_failure


class _FakePage:
    def __init__(self, child_frames):
        self.main_frame = "main"
        self.frames = ["main"] + list(child_frames)


def _timeout_exc():
    try:
        raise TimeoutError("Locator.fill: waiting for locator('#note') - "
                           "Timeout 10000ms exceeded.")
    except TimeoutError as e:
        return e


def test_selector_timeout_on_framed_page_points_at_frame():
    msg = _action_failure(_timeout_exc(), page=_FakePage(child_frames=["f1"]))
    assert "frame" in msg.lower()
    assert "iframe" in msg.lower()
    assert '"frame"' in msg, msg


def test_selector_timeout_without_frames_stays_quiet():
    msg = _action_failure(_timeout_exc(), page=_FakePage(child_frames=[]))
    assert "matched no element" in msg
    assert "iframe" not in msg.lower()


def test_non_selector_failure_gains_nothing():
    msg = _action_failure(RuntimeError("net::ERR_ABORTED"), page=_FakePage(["f1"]))
    assert "frame" not in msg.lower()
    assert msg == "net::ERR_ABORTED"
