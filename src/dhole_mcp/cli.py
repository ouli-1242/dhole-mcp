"""dhole 的 CLI 入口——也是安装坏掉时仍然必须做对的那件事。

本模块是 pip 入口（`dhole = dhole_mcp.cli:main`）。它刻意保持轻量：模块级
**没有**重 import。dhole.exe 跑起来时执行 `from dhole_mcp.cli import main`，
只会拖进 `dhole_mcp.__init__`（只有 `__version__`）和本模块（只用标准库）。
重的 server import 发生在 main() 内部、惰性进行。

17.0 B2-3：自愈从"跑一个子进程"变成"说一句话"。从前它会写
``~/.dhole/repair.py`` 并执行——停掉所有 dhole 进程，然后
``pip --force-reinstall`` 几百 MB——只要碰到**任何** ImportError，包括同一
环境里无关包抛出的那种。一次会毁掉它本该保护的状态的修复，由一句没人读的异常
来决定，不是恢复路径。留下的是：点名缺的那一件——模块没了就点模块，dhole 自己
的文件不一致就点**符号**——打印修那一件的命令，然后非零退出，让宿主不能把这
当成一个已经启动的服务器。
"""

from __future__ import annotations

import sys

_FIX_DEPS = """\
  dhole cannot start: {what}
  re-install dhole with its dependencies from the checkout, then restart your MCP host:
    python -m pip install -e ".[all]"
  (nothing was installed and no process was touched - 17.0 removed the unattended repair.)
"""

# R01-002：缺**符号**不是缺依赖。这里的 `.[all]` 会让用户白装几百 MB、什么也
# 不改变，因为坏掉的 import 两端都属于 dhole 自己。措辞注意：17.0 B2-3 的守卫
# （tests/test_self_heal_prints_only.py）禁止在任何会执行的字符串里出现 reinstall
# 旗标，所以这里点名真正能恢复代码的杠杆。
_FIX_CODE = """\
  dhole cannot start: {what}
  this is not a missing package - dhole's own files disagree with each other (one side
  imports a name the other does not have), so installing dependencies would change
  nothing. Restore the code, then restart your MCP host:
    in a checkout:  git pull   (or re-clone at the revision you want)
    from the index: python -m pip uninstall -y dhole-mcp
                    python -m pip install --no-deps dhole-mcp
  (nothing was installed and no process was touched - 17.0 removed the unattended repair.)
"""


def _startup_failure(exc: BaseException) -> str:
    """"无法 import"按形状各报一次：缺模块 vs 缺符号。

    CPython 把模块交给 `ModuleNotFoundError.name`，那一种那里就是全部故事。
    对 "cannot import name 'X' from 'Y'"，它交给 `name` 的是**模块 Y**（3.14
    实测），所以只读 `.name` 会丢掉唯一可行动的事实——符号 X，它住在 `str(exc)`
    里。解法也不同：第一种要它的依赖，第二种要 dhole 自己的文件重新一致。
    """
    text = str(exc)
    if "cannot import name" in text:
        return _FIX_CODE.format(what=text)
    return _FIX_DEPS.format(what=getattr(exc, "name", "") or text)


def main() -> int:
    """跑服务器；连 import 都做不到时，说清缺的是哪一件。

    帮助、命令目录与补全在 import ops **之前**就答完：那三件事只读
    ``cli_spec``，不该为了一张表付服务器导入的 0.4–0.6s。其余任何 argv
    （包括未知子命令、`-u`、一个坏掉的安装）都交给 ops —— 自愈文案必须还
    走原来那条路，否则坏安装会被这里误报成"用法错误"。
    """
    try:
        from dhole_mcp import cli_spec
        from dhole_mcp import cli_ui as ui

        ui.utf8_stdio()
        argv, color_err = ui.extract_color_flags(list(sys.argv[1:]))
        if color_err:
            print(ui.err(color_err), file=sys.stderr)
            return 2
        argv = cli_spec.reorder_globals(argv)
        if argv:
            first = argv[0].lower()
            if first in ("-h", "--help"):
                print(cli_spec.top_help())
                return 0
            if first == "help":
                return cli_spec.print_help(argv[1:])
            if first == "commands":
                return cli_spec.print_commands(argv[1:])
            if first in ("completion", "completions"):
                return cli_spec.print_completion(argv[1:])
            if (first in cli_spec.ALL_COMMANDS
                    and any(a in ("-h", "--help") for a in argv[1:])):
                print(cli_spec.command_help(first))
                return 0
            if any(a in ("-h", "--help") for a in argv):
                cmd = next((a.lower() for a in argv
                            if a.lower() in cli_spec.ALL_COMMANDS), None)
                print(cli_spec.command_help(cmd) if cmd else cli_spec.top_help())
                return 0

        from dhole_mcp.ops import main as _server_main
        return _server_main(argv) or 0
    except (ImportError, ModuleNotFoundError) as exc:
        # 缺的名字才是可行动的："No module named 'bs4'" 指向一条安装命令，
        # 而 "cannot import name X from Y" 指向一次装多少依赖都修不好的代码
        # 不一致。
        sys.stderr.write(_startup_failure(exc))
        return 1
