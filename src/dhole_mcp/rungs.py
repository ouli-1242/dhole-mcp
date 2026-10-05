"""Dhole 的反爬档位：一张表、它的说明，以及读这张表的几处查表。

从 server.py 原样搬出（17.0 B6 的第七刀上半，零行为变更）。值得单独一个文件是因为
伪装强度是调用方唯一能选、又最容易选错的一轴：选档而不是选实现，而"哪一档真的改了
浏览器参数"只有这一张表说了算。表与读表的 `_anti_detect()` 若分在两个文件，
就会有人往表里加一档而忘了拼法。

`_RUNG_TOOLS` 是这张表的第二道锁：线格式校验按它决定要不要把值的拼法问清楚，
工具按它决定收不收这个参数 —— 名单必须只有一份（14b 拆出 web_act 时漏过一次，
KeyError 直接顶到客户端）。对 server 的模块级名字零依赖，只用到 `identity` 与 `Any`。
"""

from typing import Any

from dhole_mcp import identity


# ─── 反爬档位（§3-19）─────────────────────────────────────────────
# 伪装强度是一条单调轴：调用方选档，不选实现。表里每一栏就是浏览器层真实收到的
# 参数，`auto` 逐位等于收编之前 web_read 的签名默认（画布噪声 / WebRTC 屏蔽 / CF
# 挑战处理全开、资源按当日规则裁、headless、channel 自动探测）——这一刀的定义是
# "收参数"而不是"改行为"，tests/test_anti_detect_b5.py 里那条
# test_auto_is_bit_for_bit_the_pre_refactor_default 红了就说明在改反爬效果。
#
# 两档之外不留逃生门，而表里没有 real_chrome 的位置：**没有任何一档传
# real_chrome=True**。`_launch_channel(False)` 已经用 `_detect_chrome_channel()`
# 在装了 Chrome 的机器上走 channel='chrome'，传 True 只是在没装的机器上把启动
# 钉死——那不是更强的反爬，是更脆的启动，还会让一档参数去改 B3 的装机承诺。
# useragent 同理不在表里：UA 必须与 TLS/指纹成套（HTTP 层配 primp 的
# impersonate_os，浏览器层配 _FINGERPRINT_PROFILES），手传一个会造出"UA 说
# Chrome、JA3 说别的"的自相矛盾指纹，比不设更容易被认出来。
# web_read 的完整阶梯（web_act 用下面的短指针，见其注释）。
ANTI_DETECT_DESC = """Browser stealth level: off, basic (canvas + WebRTC), auto (the tested default, answers Cloudflare), aggressive (auto + every resource). Omit = cred_defaults, else auto."""
# web_act 的短指针（G25 §4.3 去重）：完整阶梯只住在 web_read 一处——同一段
# 168 字符在 tools/list 上出现两次，第二份是纯重复。档位语义见 _ANTI_DETECT
# 映射表，这里只留"去哪读全的"。
ANTI_DETECT_DESC_SHORT = "Stealth rungs per web_read.anti_detect; omit = cred_defaults, else auto."

_ANTI_DETECT = {
    #        画布噪声, WebRTC 屏蔽, CF 挑战处理, 资源全加载
    "off":        (False, False, False, False),
    "basic":      (True, True, False, False),
    "auto":       (True, True, True, False),
    "aggressive": (True, True, True, True),
}

#: 哪些工具读这颗档位。名单只有一份：`_validate_tool_args` 按它决定"要不要把值的
#: 拼法问清楚"，工具自己按它决定要不要收这个参数 —— 两边各自列一遍，迟早漏一颗
#: （14b 拆出 web_act 时就漏过一次：那一路的转发不过问，KeyError 直接顶到客户端）。
_RUNG_TOOLS = frozenset({"web_read", "web_act"})


def _anti_detect(level: str) -> dict:
    """One rung of the ladder, spelled the way the browser tier reads it."""
    canvas, webrtc, challenge, everything = _ANTI_DETECT[level]
    return {"hide_canvas": canvas, "block_webrtc": webrtc,
            "solve_cloudflare": challenge, "load_everything": everything,
            "headless": True, "real_chrome": False, "useragent": None}


# 档位里有两颗是**启动级**的：画布噪声进 context 的 init script，WebRTC 屏蔽进
# chromium 的启动参数。浏览器一起来就改不动它们 —— 所以"这台浏览器是按哪一档起来的"
# 是一个有答案的问题，而下面三个函数就是它的问法。`solve_cloudflare` 与
# `disable_resources` 是逐次生效的，不在这个身份里。
_LAUNCH_HARDENING = ("hide_canvas", "block_webrtc")


def _launch_identity(**hardening: bool) -> tuple:
    """一档（或一台已起的浏览器）的启动级身份。"""
    return tuple(bool(hardening.get(k)) for k in _LAUNCH_HARDENING)


def _default_hardening() -> dict:
    """当下生效的那一档摊开的样子：cred_defaults 存的，缺省 auto。"""
    return _anti_detect(identity.get("anti_detect") or "auto")


def _session_identity(session: Any) -> tuple:
    return _launch_identity(hide_canvas=getattr(session, "_hide_canvas", False),
                            block_webrtc=getattr(session, "_block_webrtc", False))
