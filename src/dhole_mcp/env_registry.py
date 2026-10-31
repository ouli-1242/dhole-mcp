"""每个 ``DHOLE_*`` 环境变量的机器可读注册表，外加一张读者读不懂的值的
进程级警告清单。

为什么一个文件干两件事（W1 + W4）：``security.py`` / ``robots.py`` /
``sessions.py`` **不经过** ``server.py`` 读环境变量。如果警告清单住在
``server.py``，它们就得 import 它，而 ``server.py`` 已经 import 了它们全部——
循环。本模块不 import 任何 ``dhole_mcp.*``，所以任何模块都能拿到它。

W1 · ``ALL`` 是唯一事实来源：``src/dhole_mcp/**.py`` 里每个 ``DHOLE_*`` 字符串
字面量都必须出现在这里（``tests/test_env_registry.py`` 双向检查），
``doctor`` / ``configuration.md`` 从它派生，而不是手工维护一份会漂移的平行
清单。

W4 · ``read_flag`` / ``read_int`` / ``read_float`` 保持既有语义，同时把无法
识别的值记进模块级 ``_warnings`` 列表，让 ``dhole --doctor``（W2）能把它翻
出来。一个回退到默认值的数字从前是静默事件；现在它恰好在一个地方可见。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Literal, Optional

# ─── 共享 token 集（W4.3） ─────────────────────────────────────────────
# ``ON_TOKENS`` / ``OFF_TOKENS`` 在这里导出，让 ``_env_flag`` 与 ``robots``
# 不再各自维护一份几乎重复的清单。从前 ``security._dns_recheck_enabled`` 里
# 还有第三份（它额外漏掉了 ``"" / none / null``）；那个旋钮已退役、没人读它，
# 所以那份副本也没了。
ON_TOKENS = frozenset({"1", "true", "yes", "on"})
OFF_TOKENS = frozenset({"0", "false", "no", "off", "none", "null"})


Kind = Literal[
    "flag",     # 开关型环境变量；见 ``read_flag``
    "int",      # 整数型环境变量；见 ``read_int``
    "float",    # 浮点型环境变量；见 ``read_float``
    "str",      # 自由文本
    "secret",   # API key —— 任何消费者都**绝不**回显
    "hostlist", # 逗号分隔的主机名/名字列表
    "path",     # filesystem path
    "url",      # URL
    "enum",     # 固定集合里的一个值
]
ReadAt = Literal["import", "call"]
FailSafe = Literal["open", "closed"]


@dataclass(frozen=True)
class EnvVar:
    """一颗环境变量，同时给人读、也给诊断读。

    ``read_at`` 是 W2 最需要的字段："call" 表示改宿主的 env 块会在下一次工具
    调用生效；"import" 表示进程必须重启，弄错这一点是用户以为配置改了却
    "没生效"的两大原因之一。

    ``fail_safe`` 标出一颗值出错时失败方向不安全的变量（D1）。目前只有 4 道闸：
    robots / CDP attach / SSRF recheck / 私网白名单。其它变量保持 W4.1 的承诺：
    没有开关会翻转语义。
    """

    name: str
    kind: Kind
    default: str
    read_at: ReadAt
    scenario: str
    summary: str
    bounds: Optional[tuple] = None
    fail_safe: Optional[FailSafe] = None
    read_by: tuple[str, ...] = ()

    def display_value(self, raw: Optional[str]) -> str:
        """W2 打印这颗 env 值时该长什么样。

        ``secret`` 永不显示值——用 ``len`` 读出的长度可以，字节不行。
        ``hostlist``/``path``/``str``/``url`` 原样显示；诊断从不解析它。
        """
        if raw is None:
            return "<unset>"
        if self.kind == "secret":
            return f"set (len={len(raw)})"
        return raw


# ─── the surviving env variables (17.0 大众化一刀切；见 plan §3-20) ──────
ALL: tuple[EnvVar, ...] = (
    EnvVar(
        name="DHOLE_ALLOW_PRIVATE_HOSTS",
        kind="hostlist",
        default="",
        read_at="call",
        scenario="fetching intranet / local dev services",
        summary="Comma-separated hostnames exempted from the SSRF guard.",
        fail_safe="closed",
        read_by=("security", "server"),
    ),
    EnvVar(
        name="DHOLE_BOCHA_API_KEY",
        kind="secret",
        default="",
        read_at="call",
        scenario="poor search results / want a different pool",
        summary="Bocha search API key; keyed backend, only runs when named.",
        read_by=("search_metasearch",),
    ),
    EnvVar(
        name="DHOLE_BRAVE_API_KEY",
        kind="secret",
        default="",
        read_at="call",
        scenario="poor search results / want a different pool",
        summary="Brave official search API token; keyed backend, only runs when named.",
        read_by=("search_metasearch",),
    ),
    EnvVar(
        name="DHOLE_BROWSER_IDLE_TIMEOUT",
        kind="int",
        default="1800",
        read_at="import",
        scenario="browser feels cold on every call",
        summary="Seconds idle before the warm browser is closed; 0 keeps it alive.",
        bounds=(0, 86400),
        read_by=("server",),
    ),
    EnvVar(
        name="DHOLE_DEEPSEEK_API_KEY",
        kind="secret",
        default="",
        read_at="call",
        scenario="poor search results / want a different pool",
        summary="DeepSeek search API key; keyed backend, only runs when named.",
        read_by=("search_metasearch",),
    ),
    EnvVar(
        name="DHOLE_DEFAULT_CONTENT_CHARS",
        kind="int",
        default="40000",
        read_at="import",
        scenario="context overhead too high",
        summary="Default body budget when the caller did not pass max_content_chars.",
        bounds=(500, 200000),
        read_by=("server",),
    ),
    EnvVar(
        name="DHOLE_DEFAULT_ENGINES",
        kind="hostlist",
        default=("baidu,bing,bing_global,duckduckgo,yahoo,brave,sogou,so360,"
                 "shenma,yandex,seznam,marginalia"),
        read_at="call",
        scenario="poor search results / want a different pool",
        summary="Override the keyless default engine pool.",
        read_by=("search_metasearch", "updater"),
    ),
    EnvVar(
        name="DHOLE_EXA_API_KEY",
        kind="secret",
        default="",
        read_at="call",
        scenario="poor search results / want a different pool",
        summary="Exa search API key; keyed backend, only runs when named.",
        read_by=("search_metasearch",),
    ),
    EnvVar(
        name="DHOLE_FIRECRAWL_API_KEY",
        kind="secret",
        default="",
        read_at="call",
        scenario="poor search results / want a different pool",
        summary="Firecrawl search API key; keyed backend, only runs when named.",
        read_by=("search_metasearch",),
    ),
    EnvVar(
        name="DHOLE_GOOGLE_API_KEY",
        kind="secret",
        default="",
        read_at="call",
        scenario="poor search results / want a different pool",
        summary="Google Programmable Search API key; keyed backend, only runs when named.",
        read_by=("search_metasearch",),
    ),
    EnvVar(
        name="DHOLE_GOOGLE_CX",
        kind="str",
        default="",
        read_at="call",
        scenario="poor search results / want a different pool",
        summary="Google Programmable Search Engine ID (cx) paired with the API key.",
        read_by=("search_metasearch",),
    ),
    EnvVar(
        name="DHOLE_HF_ENDPOINT",
        kind="url",
        default="",
        read_at="call",
        scenario="data location / privacy / diagnostics",
        summary="Hugging Face mirror for the rerank model download.",
        read_by=("reranker",),
    ),
    EnvVar(
        name="DHOLE_HOME",
        kind="path",
        default="~/.dhole",
        read_at="call",
        scenario="data location / privacy / diagnostics",
        summary="State directory (cache, cookie jar).",
        read_by=("cli", "paths", "search", "search_metasearch", "sessions", "updater"),
    ),
    EnvVar(
        name="DHOLE_HTTP_TOKEN",
        kind="secret",
        default="",
        read_at="import",
        scenario="serving dhole over HTTP beyond loopback",
        summary="Bearer token for the HTTP transport: when set, every /mcp call must carry it (Authorization: Bearer ... or X-Dhole-Token); unset keeps the open-door behavior.",
        read_by=("server",),
    ),
    EnvVar(
        name="DHOLE_PERSIST_CREDENTIALS",
        kind="flag",
        default="off",
        read_at="import",
        scenario="data location / privacy / diagnostics",
        summary="Persist session cookies and stored credentials to disk, encrypted "
                "(Windows DPAPI); the default keeps them in memory only - they end "
                "with the process.",
        fail_safe="closed",
        read_by=("identity", "sessions"),
    ),
    EnvVar(
        name="DHOLE_IGNORE_ROBOTS",
        kind="flag",
        default="off",
        read_at="call",
        scenario="compliance / fetch behavior",
        summary="Disable robots.txt compliance process-wide.",
        fail_safe="closed",
        read_by=("robots", "server"),
    ),
    EnvVar(
        name="DHOLE_RERANK_MODEL",
        kind="enum",
        default="bge-zh",
        read_at="import",
        scenario="model choice / speed vs Chinese quality",
        summary="Local reranker: bge-zh (default, bilingual, ~279MB) or mmarco "
                "(fast, ~119MB). First use downloads; restart to switch.",
        bounds=("bge-zh", "mmarco"),
        read_by=("reranker",),
    ),
    EnvVar(
        name="DHOLE_SEARCH_ROW_FILTER",
        kind="flag",
        default="on",
        read_at="call",
        scenario="search result quality",
        summary="Drop SEO-farm result rows (host shape + spam wording, only below the relevance line).",
        read_by=("search",),
    ),
    EnvVar(
        name="DHOLE_SEARCH_PROXY",
        kind="url",
        default="",
        read_at="call",
        scenario="search engines blocked / rate-limited / should use a proxy",
        summary="Search egress proxy, one URL (HTTPS/HTTP/ALL_PROXY as fallbacks).",
        read_by=("errors", "search", "search_engines", "search_metasearch", "search_proxy", "server"),
    ),
    EnvVar(
        name="DHOLE_QUERY_LOG",
        kind="flag",
        default="off",
        read_at="call",
        scenario="building a real-query evaluation set",
        summary="Write the queries you actually searched with to a local file; no results, no network.",
        read_by=("audit", "server"),
    ),
    EnvVar(
        name="DHOLE_TAVILY_API_KEY",
        kind="secret",
        default="",
        read_at="call",
        scenario="poor search results / want a different pool",
        summary="Tavily search API key; keyed backend, opt-in.",
        read_by=("search_metasearch",),
    ),
    EnvVar(
        name="DHOLE_TOOLS",
        kind="hostlist",
        default="",
        read_at="import",
        scenario="context overhead too high",
        summary="Register only the listed tools; a typo raises at import.",
        read_by=("server",),
    ),
    EnvVar(
        name="DHOLE_USAGE_LOG",
        kind="flag",
        default="off",
        read_at="call",
        scenario="data location / privacy / diagnostics",
        summary="Write a local call log; no parameter values, no network.",
        read_by=("actions", "server"),
    ),
)

_BY_NAME: dict[str, EnvVar] = {e.name: e for e in ALL}


def by_name(name: str) -> Optional[EnvVar]:
    """Look up one registry record by its exact name; ``None`` for a variable dhole does not own.
    """
    return _BY_NAME.get(name)


# ─── 进程级警告清单（W4.2） ─────────────────────────────────────────────
@dataclass(frozen=True)
class EnvWarning:
    """一个到达了某个读者、却解析不了的值。

    ``raw_value`` 是它在环境里的原样字符串；我们从不重写或归一化它。W2 会带着
    出问题的 env 名打印，让用户看到"你把 X 设成 abc，我们用了 40000"，而不是
    默默接受一次回退。
    """

    name: str
    raw_value: str
    message: str


_warnings: list[EnvWarning] = []


def record_warning(name: str, raw_value: str, message: str) -> None:
    """追加一条无法识别值的警告。从不抛；从不回显秘密。"""
    _warnings.append(EnvWarning(name=name, raw_value=raw_value, message=message))


def warnings_snapshot() -> tuple[EnvWarning, ...]:
    """警告清单的只读视图——W2 在 doctor() 里遍历它。"""
    return tuple(_warnings)


def clear_warnings() -> None:
    """丢掉所有已记录的警告。给测试用，不是给服务器用。"""
    _warnings.clear()


# ─── 值从哪来 ───────────────────────────────────────────────────────────
#
# 一颗旋钮两扇门。`env` 保持最高优先级，因为每个现有安装都已经这么配（README
# 的示例块也这么写），但 MCP 客户端配置里的 env 块意味着改一个数字都要编辑 JSON
# 并重启宿主——这正是按次读取的旋钮不再是配置、而开始变成基础设施的原因。
# `~/.dhole/config.json` 是同一批旋钮，不用重启：mtime 变了就重读，所以
# `dhole config set DHOLE_DEFAULT_CONTENT_CHARS 8000` 在下一次调用生效。
#
# 不动的是：DHOLE_HOME，因为它定位这个文件（bootstrap）；以及所有 import 时
# 读取的东西（DHOLE_TOOLS 之类）——按次重读没法取消注册一颗已经从 `tools/list`
# 里拿掉的工具。这些条目在这里仍可写，但 `dhole config` 会对它们说"重启宿主"，
# `dhole --doctor` 显示同一条注记。

_CONFIG_BASENAME = "config.json"

#: 留在环境里、刻意**不**从持久文件读的旋钮。每一颗在这里都是结构性原因，不是
#: 口味：
#:
#:   DHOLE_HOME              它定位这个文件，从文件里读它是循环（而且会静默忽略
#:                           测试与沙箱依赖的覆盖）。
#:   DHOLE_TOOLS             白名单：从调用方看不见的文件里遵守它，会让只设了一条
#:                           env 的人静默丢掉工具。它属于宿主可见的 env 块。
ENV_ONLY = frozenset({
    "DHOLE_HOME", "DHOLE_TOOLS",
})

_file_cache: dict[str, str] | None = None
_file_stamp: tuple[float, int] | None = None


def config_path():
    """``~/.dhole/config.json``（尊重 DHOLE_HOME）。惰性 import：本模块在状态目录
    存在之前就必须可 import。"""
    from dhole_mcp import paths
    return paths.home() / _CONFIG_BASENAME


def _load_file() -> dict[str, str]:
    """文件读成扁平的 ``{NAME: "value"}``，变了就重读。

    坏文件是一条已记录的警告，绝不崩溃，也绝不静默 no-op：一个多余的大括号
    不能一声不吭地关掉里面所有旋钮，也不能把服务器带下去。
    """
    global _file_cache, _file_stamp
    import json
    path = config_path()
    try:
        stat = path.stat()
        stamp = (stat.st_mtime, stat.st_size)
    except OSError:
        _file_cache, _file_stamp = {}, None
        return _file_cache
    if _file_cache is not None and stamp == _file_stamp:
        return _file_cache
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - a bad config must not break reads
        record_warning(_CONFIG_BASENAME, "",
                       f"{path} is not readable JSON ({type(exc).__name__}) - "
                       "every value in it is being IGNORED; fix or delete the file")
        _file_cache, _file_stamp = {}, None
        return _file_cache
    flat: dict[str, str] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, bool):
                flat[str(key)] = "1" if value else "0"
            elif value is None or isinstance(value, (str, int, float)):
                flat[str(key)] = str(value)
    _file_cache, _file_stamp = flat, stamp
    return flat


def file_entries() -> dict[str, str]:
    """持久配置当前贡献的值（给 CLI / doctor）。"""
    return dict(_load_file())


def source_of(name: str) -> str:
    """`env` / `file` / `unset`——这颗旋钮是从哪扇门出来的。"""
    if name in os.environ:
        return "env"
    if name in _load_file():
        return "file"
    return "unset"


def value_of(name: str) -> Optional[str]:
    """每个带类型读者都走的唯一查值入口：env 优先，然后文件。"""
    value = os.environ.get(name)
    if value is not None:
        return value
    return _load_file().get(name)


def set_entry(name: str, value: str):
    """持久化一颗旋钮。名字不在注册表里就抛。

    拒绝未知名字正是全部意义：写进文件的 `DHOLE_DEFUALT_CONTENT_CHARS` 看起来
    和一个生效的设置一模一样，而唯一诚实的抓获时刻就是写入那一刻。
    """
    import difflib
    import json
    from dhole_mcp import paths

    if by_name(name) is None:
        near = difflib.get_close_matches(name, [e.name for e in ALL], n=2, cutoff=0.6)
        raise ValueError(
            f"{name!r} is not a dhole configuration variable "
            f"({len(ALL)} registered - see `dhole config list`)"
            + (f"; did you mean {', '.join(near)}?" if near else "")
        )
    path = config_path()
    data = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except Exception:  # noqa: BLE001 - 覆盖坏文件，并且说出来
            pass
    data[name] = value
    paths.ensure_private_dir(path.parent)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths.harden_file(path)
    invalidate_file_cache()
    return path


def unset_entry(name: str) -> bool:
    """从文件里删掉一颗旋钮。删掉了东西就返回 True。"""
    import json
    path = config_path()
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False
    if not isinstance(data, dict) or name not in data:
        return False
    data.pop(name)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    invalidate_file_cache()
    return True


def invalidate_file_cache() -> None:
    """丢掉缓存的文件。测试把 DHOLE_HOME 指到一次性目录，而只按 mtime 做键的
    缓存可能在粗粒度时钟上熬过这次切换。"""
    global _file_cache, _file_stamp
    _file_cache, _file_stamp = None, None


def config_file_keys() -> list[str]:
    """config.json 里已设旋钮的**名字**（绝不是值），排好序。

    sys_state 的 `show=['config']` 只吃这一口：值里可能有 secret（对 secret 连
    长度以外的任何东西都不显示），而工具输出会落进 transcript——值走操作员侧
    的 `dhole config list`，那里有人看着。
    """
    return sorted(_load_file().keys())


# ─── 带类型的读者 ───────────────────────────────────────────────────────
#
# W4.1 让每个非安全开关的既有语义原样不变——重点在于"某个运维一直用
# DHOLE_MULTIMODAL=yes-please"不能因为我们加了诊断就静默失去能力。只有 4 道
# 安全闸（D1，标了 ``fail_safe="closed"``）获得方向纠正。


def read_flag(name: str, default: bool = False, *, fail_safe: bool = False) -> bool:
    """读一颗 on/off 环境变量。两个方向见 ``EnvVar.fail_safe``。

    ``fail_safe=False``（与 ``_env_flag`` 一致）：未设 -> ``default``；空串或
    在 ``OFF_TOKENS`` 里 -> False；其它 -> True，并对既不在 ``ON_TOKENS`` 也不在
    ``OFF_TOKENS`` 的值记一条警告。

    ``fail_safe=True``（安全闸）：值必须在 ``ON_TOKENS`` 或 ``OFF_TOKENS`` 里；
    任何无法识别的值都读成 False，所以把 ``false`` 拼成 ``flase`` 的
    DHOLE_IGNORE_ROBOTS 会让合规保持开启，而不是静默关闭。
    """
    raw = value_of(name)
    if raw is None:
        return default
    v = raw.strip().lower()
    if v == "":
        # 空串走 _env_flag 的"在 off 清单里"分支——False 且不告警（用户确实
        # 说了"关"，只是用空串说的）。
        return False
    if v in ON_TOKENS:
        return True
    if v in OFF_TOKENS:
        return False
    if fail_safe:
        record_warning(
            name, raw,
            f"{raw!r} is not a recognised on/off value - treated as OFF (fail-safe)",
        )
        return False
    record_warning(
        name, raw,
        f"{raw!r} is not a recognised on/off value - treated as ON",
    )
    return True


def read_int(
    name: str,
    default: int,
    *,
    lo: Optional[int] = None,
    hi: Optional[int] = None,
) -> int:
    """读一颗整数环境变量；声明了 bounds 时夹取。

    保持 ``_env_int`` 的语义：``int(float(raw))`` 失败的值返回 ``default``。
    新增的是把失败记下来，让 ``doctor`` 告诉运维，而不是让他猜。
    """
    raw = value_of(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = int(float(raw))
    except (TypeError, ValueError):
        record_warning(
            name, raw,
            f"{raw!r} is not an integer - using default {default}",
        )
        return default
    if lo is not None and val < lo:
        record_warning(
            name, raw,
            f"{raw!r}={val} is below the minimum {lo} - using {lo}",
        )
        return lo
    if hi is not None and val > hi:
        record_warning(
            name, raw,
            f"{raw!r}={val} is above the maximum {hi} - using {hi}",
        )
        return hi
    return val


def read_float(
    name: str,
    default: float,
    *,
    lo: Optional[float] = None,
    hi: Optional[float] = None,
) -> float:
    """``read_int`` 的浮点同胞。同样的夹取与告警规则。"""
    raw = value_of(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        record_warning(
            name, raw,
            f"{raw!r} is not a number - using default {default:g}",
        )
        return default
    if lo is not None and val < lo:
        record_warning(
            name, raw,
            f"{raw!r}={val:g} is below the minimum {lo:g} - using {lo:g}",
        )
        return lo
    if hi is not None and val > hi:
        record_warning(
            name, raw,
            f"{raw!r}={val:g} is above the maximum {hi:g} - using {hi:g}",
        )
        return hi
    return val


# ─── W2 配置审计用的有效性谓词 ─────────────────────────────────────────
def is_valid_value(entry: EnvVar, raw: str) -> tuple[bool, str]:
    """判断 ``raw``（字符串）能不能按 ``entry.kind`` 解析。

    返回 ``(ok, why)``。``why`` 是失败时给人读的原因；成功时为空。W2 用它把
    已设但非法的 env 标成 ✗，并生成 ``fix:`` 行，而不在这里复制一份解析规则。
    """
    if raw is None:
        return True, ""
    v = raw.strip().lower()
    if entry.kind == "flag":
        if v == "" or v in ON_TOKENS or v in OFF_TOKENS:
            return True, ""
        return False, "not a recognised on/off value (expected one of 1/true/yes/on or 0/false/no/off/none/null)"
    if entry.kind == "int":
        try:
            val = int(float(raw))
        except (TypeError, ValueError):
            return False, "not an integer"
        if entry.bounds:
            lo, hi = entry.bounds
            if val < lo or val > hi:
                return False, f"outside {lo}..{hi}"
        return True, ""
    if entry.kind == "float":
        try:
            val = float(raw)
        except (TypeError, ValueError):
            return False, "not a number"
        if entry.bounds:
            lo, hi = entry.bounds
            if val < lo or val > hi:
                return False, f"outside {lo:g}..{hi:g}"
        return True, ""
    # str / secret / hostlist / path / url / enum：每个非空字符串在结构上都
    # 合法；enum 的 bounds 在下面检查。
    if entry.kind == "enum" and entry.bounds:
        allowed = entry.bounds
        if isinstance(allowed, Iterable) and v not in {str(a).lower() for a in allowed}:
            return False, f"must be one of {sorted(allowed)}"
    return True, ""
