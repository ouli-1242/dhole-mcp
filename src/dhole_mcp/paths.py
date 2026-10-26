"""运行期路径布局 —— dhole 持久化的每个文件都住在**一个**目录下。

dhole 写出的都是可再生的状态，而且只落在一个根下："这个工具在我机器上留下了
什么？"必须能在一个目录里回答完，而一条点名两个目录的卸载说明就是在其中一个上
撒谎。（``~/.dhole_mcp_cache/`` 在 17.0 B2 之前是第二个根 —— 把它折进来的迁移
已经删掉，现在没有任何东西读那条路径。仍留着它的 pre-14.3 安装会一直保留，
直到用户自己删。）本模块是这些路径的唯一真源：

    ~/.dhole/
      cache.db                 内容缓存（SQLite；外加 -wal/-shm 边车文件）
      sessions.db              cookie 罐（17.1+：仅当 DHOLE_PERSIST_CREDENTIALS=1；
                               值经 DPAPI 加密 —— 默认存储是内存）
      identity.db              存下的 cred_defaults（同一个开关、同一套加密）
      models/                  下载的模型文件（神经重排器）
      circuit_breaker.json     引擎冷却状态
      engine_stats.json        各引擎的解析产出（谁交回 0 条可用结果）
      usage.jsonl              选开的本地调用日志

刻意**不**在这里的东西：

* 浏览器 profile —— 每次会话一个 ``tempfile.mkdtemp()`` 目录，关闭时删除
  （瞬时草稿，不是状态；崩溃时留给操作系统临时目录清理器，而不是永远留在这个目录）；
* ``rapidocr`` 的 OCR 模型 —— 随那个包打包，不在这里下载。

只用标准库，且被 cache/reranker/search/updater 导入，所以绝不能导入第三方依赖。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_HOME_DIR_NAME = ".dhole"


def home() -> Path:
    """dhole 拥有的那一个目录：``~/.dhole``，或设置时的 ``DHOLE_HOME``。

    为什么要有覆盖：这个目录明文装着每一份抓回的页面正文（cache.db）、搜索查询
    与重排模型。在共享机器上，这些内容可能不该和其他文件放在一起，而位置过去是
    不可移动的。``DHOLE_HOME`` 也让测试或沙箱能把一切指向一个随手丢弃的目录。

    （17.0 B2 删掉了 pre-14.3 的缓存迁移，所以自定义 home 现在就是它字面的意思：
    不会再从 ``~/.dhole_mcp_cache`` 折进任何东西。）
    """
    raw = (os.environ.get("DHOLE_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / _HOME_DIR_NAME


def ensure_private_dir(path: Path | str) -> Path:
    """mkdir -p + 尽力而为的仅属主模式。从不抛异常。

    POSIX 拿到真正的 0700。Windows 基本忽略 chmod（由 NTFS ACL 说了算），所以在
    那里这是空操作，``DHOLE_HOME`` 才是真正的杠杆 —— 这件事明说，而不是假装位
    已经设上了。

    有 str 调用方（`sessions` / `identity` 传 `os.path.dirname`），所以路径在这里
    归一化，而不是要求每个调用方归一。`except` 刻意只接 OSError：当它过去吞掉所有
    异常时，一个 `str.mkdir` 的 AttributeError 藏在里面，存储后来才以 sqlite 的
    "unable to open database file" 死掉。
    """
    path = Path(path)
    try:
        path.mkdir(parents=True, exist_ok=True)
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


def harden_file(path: Path | str) -> None:
    """刚写下的状态文件尽力设为 0600。从不抛异常。

    在原子替换之后调用，免得一个临时文件带着页面内容一直全世界可读。
    """
    try:
        os.chmod(str(path), 0o600)
    except Exception:
        pass


def harden_dir(path: Path | str) -> None:
    """dhole 拥有的目录尽力设为 0700。从不抛异常。

    与 :func:`ensure_private_dir` 不同，刻意**不**创建目录：调用方保留自己的
    ``mkdir``，创建失败仍按原来的方式抛出，这里只收紧已经存在的东西。
    """
    try:
        os.chmod(str(path), 0o700)
    except Exception:
        pass


def cache_dir() -> Path:
    """装内容缓存 DB 的目录（就是 dhole home 本身）。"""
    return home()


def db_path() -> Path:
    """内容缓存的 SQLite 文件。"""
    return home() / "cache.db"


def models_dir() -> Path:
    """下载的模型文件所在目录（神经重排器）。"""
    return home() / "models"


def file(name: str) -> Path:
    """dhole home 直接下属的一个文件（``circuit_breaker.json`` …）。"""
    return home() / name


def profiles_dir() -> Path:
    """具名持久浏览器 profile 的父目录（``~/.dhole/profiles/``）。

    每个 profile 是一个完整的 Chrome user-data-dir：cookies、localStorage、登录
    状态。17.0 没有任何调用方给它取名（按环境的 ``DHOLE_BROWSER_PROFILE`` 已
    注销，按次参数要等 B5 那颗「设一次的默认身份」工具回来才有人传），所以这个
    目录通常不存在。
    它一旦存在就是 "keep me signed in" 的产物，里面每一项都是凭据级：创建时 0700
    （POSIX 上仅属主；Windows 上真正的边界是用户主目录下的 profile ACL）。
    """
    return ensure_private_dir(home() / "profiles")


#: 服务器侧两个根的**名字**。回执里写它们的绝对形式，就是把这台机器的目录布局与
#: 用户名交给调用方 —— 契约 C15（错误不泄漏本机绝对路径）在 `File not found:`
#: 那句上曾经被放宽（2026-10-09 审计 D11：实测错误里逐字带着
#: `D:\tools\dhole-mcp\.scratch\...`，而进程 cwd 在 MCP 宿主下就是宿主自己的安装
#: 目录）。可诊断性不该靠泄漏来换：调用方真正需要的是"你按什么顺序试了哪几处"，
#: 而那件事用名字说得清。
MASKED_CWD = "<server cwd>"
MASKED_HOME = "~"
#: 用户名出现在**任何一段**里都要折成这个名字（评审 R02-090）。
MASKED_USER = "<user>"


def _identity_names() -> tuple[str, ...]:
    """这台机器"是谁"的那几个写法：home 的最后一段 + `USERNAME` / `USER`。

    三个来源都要：Windows 的 `USERNAME` 与 POSIX 的 `USER` 在不同启动路径下各可能为空
    （服务化启动时 `USER` 常缺，`expanduser` 又可能给出改写过的家目录），而漏一个来源就是
    漏一次遮蔽。长名排在前面，免得短名先把长名的一段吃掉。少于两个字符的名字不参与：
    那种"名字"在任何一段正文里都会撞到，遮蔽会退化成涂掉句子。
    """
    names: set[str] = set()
    for candidate in (os.path.basename(os.path.expanduser("~") or ""),
                      (os.environ.get("USERNAME") or "").strip(),
                      (os.environ.get("USER") or "").strip()):
        if candidate and len(candidate) >= 2 and candidate not in ("~", ".", ".."):
            names.add(candidate)
    return tuple(sorted(names, key=len, reverse=True))


def mask_machine_paths(text: str) -> str:
    """把 ``text`` 里**本机**自己的几个根换成符号名。

    遮的是*服务器*拥有的两个根：进程工作目录（在 MCP 宿主下就是宿主自己的安装
    目录 —— 调用方从未提过的那个路径，见 ``_resolve_local_path``）与用户主目录。
    主目录即使由调用方自己打出也遮，因为用户名本身就是路径泄漏的一部分，而
    ``~/report.csv`` 是*更好*的诊断信息，不是更差的。

    不含本机根的路径原样返回：调用方自己编造出来的绝对路径，理应让他读回去。

    ……用户名例外，它无论出现在哪一段都遮（:func:`_identity_names`，评审
    R02-090）。这个例外不是新加的根：上面遮主目录的理由是"用户名跟着路径走"，
    而**前缀**规则看不见落在两个根之外的用户名。线上实测
    （``review/.runtime/p1/r98b_user_segment.json``）：另一个盘上临时根下的文件，
    位于名为 ``probe-of-<user>`` 的目录里（pytest 自己的命名恰好就是这个形状），
    在成功的 ``url`` 与 ``File not found`` 两句话里都逐字交回。

    长根排前面，所以嵌在 home 里的 cwd 保住自己更具体的标签。根之后的边界必须
    是真正的收尾 —— 任何**不是**路径名字符的字符（分隔符、引号、括号、空格、
    串尾）：没有它，一个更短的根会折掉只是以它开头的更长目录
    （``C:\\Users\\mel`` 被读成命中 ``C:\\Users\\me``）；而更严格的边界（只认
    分隔符或串尾）又会漏掉引号形式（``at 'D:\\tools\\dhole-mcp'``）以及同一个根
    的 POSIX 拼法，那正是根会泄漏的途径。两种拼法都遮，因为归一化后的路径只是
    同一个目录换了顶帽子。匹配大小写不敏感，因为 Windows 上同一个根常常写成
    ``c:\\users\\ouli``；而遮少了才是会泄漏的那一侧。
    """
    if not text:
        return text
    roots: list[tuple[str, str]] = []
    try:
        home_dir = os.path.expanduser("~")
        if home_dir and home_dir not in ("~", "/", "\\"):
            roots.append((home_dir, MASKED_HOME))
    except Exception:
        pass
    try:
        cwd = os.getcwd()
        if cwd:
            roots.append((cwd, MASKED_CWD))
    except Exception:
        pass
    #: 根之后必须**不是路径名字符**才算收尾：分隔符、引号、括号、空格、串尾都算，
    #: 而字母/数字/`-`/`_` 不算 —— 这一格守的仍是原来那个意图（`C:\Users\me` 不许把
    #: `C:\Users\mel` 折掉），只是把"只认分隔符或串尾"放宽到"认任何真正的收尾"：库交出的
    #: 消息常把路径打在引号里（`Package not found at 'D:\tools\dhole-mcp'`），那种形状
    #: 从前一个字都不改。
    #:
    #: `.` 单独一条判据（评审 R02-049）：从前它和字母数字并列，于是**句子里收尾的根**
    #: 整格漏掉（`searched under D:\tools\dhole-mcp.` —— 库里最常见的「在这里没找到」
    #: 就长这样，而同一句里的逗号冒号引号都遮）。句号是不是"名字的一部分"，看它后面
    #: 还有没有名字字符：`.mirror` / `.db` 是另一颗目录或扩展名（不许折），
    #: `.` 接串尾、空白或别的标点就是句子（要遮）。
    _root_boundary = r"(?![A-Za-z0-9_\-]|\.[A-Za-z0-9_\-])"
    for root, label in sorted(roots, key=lambda rl: -len(rl[0])):
        # 同一个根的两种拼法都要遮。`re.escape(root)` 在 Windows 上只生成反斜杠形式，
        # 而响应里到处是归一化过的 POSIX 路径（pathlib 的 `.as_posix()`、zip/fsspec 内部
        # 路径、`file:///` 那一格的形状）—— 换一种写法不是换一台机器。
        for spelling in dict.fromkeys((root, root.replace("\\", "/"),
                                       root.replace("/", "\\"))):
            text = re.sub(re.escape(spelling) + _root_boundary, label, text,
                          flags=re.IGNORECASE)
    #: 身份那一条：用户名是**内容**，不是前缀，所以它在任何一段里都算。边界只认字母/数字/
    #: 下划线连着的不算一段（`<user>project` 是另一颗目录，反向守卫钉着这一条），而 `-` 与 `/`
    #: 都算收尾 —— `probe-of-<user>` 正是被 `-` 连起来的一段，也是这条判据存在的全部理由。
    for name in _identity_names():
        text = re.sub(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])",
                      MASKED_USER, text, flags=re.IGNORECASE)
    return text



