# -*- coding: utf-8 -*-
r"""我们自己写的拒绝，不许由句子的第一个词命名。

fields.md 曾经把 `diagnosis.code` 承诺成"a stable snake_case enum"，而 `classify_error_text`
的最后一档是"拿首词当 code"。这一档对**外人写的句子**是对的（httpx 的原文、站方解析器的
报错 —— 总得给个名字，缺席比一个临时名字更坏）；对**本仓库作者写下的字面量**是错的：
第一个词是措辞的一部分，改写句子 code 就跟着改，而调用方被教的是"照 code 分支，别读散文"。
所以这里量两件事：产地写的每一个字面量都得有个不是措辞给的 token，以及手册那句承诺得
收窄到真话的范围（设计过的失败 = 产地写死的 token；外来的错误原文 = 首词，认不出就别猜）。

量出来的口径（`.scratch/liveverify/code_reachable2.py` + 本测试的收集器，2026-10-09；R02-130
把两扇状态门收进判据后重数，2026-10-18；R02-136 把模型名单改成扫出来、三副新壳的产地第一次
到场之后重数，同一日；R02-143 把「句子住在同模块 helper 的 return 里、构造点转手一格」那一族
收进语料后重数，2026-10-19，数字由 `review/.runtime/p1/r294_ruler_caliber_after_flow_fix.json`
量出；R02-145 的尺子半边把**下标写入**那一族（`rec["error"] = …`，住在正文 JSON 里的行）也收进
语料之后再一次重数，同日同档）：真会进 diagnosis 的**静态可判**串 **75 条** ——
65 条在前缀表、2 条在子串表、5 条自带 `token:` 记号、3 条由判据后面那档认下
（搜索门的参数拒绝按本门名册判，F113 加的那一档），**0 条落在首词兜底、0 条落空**。另有 39 处
的头是变量（异常原文、`str(e)[:200]`、`_tier_error_text(exc)`），那些正是"外人写的句子"，
兜底就是为它们存在的。这一跳新到场的是 5 句（`feed._grab` / `_grab_with_retry` 的
`body_too_large:` 与 `network_error:`、`_friendly_parse_error` 的 `not a feed:`、
`_since_cutoff` 的两支 `invalid_request:`），全部在册。

本文件自己也被这条判据打过五次，五处的修法都记在这儿免得下一位再走一遍：
  · **名单**（`DIAGNOSED_MODELS` / `DIAGNOSED_ROW_MODELS`）从前手抄，于是"谁有那一格"有了
    第二位作者：F113 装上格子的 `CrawlResponseModel` / `SearchResponseModel` 与本批装上格子的
    `FeedRefusalModel` 三副的产地从未进过语料，而旧前提腿只会问"名单里的都有格子吗"，
    问不出"有几家有格子却没进名单"（R02-136）。现在由 `_models_with_a_code_slot()` 扫
    `model_fields`，新壳自动到场 —— 上面那 5 条差额就是它第一次到场量出来的。
  · **`layer_of` 从前手抄判据的顺序**（前缀→包含→网络分类器→兜底），判据每长一档这份镜像就
    瞎一格：名单修好之后它把三面**已经在册**的参数拒绝读成 first-word。现在只问两位真相 ——
    `classify_error_text` 怎么答、册里有没有一行命中这句（R02-136 的第二半，反控制在
    `test_a_sentence_judged_by_a_later_step_is_not_read_as_invented`）。
  · **采集单位只认构造点**，于是"句子写在同模块 helper 的 `return` 里、构造点转手一格"这一族
    根本不在网里：R02-134 那三支（`HTTP {status}` / `feed too large (…)` / `since …`）就是这么
    一路绿灯到线上的（r284 实测 `FeedResult` 三处 + `CrawlPage` 一处全部 `error=变量`）。现在
    `helper_return_sentences()` 补一跳 —— 按**流**收而不是按"函数体里有字符串"收：r291 第一版
    照后者量，12 条里多数是误伤（插进句子中间的碎片 `detail = "the document is not well-formed
    XML"`、以及 `return None, "", ""` 那种空返回），镜像把对的读成红比漏报更贵。跨模块转手仍
    不在射程，那一半由 `test_the_hop_stops_at_a_forwarding_call` 明写着，不当成已覆盖。
  · **补进来的那一跳本身又是两位读者之一**（R02-143 的第二半 · r293 在真实树上量出）：第一版
    顺着 `error=<对象>.<格子>` 的宿主名去追返回值，又把 `if`/`or` 分支的整棵子树当成语料 ——
    于是 `server.py:8765` 的 `"HTTP tier"` / `"none"`（`_over_budget(stage, fetcher_used)` 递进去的
    **档位标签**）被读成了两句没有名字的拒绝。判据没变，变的是流精度：只认裸名字的转手，分支
    一格一格递归求解。这条也记在这儿，因为它的形状与上一批的 R02-136 完全同型 —— 一把尺补
    射程时，最容易犯的不是漏报而是把对的读成红，而"红里混着假的"会让下一位照着去改产品。
  · **还有第四档采集单位没在网里**（R02-145 的尺子半边 · r299 数出）：行是裸 dict 的时候，句子
    由 `rec["error"] = …` 的**下标**写入 —— 构造点关键字（`error=`）、属性赋值（`.error =`）、
    F117 那一跳（只认裸名字转手）三档都够不着，所以 `fields_no_match` 这一句在线上被调用方读到
    时，尺具一辈子没见过它（r301 实测：12 行的普通扇出里两行就是它）。补上 `assigned-subscript`
    这一档之后它当场红 —— 那一颗 code 由首词兜底按整颗词给，`recover_with` 交回空串。

修前唯一那条违约是备用数据流那句门：`_agent_hints` 按 `startswith` 认得出它、diagnosis 表
认不出，于是 code 空、整格缺席，而同一份回执的 next_action 照样说话 —— 一句两读者，只有一位
在册（现已补进 `_DIAGNOSIS_PREFIXES`，code `alternate_data_stream`）。本测试把这条分工钉住：
产地写下的每一个字面量，必须要么在三张表里，要么自己带着 `token:` 记号。

两处上一版探针的高估里，有一处已经在 R02-130 反过来修掉了：
  · `SysStateModel` / `CredDefaultsModel` 当时**没有** diagnosis 格子，它们的拒答串不参与
    本判据；R02-130 给这两副模型补上格子（`models.RefusalHandle`，与 feed 的行同一机制），
    于是它们的产地字面量从此参与判据 —— 口径变了，`TestThePremises` 那格跟着改；
  · `_invalid_request_result(url, msg)` 在**调用点之外**还拼 `invalid_request: `，
    所以 `Missing required argument(s)…` 上线是 `invalid_request: Missing…`，
    code 是那颗稳定的 token。前缀从这只函数自己的构造式里读，不写死在测试里；而一个
    函数只要构造**多于一种** error 形状（`web_read` 就是），它就不算转手壳 —— 免得拿
    一个在该调用点并不存在的公共前缀给它发证。
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import re
import sys
import textwrap

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from dhole_mcp import quality  # noqa: E402
from dhole_mcp.feed import FeedResult  # noqa: E402
from dhole_mcp.models import (BulkResponseModel, CredDefaultsModel, ResponseModel,  # noqa: E402
                              SysStateModel)
from dhole_mcp.quality import (_DIAGNOSIS_CONTAINS, _DIAGNOSIS_PREFIXES,  # noqa: E402
                               _LEADING_CODE_RE, classify_error_text)
from dhole_mcp.errors import classify_network_error  # noqa: E402

SRC_DIR = pathlib.Path(inspect.getsourcefile(quality)).parent


def _models_with_a_code_slot():
    """哪些模型**真的**有 `diagnosis` 那一格 —— 扫出来，不手抄（评审 R02-136）。

    上一版这里是一张人维护的名单，于是「谁有格子」这件事有了第二位作者：F113 给
    `CrawlResponseModel` / `SearchResponseModel` 装上格子、F114 给 `FeedRefusalModel` 装上格子，
    名单一个字未跟，那三副回执的产地从来没进过本判据的语料 —— 而它自己的前提腿只断言
    「名单里的都有格子」，对「多了一家有格子」不报红。真相住在模型的字段里，这里只读它。
    """
    from pydantic import BaseModel

    import dhole_mcp.crawl as _crawl
    import dhole_mcp.feed as _feed
    import dhole_mcp.models as _models
    import dhole_mcp.search as _search

    found: dict[str, type] = {}
    for mod in (_models, _feed, _crawl, _search):
        for name, obj in inspect.getmembers(mod, inspect.isclass):
            if (obj.__module__ == mod.__name__ and issubclass(obj, BaseModel)
                    and "diagnosis" in obj.model_fields and name != "RefusalHandle"):
                # `RefusalHandle` 是那副基类本身，不是任何一扇交出去的回执
                found[name] = obj
    return found


#: 行级回执（一次调用里的一行自己戴 error）与信封级（一副回执）分开数：判据问的是同一件事，
#: 但产地写法不同（`FeedResult(error=…)` 的行 vs `ResponseModel(error=…)` 的信封）。
_ROW_LEVEL = {"FeedResult"}
_SLOT_MODELS = _models_with_a_code_slot()
DIAGNOSED_MODELS = {n for n in _SLOT_MODELS if n not in _ROW_LEVEL}
DIAGNOSED_ROW_MODELS = {n for n in _SLOT_MODELS if n in _ROW_LEVEL}
NOT_DIAGNOSED_MODELS: set[str] = set()
TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{2,}$")


def head_of(node):
    """上线串的字面量头：常量、f-string 的第一个常量段、串接的左端。

    量不到（变量、调用、三元）交回 None —— 那些是外人写的句子，兜底正是为它们存在的。
    """
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        # 只有当第一段就是常量时，我们才看得见上线的头；以值开头的是转手，交回 None。
        first = node.values[0] if node.values else None
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
        return None
    if isinstance(node, ast.BinOp) and node.op.__class__.__name__ == "Add":
        return head_of(node.left)
    return None


def _interpolates_params(joined: ast.JoinedStr, params: list[str]) -> bool:
    """f-string 里插的名字有没有本函数的参数 —— 有才算"转手一句人话"。"""
    for value in joined.values:
        if isinstance(value, ast.FormattedValue):
            if any(getattr(node, "id", None) in params for node in ast.walk(value)):
                return True
    return False


def prefix_composers(src_dir=None):
    """自动读出两种接线：往上拼 `prefix: ` 的函数，和把参数转手给 error 的函数。

    只认**单一构造**的函数（`_invalid_request_result` 那种专职拒答壳）。一个函数若在-body
    里构造好几个不同形状的 error（`web_read` 就是），它就不是"转手"而是自己写话 —— 那些
    字面量已经以 envelope 槽位收到语料里了，把它们再当成每个调用点的公共前缀，等于用一
    个不存在的前缀给所有调用点发证。
    """
    composers, forwards = {}, {}
    for path in sorted((src_dir or SRC_DIR).glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = [a.arg for a in node.args.args]
            offset = 1 if params[:1] in (["self"], ["cls"]) else 0
            built = []
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Call):
                    continue
                if (getattr(inner.func, "id", None) or getattr(inner.func, "attr", None)
                        ) not in DIAGNOSED_MODELS:
                    continue
                error_kw = next((k.value for k in inner.keywords if k.arg == "error"), None)
                if error_kw is None:
                    continue
                unwrapped = error_kw
                if (isinstance(unwrapped, ast.Call) and len(unwrapped.args) == 1
                        and not unwrapped.keywords):
                    unwrapped = unwrapped.args[0]
                built.append(unwrapped)
            if len(built) != 1:
                continue
            unwrapped = built[0]
            if isinstance(unwrapped, ast.Name) and unwrapped.id in params:
                forwards[node.name] = (unwrapped.id, params, offset)
                continue
            if (isinstance(unwrapped, ast.JoinedStr) and len(unwrapped.values) > 1
                    and isinstance(unwrapped.values[0], ast.Constant)
                    and isinstance(unwrapped.values[0].value, str)
                    and unwrapped.values[0].value.endswith(": ")
                    # 插进去的必须真是本函数的参数 —— 否则这个前缀不是"调用点传进来的话"，
                    # 给每个调用发这张证就是替一个不存在的接线说话。
                    and _interpolates_params(unwrapped, params)):
                composers[node.name] = unwrapped.values[0].value
    return composers, forwards


def bound_arg(node, pname, params, offset):
    kw = {k.arg: k.value for k in node.keywords if k.arg}
    if pname in kw:
        return kw[pname]
    i = params.index(pname) - offset
    return node.args[i] if 0 <= i < len(node.args) else None


def _error_slot_names(tree):
    """构造点上被当 `error=` 交出去的**裸名字**（转手壳的那一处 —— 句子就住在被调式的返回值里）。

    只认裸 `Name`，不顺着 `Attribute`/`Subscript` 剥到宿主名（r293 量的正是这一处）：
    `error=result.error` 转手的是**返回对象的某一格**，不是"这个调用的返回值就是那句话"。
    照着宿主名去追返回值，收集器就会把那次调用的**参数标签**当成句子 —— `server.py:8765` 的
    `"HTTP tier"` / `"none"` 是 `_over_budget(stage, fetcher_used)` 的档位名，不是任何一句拒绝。
    那一类格子的写法（构造点写字面量、`result.error = …` 的赋值）本来就在
    `reachable_error_strings` 的 `envelope` / `assigned` 两档里；剩下那种"行住在 content 的
    JSON 里"（`rec["error"] = f"HTTP {status}"`）不在这一跳的射程，已另立 R02-145，不在这儿
    冒充看见。
    """
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if fname not in DIAGNOSED_MODELS and fname not in DIAGNOSED_ROW_MODELS:
            continue
        value = next((k.value for k in node.keywords if k.arg == "error"), None)
        if isinstance(value, ast.Name):
            names.add(value.id)
    return names


def _supply_sites(tree, slot_names):
    """`名字 = 本模块某函数的调用(...)` —— 连同该名字在元组解包里的**位置**。"""
    supplies = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        # `err = await _grab(...)` 与 `err = _grab(...)` 是同一件事：await 只是包装，
        # 不剥掉它，句子住在被 await 的那只函数里的这一族就整条断掉（r292 在真实副本上
        # 量的正是这一处：`feed._grab_with_retry` 里那句 `body, status, encoding, err, hdrs
        # = await _grab(...)`）。
        if isinstance(value, ast.Await):
            value = value.value
        if not isinstance(value, ast.Call):
            continue
        callee = getattr(value.func, "attr", None) or getattr(value.func, "id", None)
        if not callee:
            continue
        for t in node.targets:
            if isinstance(t, ast.Name):
                if t.id in slot_names:
                    supplies.append((callee, t.id, None))
            elif isinstance(t, (ast.Tuple, ast.List)):
                for i, e in enumerate(t.elts):
                    if isinstance(e, ast.Name) and e.id in slot_names:
                        supplies.append((callee, e.id, i))
    return supplies


def _returned_heads(func, position, funcs=None, depth=0, seen=None):
    """函数里每一处**会交到那一格**的静态头：只看 return，元组按位置取，同模块的转手接着追。

    r291 的第一版把函数体里所有静态串都收进来，误伤两类：插进句子中间的碎片
    （`detail = "the document is not well-formed XML"`，上线的头是 `not a feed: …`）与
    跟这一格无关的赋值。按**流**收之后，碎片不再是句子头，而真正写死在 return 上的裸句
    （R02-134 那三支的形状）一条也不会漏。第三类误伤由 r293 量出来、在这一版修掉：
    `if`/`or` 分支里**嵌套调用的参数标签**（`… or await _over_budget("HTTP tier", "none", …)`
    里的 `"HTTP tier"`）曾被当成句子 —— 参数是递给别人的话头，不是交到这一格的话，所以分支
    只许一格一格地**递归求解**，不许扫子树。

    接着追的条件与边界（r292 用真实副本量出来的形状：`feed._grab` 的裸句子要两跳才到场）：
    返回值是 Name → 在本函数里解析它被赋的值；返回值是**本模块有定义的那个函数**的调用 → 进
    那个函数继续。有界 ≤5 跳、带访问集，跨模块的调用（本文件里没有定义）就停 —— 那是把句子交给
    别处的作者写，不追（追过去 = 让一把尺去读另一个模块的内部）。
    """
    funcs = funcs or {}
    seen = seen or set()
    key = (func.name, position, depth)
    if depth > 5 or key in seen:
        return []
    seen.add(key)

    assigned = {}
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                elts = ([tgt] if isinstance(tgt, ast.Name)
                        else list(tgt.elts) if isinstance(tgt, (ast.Tuple, ast.List)) else [])
                for i, e in enumerate(elts):
                    if isinstance(e, ast.Name):
                        assigned.setdefault(e.id, []).append((node.lineno, e, i))

    heads = []

    def _resolve(expr, line, pos, trail=frozenset()):
        if expr is None:
            return
        head = head_of(expr)
        if head is not None:
            heads.append((line, head))
            return
        if isinstance(expr, ast.Await):
            _resolve(expr.value, line, pos, trail)
            return
        if isinstance(expr, ast.Name):
            for ln, _tgt, sub_pos in assigned.get(expr.id, []):
                # 同一条链上不许第二次解同一个名字（`err = err or "…"` 那种自我引用会绕圈）
                step = (func.name, expr.id)
                if step in trail:
                    continue
                _resolve(_assigned_value(func, _tgt), ln, sub_pos, trail | {step})
            return
        if isinstance(expr, ast.BoolOp):
            for operand in expr.values:
                _resolve(operand, line, pos, trail)
            return
        if isinstance(expr, ast.IfExp):
            for operand in (expr.body, expr.orelse):
                _resolve(operand, line, pos, trail)
            return
        if isinstance(expr, ast.Call):
            callee = getattr(expr.func, "attr", None) or getattr(expr.func, "id", None)
            inner = funcs.get(callee)
            if inner is not None and inner is not func:
                for ln, h in _returned_heads(inner, pos, funcs, depth + 1, seen):
                    heads.append((ln, h))

    for node in ast.walk(func):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        expr = node.value
        pos = position
        if position is not None and isinstance(expr, (ast.Tuple, ast.List)):
            if position >= len(expr.elts):
                continue
            expr = expr.elts[position]
            pos = None
        _resolve(expr, node.lineno, pos)
    return heads


def _assigned_value(func, target):
    """找到某个名字最近一次被赋的**值节点**（只用于同函数内的 Name 解析，不求值）。"""
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign):
            continue
        names = []
        for tgt in node.targets:
            if isinstance(tgt, ast.Name):
                names.append(tgt.id)
            elif isinstance(tgt, (ast.Tuple, ast.List)):
                names += [e.id for e in tgt.elts if isinstance(e, ast.Name)]
        if getattr(target, "id", None) in names:
            return node.value
    return None


def helper_return_sentences(src_dir=None):
    """同模块内**按流**收：句子写在 helper 的 `return` 里、由构造点一格一格转手进 error 的那些。

    射程写清楚，不许含糊（R02-143 · r284 / r291b / r292 三轮量出来的）：
      · **追**：`error=<名字>`，该名字由本模块某函数的调用赋值（元组解包按位置回到函数里取同一
        位置的返回值）；返回值是 Name → 在本函数内解析它被赋的值；返回值又是**本模块有定义**的
        另一个函数的调用 → 接着追（≤5 跳 + 访问集防环）。`feed._grab` → `_grab_with_retry` →
        构造点是**两跳** —— r292 在真实源码的副本上把 `_grab` 退回裸 `f"HTTP {status}"`，只走
        一跳的收集器当时没抓到，这一族的形状就是这么漏的。
      · **不追**：调用一个本模块没有定义的函数（作者住在别的模块，例如
        `quality.status_error_sentence` —— 那一头由它自己那副在册格子说话，这把尺不去读另一个
        模块的内部）；空串的返回值（那是"这里没有句子"，不是"有句子没名字"）；以及
        `error=<对象>.<格子>` 那种**转手返回对象的一格**（`_error_slot_names` 只认裸名字：照着
        宿主名追返回值，量到的会是那次调用的参数标签，r293 的 `"HTTP tier"` 误伤就是这么来的。
        那种格子由构造点与赋值两档收，剩下"行住在 content 的 JSON 里"那一族是 R02-145）。
    """
    root = src_dir or SRC_DIR
    found = []
    seen = set()
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        funcs = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.setdefault(node.name, node)
        supplies = _supply_sites(tree, _error_slot_names(tree))
        for callee, _name, position in supplies:
            func = funcs.get(callee)
            if func is None:
                continue
            for line, head in _returned_heads(func, position, funcs):
                if not head.strip():
                    continue
                # 同一行那一句由两条路走到（`_grab` 自己 + 经 `_grab_with_retry` 转手）：
                # 量到的是一条事实，不是两条
                key = (path.name, line, head)
                if key in seen:
                    continue
                seen.add(key)
                found.append((f"{path.name}:{line}", f"via-{callee}-return", head))
    return found


def reachable_error_strings(src_dir=None):
    """[(file:line, 槽位, 上线串头)] —— 只收静态写得出的字面量头。

    两种产地都算：构造点上直接写字面量的（R02-136 之前只有这一种），以及住在同模块
    helper 的 `return` 里、由构造点转手一格的（R02-143 —— `feed._grab` 的裸 `HTTP 404`
    就是靠这一跳才被这本网罩住的）。
    """
    root = src_dir or SRC_DIR
    composers, forwards = prefix_composers(root)
    found = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            where = f"{path.name}:{getattr(node, 'lineno', 0)}"
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                kw = {k.arg: k.value for k in node.keywords if k.arg}
                if name in DIAGNOSED_MODELS and "error" in kw:
                    found.append((where, "envelope", head_of(kw["error"])))
                elif name in DIAGNOSED_ROW_MODELS and "error" in kw:
                    found.append((where, "feed-row", head_of(kw["error"])))
                elif name in NOT_DIAGNOSED_MODELS:
                    pass                       # 没有格子，不参与本判据（见 TestThePremises）
                elif name in composers:
                    arg = kw.get("msg", kw.get("error"))
                    if arg is None:
                        arg = (node.args[1] if len(node.args) > 1
                               else (node.args[0] if node.args else None))
                    found.append((where, f"via-{name}",
                                  composers[name] + (head_of(arg) or "")))
                elif name in forwards:
                    pname, params, offset = forwards[name]
                    found.append((where, f"thru-{name}",
                                  head_of(bound_arg(node, pname, params, offset))))
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Attribute) and t.attr == "error":
                        found.append((where, "assigned", head_of(node.value)))
                    elif (isinstance(t, ast.Subscript)
                          and getattr(t.slice, "value", None) == "error"):
                        # R02-145 那一族：行是裸 dict（`rec["error"] = …`），住在正文的 JSON 里。
                        # r299 数出全仓 7 处这种写入，此前**三档采集单位一档都够不着** ——
                        # 尺子的覆盖面等于它的采集单位（R02-143 那条法在这一层的续）。
                        found.append((where, "assigned-subscript", head_of(node.value)))
    rows = [(w, s, h) for w, s, h in found if h]
    # 一跳的那一半（同模块 helper 的 return）：同一个头已经从构造点收到过就不重复计
    seen = {(s, h) for _w, s, h in rows}
    for w, s, h in helper_return_sentences(root):
        if (s, h) not in seen:
            seen.add((s, h))
            rows.append((w, s, h))
    return rows


def _registered_row(text: str):
    """这张册里有没有一行命中这句（只看有没有，不再抄一遍判据的先后）。"""
    for prefix, code, _r, _rec in _DIAGNOSIS_PREFIXES:
        if text.startswith(prefix):
            return "table-prefix", code
    for needle, code, _r, _rec in _DIAGNOSIS_CONTAINS:
        if needle in text:
            return "table-contains", code
    return None, None


def layer_of(text: str) -> tuple[str, str]:
    """这句 dhole 自己写的话，它的 code 是**谁**给的。

    从前这里手抄了一遍判据的顺序（前缀 → 包含 → 网络分类器 → 首词兜底），于是判据每多长一档
    这份镜像就瞎一格：F113 给 `classify_error_text` 加了「搜索门的参数拒绝按本门名册判」那一档，
    这里看不见，R02-136 把三副新壳的产地放进语料之后就当场把已经在册的三面读成 first-word。
    现在只问两处真相：**判据唯一的读者**怎么答（`classify_error_text`），以及**册里有没有一行**
    命中这句。兜底发明的那个词，只在读者答的正好是这句第一个词、而册又不认它时才成立 ——
    顺序不再由这份镜像复制，所以判据以后再加一档也不会再出现"尺具读不到"的那种红。
    """
    code, _retryable, _recover = classify_error_text(text)
    where, row_code = _registered_row(text)
    if where is not None and row_code == code:
        return where, code
    category, _hint = classify_network_error(text)
    if code and category != "unknown" and code == category:
        return "network-classifier", code
    if not code:
        return "uncoded", ""
    m = _LEADING_CODE_RE.match(text)
    if m and m.group(0) == code:
        # 读者答的正是这句的第一个词，而册里没有一行命中它 —— 那不是名字，是措辞
        if text[len(code):len(code) + 1] == ":":
            return "self-token", code
        return "first-word", code
    return "reader", code


class TestThePremises:
    """判据的两块地基：谁有 diagnosis 格子，以及这台探针真能认出违约。"""

    def test_only_the_diagnosed_models_carry_a_code_slot(self):
        # 上一版探针把 SysState/CredDefaults 的拒答也算成违约 —— 那两个模型根本没有
        # diagnosis 格子，于是判据去量一个不存在的字段。这行前提要是哪天变了（有人给
        # SysStateModel 加了 diagnosis），本测试会红，口径跟着改。
        # R02-130 就是那个"哪天"：两扇状态门现在有自己的格子（由模型自己从 `error` 填），
        # 所以它们的产地字面量从此进判据；Bulk 的行仍是 ResponseModel，本体不必有这一格。
        # R02-136 改了这一格的**做法**：名单不再由人抄，而是扫 `model_fields`。
        # 抄着的时候它只会问"名单里的都有格子吗"，永远问不出"有几家有了格子却没进名单"——
        # F113 / F114 各装了一副新壳而这一腿一路绿灯，就是那段历史的形状。
        assert "diagnosis" in ResponseModel.model_fields
        assert "diagnosis" in FeedResult.model_fields
        for model in (SysStateModel, CredDefaultsModel):
            assert "diagnosis" in model.model_fields, model.__name__
        assert "diagnosis" not in BulkResponseModel.model_fields
        for name in ("CrawlResponseModel", "SearchResponseModel", "FeedRefusalModel"):
            assert name in _SLOT_MODELS, f"这副回执有线上格子却没被扫到：{name}"
            assert name in DIAGNOSED_MODELS, f"{name} 的产地没进判据语料"

    def test_a_prose_sentence_does_land_in_the_first_word_layer(self):
        # 反向守卫：静态可判语料全绿不等于探针是瞎的。手工一句散文（小写起头，兜底的
        # 正则就长这样）必须落在 first-word，手工一个 `token:` 记号必须落在 self-token，
        # 一个大写起头的句子则落在 uncoded —— 三档都能认出，语料才不是空跑。
        assert layer_of("refusing to create that path for you")[0] == "first-word"
        assert layer_of("some_self_explaining: prose follows")[0] == "self-token"
        assert layer_of("Access denied: nothing we authored matches")[0] == "uncoded"

    def test_a_sentence_judged_by_a_later_step_is_not_read_as_invented(self):
        """反控制（R02-136 那次阅读器瞎掉的形状）：判据靠后面的档认下的句子，不许被读成首词。

        这三面是搜索门自己的参数拒绝（`_param_refusal` 装配，名字与别扇同一颗
        `invalid_request`）。旧那版 `layer_of` 手抄判据顺序、抄不到 F113 加的那一档，
        把它们读成 `first-word`（措辞发明的词）—— 一份镜像把已经在册的东西报成违约，
        比漏报更贵：下一次有人照着这条红去"修产品"，就会把对的改成错的。
        """
        for text in ("find_similar requires a url (pass url=, or the URL as query).",
                     "fetch_min_consensus must be 0 (off) or >= 2: every result has "
                     "at least one contributing index family, so a floor of 1 filters nothing.",
                     "fetch_min_consensus only applies with fetch_content=true - "
                     "without it nothing is auto-fetched, so the floor has nothing to gate."):
            layer, code = layer_of(text)
            assert code == "invalid_request", (text[:44], layer, code)
            assert layer not in ("first-word", "uncoded"), (text[:44], layer, code)

    def test_the_corpus_is_big_enough_to_mean_something(self):
        rows = reachable_error_strings()
        assert len(rows) >= 40, f"探针只收到 {len(rows)} 条 —— 收集逻辑八成断了"
        slots = {s for _w, s, _h in rows}
        heads = " | ".join(h for _w, _s, h in rows)
        # 四种接线各要有一条在册，少一种就是收集器断了（前缀壳 / 直接构造 / 串接左端 /
        # 赋值写入）。
        assert any(s.startswith("via-") for s in slots), slots
        assert "envelope" in slots and "assigned" in slots, slots
        assert "Access denied: path names an NTFS" in heads
        assert "invalid_request: " in heads
        assert "File not found: " in heads        # 串接（"…" + join(…) + "…"）的左端
        assert "ocr_failed: " in heads            # result.error = 的赋值写入
        # R02-145 的第四档：行是裸 dict 时由 `rec["error"] = …` 的下标写入（r299 数出 7 处，
        # 其中两处静态可读）。少这一档，住在正文 JSON 里的那些句子就永远不进判据。
        assert "assigned-subscript" in slots, slots
        assert "fields_no_match" in heads, heads


def _bare_status_head(head):
    """这一句是不是那句**裸的状态话**（`HTTP 404` / `f"HTTP {status}"` 两种写法同一个形状）。"""
    return bool(head) and (head.rstrip() == "HTTP" or bool(re.match(r"^HTTP \d{3}\b", head)))


def bare_status_writers(src_dir=None):
    """每一处往 error 格子交「裸状态话」的地方（四档写入全看，子树里翻）。

    这把尺问的是**形状**而不是头 —— 一句被 `(res.error or f"HTTP {res.status}")[:200]` 包起来的
    话，`head_of` 量不到（整值是 Subscript），但它上线时照样是那句没有名字的话。r289 是手工扫
    出来的（F116 收官那一趟），这条腿把那一次扫描长成一个闸：这一族的名字只许住在
    `quality.status_error_sentence` 那一位作者那里。
    """
    root = src_dir or SRC_DIR
    found = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            exprs = []
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name in DIAGNOSED_MODELS | DIAGNOSED_ROW_MODELS:
                    exprs = [k.value for k in node.keywords if k.arg == "error"]
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Attribute) and t.attr == "error":
                        exprs.append(node.value)
                    elif (isinstance(t, ast.Subscript)
                          and getattr(t.slice, "value", None) == "error"):
                        exprs.append(node.value)
            for expr in exprs:
                if expr is None:
                    continue
                for sub in ast.walk(expr):
                    head = head_of(sub)
                    if _bare_status_head(head):
                        found.append((f"{path.name}:{getattr(sub, 'lineno', 0)}", head))
    return found


class TestNoAuthoredRefusalUsesItsFirstWord:
    def test_no_authored_literal_is_named_by_its_first_word(self):
        offenders = []
        for where, slot, head in reachable_error_strings():
            layer, code = layer_of(head)
            if layer in ("first-word", "uncoded"):
                offenders.append(f"{where} [{slot}] layer={layer} code={code!r} "
                                 f"head={head[:70]!r}")
        assert not offenders, (
            "这些是 dhole 自己写下的字面量，却由措辞决定 code（或根本没有 code）：\n  "
            + "\n  ".join(offenders)
            + "\n修法二选一：在产地把串写成 `token: 句子`，或把这句加进 "
              "_DIAGNOSIS_PREFIXES（顺带给 recover_with 一句真杠杆）。")

    def test_no_authored_literal_writes_the_bare_status_shape(self):
        """一件事实一位作者：`HTTP <数>` 这一形状不许再被交进任何 error 格子（R02-145 的①半边）。

        F116 把 feed 那一支并回 `quality.status_error_sentence` 之后，r289 手工反扫还剩一处
        （`server._nested_read` 的 `rec["error"] = (res.error or f"HTTP {res.status}")[:200]`）。
        那条扫描今天长在这个文件里 —— 形状级的检查放在语料头之后会漏（那一句被 `or` 与 `[:200]`
        包着，`head_of` 量不到），所以这里翻**写入表达式整棵子树**。
        """
        writers = bare_status_writers()
        assert not writers, (
            "这几处还在把裸的状态话交进 error 格子（同一颗 4xx/5xx 在别处交的是 "
            "`http_error_<status>: …`，两位作者的名字对不上）：\n  "
            + "\n  ".join(f"{w} head={h!r}" for w, h in writers)
            + "\n修法：问那位唯一的作者 —— `quality.status_error_sentence(status)`。")

    def test_every_authored_literal_gets_a_snake_case_token(self):
        # fields.md 承诺的形状：小写字母起头、[a-z0-9_]。兜底给的外人词可能带连字符，
        # 但那不是本判据的射程；这里只量作者写的字面量。
        bad = [(w, h, layer_of(h)[1]) for w, _s, h in reachable_error_strings()
               if not TOKEN_RE.match(layer_of(h)[1])]
        assert not bad, [f"{w} head={h[:50]!r} code={c!r}" for w, h, c in bad]

    def test_the_two_readers_of_the_path_guard_agree(self):
        # 备用数据流那句门：`_agent_hints` 一直认得（quality.py 的 startswith 分支），
        # 而 diagnosis 表曾经不认 —— 于是同一份回执里一个读者说话、另一个格子缺席。
        ads = "Access denied: path names an NTFS alternate data stream"
        code, retryable, recover = classify_error_text(ads)
        assert code == "alternate_data_stream"
        assert retryable is False
        assert ":stream" in recover
        # 它的姊妹（受限系统目录）仍走自己那颗 token，两支不许并成一颗。
        assert classify_error_text(
            "Access denied: path is in a restricted system directory (/etc)")[0] \
            == "path_restricted"

    def test_the_row_channel_uses_the_same_table(self):
        # row_diagnosis 是 feed 行的唯一 diagnosis 产地，与信封共用同一张表的同一判据：
        # 数据流那句在行级也得有 code（否则两处读者又分家）。
        from dhole_mcp.quality import row_diagnosis
        row = row_diagnosis("Access denied: path names an NTFS alternate data stream")
        assert row == {"code": "alternate_data_stream", "retryable": False,
                       "recover_with": row["recover_with"]}
        assert ":stream" in row["recover_with"]


def directly_written_codes():
    """绕过三张表、直接把 diagnosis 格子填上字典的那些产地（**两种写法都算**）。

    目前只有两条：`_resolver_guard_result`（赋值那一半，`result.diagnosis = {…}`）与
    `_nested_read` 里那一发 `ResponseModel(…, diagnosis={…})`（实参那一半 · R02-147）。
    两颗都是作者写死的名字（不是从措辞取的），所以不违约；但它们是**枚举的第二位作者**，
    所以这里连同位置一起量出来：手册必须点名每一颗，而名册不许悄悄长出第三处。

    第一版这里只扫赋值，于是那条"至多一处"的前提腿少算了实参那一处（`server.py:7892`）——
    R02-143 / R02-145 那条轴在这一层的第三次出现：**采集单位少一种形状，判据就瞎一格**。
    """
    codes, sites = set(), set()
    for path in sorted(SRC_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        funcs = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

        def _label(line):
            enclosing = [f for f in funcs if f.lineno <= line <= f.end_lineno]
            enclosing.sort(key=lambda f: f.end_lineno - f.lineno)
            return enclosing[0].name if enclosing else "<module>"

        def _code_of(mapping):
            for k, v in zip(mapping.keys, mapping.values):
                if (isinstance(k, ast.Constant) and k.value == "code"
                        and isinstance(v, ast.Constant) and isinstance(v.value, str)):
                    return v.value
            return None

        for node in ast.walk(tree):
            # 写法一：`result.diagnosis = {"code": …}`
            if isinstance(node, ast.Assign) and any(
                    isinstance(t, ast.Attribute) and t.attr == "diagnosis"
                    for t in node.targets) and isinstance(node.value, ast.Dict):
                value = _code_of(node.value)
                if value:
                    codes.add(value)
                    sites.add(f"{path.name}:{_label(node.lineno)}={value}")
                continue
            # 写法二：`ResponseModel(…, diagnosis={"code": …})`
            if isinstance(node, ast.Call):
                kw = next((k.value for k in node.keywords if k.arg == "diagnosis"), None)
                if isinstance(kw, ast.Dict):
                    value = _code_of(kw)
                    if value:
                        codes.add(value)
                        sites.add(f"{path.name}:{_label(node.lineno)}={value}")
    return codes, sorted(sites)


FAMILY_NAME_RE = re.compile(r"http_error_(\d{3})")
RETRYABLE_SENTENCE_RE = re.compile(r"(\d{3}(?: / \d{3})*) and any 5xx")


def manual_diagnosis_row() -> str:
    """手册里承诺 `diagnosis` 那一格的那一行 —— 名册那一段的唯一产地（三枚腿共用）。"""
    fields = (SRC_DIR / "skills" / "dhole-web" / "references" / "fields.md"
              ).read_text(encoding="utf-8")
    return next(line for line in fields.splitlines() if line.startswith("| `diagnosis`"))


def roster_names(row=None):
    """手册那行的**括号名册**里的 token —— 尺子的采集单位，正反向两枚腿共用一位作者。"""
    row = manual_diagnosis_row() if row is None else row
    return re.findall(r"[a-z][a-z0-9_]{3,}", row.split("(", 1)[1].split(")")[0])


def table_side_codes():
    """分类器真会给出、且**不经措辞**的那批名字：三张表 + 产地自记号 + `_diagnose` 的形状档
    + 绕过表直接写格子的两处。"""
    from dhole_mcp.quality import _diagnose
    codes = {c for _p, c, _r, _x in _DIAGNOSIS_PREFIXES}
    codes |= {c for _n, c, _r, _x in _DIAGNOSIS_CONTAINS}
    codes |= {layer_of(h)[1] for _w, _s, h in reachable_error_strings()
              if layer_of(h)[0] == "self-token"}
    # 没有 error 的那一支（wall 形状 / 空正文 / 截断）在 `_diagnose` 里给名字，
    # 从它自己的源码读，别在测试里抄第二份清单。
    codes |= set(re.findall(r'"([a-z][a-z0-9_]{3,})"', inspect.getsource(_diagnose)))
    codes |= directly_written_codes()[0]
    return codes


def roster_name_is_real(name, table_codes=None):
    """手册点名的这一颗，分类器真发得出去吗。

    形如 `http_error_404` 的名字**不在任何一张表里**（R02-146 · r346 现数：表 45 颗，一颗
    `http_error_*` 都没有），它由一颗数生成 —— 所以这里不查名册，而是**现产现问唯一的作者**：
    `quality.status_error_code(404)` 交出什么名字，`classify_error_text` 对着那句人话又认回什么。
    这一支里不许出现任何整数字面量：那颗数只能来自被问的那颗名字本身，测试里抄一份状态清单就是
    给「哪些状态码有名」立第二位作者（`test_the_accepting_helper_holds_no_number_of_its_own` 钉）。
    """
    m = FAMILY_NAME_RE.fullmatch(name)
    if m:
        status = int(m.group(1))
        return (name == quality.status_error_code(status)
                and classify_error_text(quality.status_error_sentence(status))[0] == name)
    return name in (table_side_codes() if table_codes is None else table_codes)


class TestTheManualSaysWhatIsMeasured:
    """手册那句承诺不许说满 —— 兜底那一档是给外人句子留的，它交的不是固定枚举。"""

    def test_the_two_texts_that_describe_the_code_slot_agree(self):
        fields = (SRC_DIR / "skills" / "dhole-web" / "references" / "fields.md"
                  ).read_text(encoding="utf-8")
        models = (SRC_DIR / "models.py").read_text(encoding="utf-8")
        row = manual_diagnosis_row()
        # 旧半句 "a stable snake_case enum" 对本仓库的字面量今天仍成立（本测试就是它不再
        # 被改坏的理由），但它不覆盖兜底那一档 —— 说满是把"没见过的名字一定不存在"教给
        # 调用方。两处都改成了"设计过的失败 = 产地写死的 token；外来的错误原文 = 首词"。
        for text, where in ((row, "fields.md"), (models, "models.py")):
            assert "stable snake_case enum" not in text, where
            assert "fixed snake_case token" in text, where
            assert "leading token" in text, where
        # 新上线的那颗 token 得在手册里点名（`alternate_data_stream` 是一条真回执的格子）。
        assert "alternate_data_stream" in fields
        assert "not_a_feed" in fields

    def test_the_enumerated_names_are_real_codes(self):
        # 手册列的名字必须是分类器真会给出的 code（表里的、产地自带的、`_diagnose` 按页面形状
        # 给的、或唯一作者按一颗状态码现产的）—— 列一颗不存在的名字，等于教调用方分一个
        # 永远不会命中的支。
        table_codes = table_side_codes()
        listed = roster_names(manual_diagnosis_row())
        assert listed, "手册那行的清单读不出来"
        invented = [name for name in listed if not roster_name_is_real(name, table_codes)]
        assert not invented, f"手册点名而分类器给不出的 code: {invented}"
        # 反方向也要成立：绕过表直接写进格子的名字，手册必须点名（否则那条真回执的 code
        # 对调用方不存在 —— 与 D-32 那个"影子描述"同族，只是这次影子在枚举里）。
        direct, sites = directly_written_codes()
        unlisted = sorted(name for name in direct if name not in listed)
        assert not unlisted, f"直接写进 diagnosis 格子却没在手册点名的 code: {unlisted} ({sites})"

    def test_the_authors_outside_the_table_are_few_and_named(self):
        """三张表是这份枚举的唯一作者；绕过它们直接写格子的地方**只许这两处**。

        这条腿的期望在 R02-147 改过一次，理由写在这儿免得看起来像"改不动就把期望改松"：
          · 改的是**采集单位**，不是标准 —— 上一版只扫 `x.diagnosis = {…}` 的赋值，看不见
            `ResponseModel(…, diagnosis={…})` 的实参写法，于是名册少算一处（`server.py:7892`
            · `nested_no_match`）而断言一直"绿"；标准（绕过表的产地必须极少且点名）一字未松。
          · 两处都有存在的理由：`resolver_guard` 要说的是"参数没错，错的是进程早前记下的判词"，
            `invalid_request` 那颗 token 会把人送去改一个本来就对的值；`nested_no_match` 要说的是
            "选择器一个链接都没圈到"，与 `nested_empty`（圈到了、字段全空）是两颗不同的判决，
            进表会让前缀表替一句话认两个名字。
        第三处出现要先回答"为什么不进表" —— 表就是为"一句人话 + 一颗稳定 token"存在的。
        """
        _codes, sites = directly_written_codes()
        assert sites == ["server.py:_nested_read=nested_no_match",
                         "server.py:_resolver_guard_result=resolver_guard"], sites

    def test_every_name_not_from_the_three_tables_is_named_in_the_manual(self):
        """**不许有影子描述**：不由三张表命名、却真会交出去的那颗 code，手册必须点名。

        r309 的读数（`review/.runtime/p1/r309_non_table_code_authors.json`）：这样的名字今天 6 颗
        —— 产地自带 `token:` 记号的 5 颗（`body_too_large` / `image_ocr_unavailable` /
        `nested_empty` / `nested_no_match` / `ocr_failed`）加上直接写格子的 `resolver_guard`，
        而手册那行只点了 2 颗。剩下的 4 颗对调用方不存在：拿到 `code="ocr_failed"` 去分支的人
        在手册里查不到这个名字（D-32 那个"影子描述"的同族，只是这次影子在名册外）。
        名单由扫出来，不手抄 —— 新加一颗自记号句子而不在手册点名，这条腿当场红。
        """
        listed = set(roster_names())
        table_codes = {c for _p, c, _r, _x in _DIAGNOSIS_PREFIXES}
        table_codes |= {c for _n, c, _r, _x in _DIAGNOSIS_CONTAINS}
        from_outside = {layer_of(h)[1] for _w, _s, h in reachable_error_strings()
                        if layer_of(h)[0] == "self-token"}
        direct, _sites = directly_written_codes()
        shadow = sorted((from_outside | direct) - table_codes - listed)
        assert not shadow, f"这些 code 真会交出去，而手册那行没点名：{shadow}"


class TestAParameterizedFamilyInTheRoster:
    r"""一族由一颗数生成的名字，算不算手册那串枚举的一项 —— **算，但入册的是具体例子**（R02-146）。

    现场 `review/.runtime/p1/r346_manual_family_names_pre_fix.json`（都在当前树现读）：括号名册
    今天 30 颗 token，一颗 `http_error_*` 都没有；这一族写在括号**外**的第二处括号里
    （`(`http_error_404` / `http_error_503`)`），于是「名字在哪儿列出来」有了第二位产地；
    而尺子的取词规则会把**形状名** `http_error_<status>` 切成 `http_error_` 与 `status`
    （两个都不是分类器会给的名字，r337 同读）。唯一的作者 `quality.status_error_code` 与分类器
    `classify_error_text(status_error_sentence(…))` 在六颗状态码（404 / 408 / 425 / 429 / 500 / 503）
    上一致，而这六颗**一颗都不在两张表里**（表 45 颗）—— 所以核对这一族的正确做法不是往表里塞一行，
    而是**现产现问作者**。

    定下的口径（为什么是例子而不是形状）：调用方手里拿到的永远是具体那一份字符串，
    `http_error_<status>` 不在任何一发真回执里出现过；把它列进名册等于教人去匹配一个不存在的
    名字，而把它列在名册外又让"名字在哪里列出来"变成两处。形状留在句子里解释，例子进名册。
    """

    def _family_names_in_the_roster(self):
        return [name for name in roster_names() if FAMILY_NAME_RE.fullmatch(name)]

    def test_the_roster_names_the_status_family_by_a_concrete_example(self):
        family = self._family_names_in_the_roster()
        assert len(family) >= 2, (
            f"状态那一族的名字没进手册的括号名册（读到 {len(family)} 颗）："
            "列在外面，调用方照 `code` 分支时查名册查不到它")

    def test_a_roster_family_name_is_produced_by_the_one_author(self):
        family = self._family_names_in_the_roster()
        assert family, "括号名册里还没有这一族的名字可问"
        codes = table_side_codes()
        for name in family:
            assert name not in codes, f"{name} 竟然在两表里 —— 那现产现问这一支就是摆设"
            assert roster_name_is_real(name, codes), f"{name} 既不在表里也不由作者现产"

    def test_the_examples_named_in_the_roster_show_both_sides_of_retryable(self):
        """两颗例子不许是同一侧 —— 名册要让人看出「这一族的名字里那颗数会变，判决也跟着变」。"""
        verdicts = {quality.status_verdict(int(FAMILY_NAME_RE.fullmatch(n).group(1)))[0]
                    for n in self._family_names_in_the_roster()}
        assert verdicts == {True, False}, verdicts

    def test_the_ruler_accepts_a_family_example_it_was_never_given_a_list_of(self):
        """这一族的核对只吃名字本身：名字里那颗数就是全部输入（测试里没有第二份状态清单）。"""
        codes = table_side_codes()
        # 旧读法（只查两表）在这儿就判红 —— 这一行写在这里，是为了让下一位看得见"现产现问"
        # 那一支不是摆设：418 与 451 都不在任何名册里，它们只是作者会给的两个名字。
        assert "http_error_418" not in codes and "http_error_451" not in codes
        for name in ("http_error_418", "http_error_451"):
            assert roster_name_is_real(name, codes) is True, name
        assert roster_name_is_real("http_error_not_a_number", codes) is False

    def test_the_manual_keeps_one_place_where_names_are_listed(self):
        row = manual_diagnosis_row()
        groups = re.findall(r"\(([^)]*)\)", row)
        second = [g for g in groups[1:] if re.search(r"http_error_\d{3}", g)]
        assert not second, f"括号名册之外又列了一遍这一族的名字：{second}"

    def test_a_placeholder_shape_never_enters_the_roster(self):
        """名册那一段不许出现占位形状；出现了也当场被拆成两颗不存在的名字（尺子的读法不变）。"""
        paren = manual_diagnosis_row().split("(", 1)[1].split(")")[0]
        assert "<" not in paren and ">" not in paren
        for frag in re.findall(r"[a-z][a-z0-9_]{3,}", "http_error_<status>"):
            assert roster_name_is_real(frag) is False, frag

    def test_the_retryable_numbers_in_the_manual_are_the_authors_own(self):
        """手册那句「408 / 425 / 429 and any 5xx」是**抄来的数** —— 那就由作者现数一遍核对。

        这一枚是钉子（今天就绿），它防的是下一次改措辞时把那三颗数改得与 `status_verdict`
        不一致 —— 与 R02-142 那把"状态句子的两位作者"同一根轴，只是这次抄的是名册里的一句话。
        """
        def _named(text):
            m = RETRYABLE_SENTENCE_RE.search(text)
            assert m, "手册那句 retryable 的话读不出来"
            return [int(x) for x in m.group(1).split(" / ")]

        row_named = _named(manual_diagnosis_row())
        models_named = _named((SRC_DIR / "models.py").read_text(encoding="utf-8"))
        assert set(row_named) == set(quality._RETRYABLE_STATUSES), row_named
        assert row_named == models_named, (row_named, models_named)
        for status in row_named:
            assert quality.status_verdict(status)[0] is True, status

    def test_the_accepting_helper_holds_no_number_of_its_own(self):
        """认这一族名字的 helper 里不许有一颗状态码字面量：那颗数只能来自被问的名字。

        按 AST 数，不按文本 —— 注释里写一颗 404 不算抄了一份清单（R02-088 那把尺子）。
        范围钉在 100..999（状态码的形状）而不是"任何整数"：`group(1)` 与 `[0]` 是结构下标，
        把它们也判红是这把尺第一版的误瞄（r347 现场红在这一枚上，原始读数留档）。
        """
        tree = ast.parse(textwrap.dedent(inspect.getsource(roster_name_is_real)))
        nums = [node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, int)
                and 100 <= node.value <= 999]
        assert not nums, f"核对这一族名字的 helper 里出现了状态码字面量：{nums}"
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
        named = {(getattr(f, "attr", None) or getattr(f, "id", "")) for f in
                 (c.func for c in calls)}
        assert {"status_error_code", "status_error_sentence", "classify_error_text"} <= named, named


class TestTheOneHopFromAHelperReturn:
    """R02-143 · 句子写在同模块 helper 的 return 里时，本判据也要看得见它。

    现场（`review/.runtime/p1/r284_row_level_sentence_authors.json` + `r291b_one_hop_by_flow.json`）：
    这台探针的采集单位是「往 error 格子写字面量的**构造点**」。而 R02-134 那三支违约
    （`HTTP {status}` / `feed too large (…)` / `since …`）都住在 `feed._grab` /
    `feed._since_cutoff` 的 `return` 里，构造点只是 `error=err` 转手一个变量 ——
    r284 实测 `FeedResult` 三处 + `CrawlPage` 一处全部 `visible_to_ruler=false`。
    那一族从此量不到，直到有人在产地把句子改掉而尺具一声不响。

    射程（写在收集器自己的说明里，也由腿钉着 —— 见 `helper_return_sentences` 的"追 / 不追"）：
      · **追**：同模块内一格一格转手 —— `error=` 用的名字由本模块某函数的调用赋值（元组解包按
        位置回到函数里取同一位置的返回值）；返回式是 Name 就在本函数内解析它被赋的值；返回值
        又是本模块有定义的另一个函数就接着追（≤5 跳 + 访问集）。`feed._grab` →
        `_grab_with_retry` → 构造点是**两跳**，r292 用真实源码副本量过：只走一跳时那一族漏报。
      · **不追**：调用一个本模块没有定义的函数（作者住在别的模块，例如
        `quality.status_error_sentence` —— 那一头由它自己那副在册格子说话）；空串的返回值
        （那是"这里没有句子"，不是"有句子没名字"）；`error=<对象>.<格子>` 那种转手返回对象的
        一格（顺着宿主名追返回值，量到的是那次调用的参数标签 —— r293 的 `"HTTP tier"` 误伤；
        那一类格子由 `envelope`/`assigned` 两档收，剩下"行住在 content 的 JSON 里"是 R02-145）。
    r291 第一版按"函数体里所有静态串"收，12 条里多数是这两类误伤（`detail = "the document
    is not well-formed XML"` 是插进句子中间的碎片，`return None, "", ""` 是空返回）—— 镜像
    把对的读成红的比漏报更贵（R02-136 那一族的教训），所以这里按流收。
    """

    #: 种子里那两句就是 R02-134 修前的原形状（逐字），用来证明这一跳确实能到场
    PLANTED_BARE = 'f"HTTP {404}"'
    PLANTED_SIZE = '"feed too large (9 bytes, cap 1)"'

    def _write_planted(self, tmp_path, body):
        path = tmp_path / "planted_channel.py"
        path.write_text(body, encoding="utf-8")
        return tmp_path

    def test_a_sentence_written_in_a_helper_return_reaches_the_judge(self, tmp_path):
        """反向：种一棵"产地在 helper 的 return、构造点转手"的树，探针必须点名它。"""
        tree = self._write_planted(tmp_path, (
            "class FeedResult: pass\n"
            "class CrawlPage: pass\n"
            "class ResponseModel: pass\n"
            "class SysStateModel: pass\n"
            "class CredDefaultsModel: pass\n"
            "class FeedRefusalModel: pass\n"
            "class CrawlResponseModel: pass\n"
            "class SearchResponseModel: pass\n"
            "def _grab(url):\n"
            f"    return b'', 404, '', {self.PLANTED_BARE}, {{}}\n"
            "def _cap(body):\n"
            f"    return {self.PLANTED_SIZE}\n"
            "def fetch(url):\n"
            "    body, status, encoding, err, hdrs = _grab(url)\n"
            "    if not body:\n"
            "        err = _cap(body)\n"
            "    return FeedResult(source_url=url, error=err)\n"))
        rows = reachable_error_strings(tree)
        heads = [h for _w, _s, h in rows]

        def _has(prefix):
            return any(h.startswith(prefix) for h in heads)

        assert _has("HTTP "), f"裸的状态句没进语料：{heads}"
        assert _has("feed too large ("), f"尺寸帽那句没进语料：{heads}"
        # 到场之后，分层必须真把这两句读成违约（否则这一跳只是多收了两种形状）
        violations = [h for _w, _s, h in rows
                      if layer_of(h)[0] in ("first-word", "uncoded")]
        assert len(violations) == 2, f"这两句进了语料却没被读成违约：语料={rows} 违约={violations}"
        assert all(v.startswith(("HTTP ", "feed too large (")) for v in violations), violations

    def test_an_empty_return_is_not_read_as_a_missing_name(self, tmp_path):
        """反向锁（r291 第一版的误伤形状）：`return None, "", ""` 是「这里没有句子」。"""
        body = ("class FeedResult: pass\n"
                "class ResponseModel: pass\nclass SysStateModel: pass\n"
                "class CredDefaultsModel: pass\nclass FeedRefusalModel: pass\n"
                "class CrawlPage: pass\nclass CrawlResponseModel: pass\n"
                "class SearchResponseModel: pass\n"
                "def _cutoff(value):\n"
                "    if not value:\n"
                "        return None, '', ''\n"
                "    return None, '', 'invalid_request: since must be a date string'\n"
                "def fetch(url, since):\n"
                "    cut, iso, err = _cutoff(since)\n"
                "    return FeedResult(source_url=url, error=err)\n")
        rows = reachable_error_strings(self._write_planted(tmp_path, body))
        heads = [h for _w, _s, h in rows]
        assert any(h.startswith("invalid_request: since") for h in heads), heads
        assert "" not in heads, f"空返回被当成一句违约的话：{heads}"
        assert [h for h in heads if layer_of(h)[0] in ("first-word", "uncoded")] == []

    def test_the_hop_stops_at_a_forwarding_call(self, tmp_path):
        """诚实的边界：返回式是**别人的调用**时这一跳追不过去，不许装作看见了。"""
        body = ("class FeedResult: pass\n"
                "class ResponseModel: pass\nclass SysStateModel: pass\n"
                "class CredDefaultsModel: pass\nclass FeedRefusalModel: pass\n"
                "class CrawlPage: pass\nclass CrawlResponseModel: pass\n"
                "class SearchResponseModel: pass\n"
                "def _proxy(url):\n"
                "    return status_error_sentence(url)\n"
                "def fetch(url):\n"
                "    body, status, encoding, err, hdrs = _proxy(url)\n"
                "    return FeedResult(source_url=url, error=err)\n")
        rows = reachable_error_strings(self._write_planted(tmp_path, body))
        assert rows == [], (
            f"跨模块转手被当成看见了（作者住在别的模块，本判据不追过去）：{rows}")
        assert "不追" in helper_return_sentences.__doc__, \
            "这一跳的边界必须写在收集器自己的说明里"

    def test_a_second_hop_inside_the_same_module_is_followed(self, tmp_path):
        """r292 量出来的形状：`feed._grab` 的裸句子要两跳才到场。

        真树里 `err` 的来源是 `body, _status, encoding, err, _hdrs, retry_note = await
        _grab_with_retry(...)`，而 `_grab_with_retry` 自己又是从 `_grab` 的返回值元组里把 `err`
        解出来的。只走一跳的收集器在 `_grab_with_retry` 那里停住（那一格是个 Name），
        r292 用真实源码副本把 `_grab` 退回裸 `f"HTTP {status}"` 时**没抓到** —— 这一枚腿钉的
        就是那次漏报的形状：同模块内的转手要一路跟到写死那句话的地方（有界、防环）。
        """
        body = ("class FeedResult: pass\n"
                "class ResponseModel: pass\nclass SysStateModel: pass\n"
                "class CredDefaultsModel: pass\nclass FeedRefusalModel: pass\n"
                "class CrawlPage: pass\nclass CrawlResponseModel: pass\n"
                "class SearchResponseModel: pass\n"
                "def _grab(url):\n"
                "    return b'', 404, 'utf-8', f\"HTTP {404}\", {}\n"
                "async def _grab_with_retry(url, timeout):\n"
                "    body, status, encoding, err, hdrs = await _grab(url)\n"
                "    return body, status, encoding, err, hdrs, ''\n"
                "async def fetch(url):\n"
                "    body, _st, _enc, err, _h, _note = await _grab_with_retry(url, 5)\n"
                "    return FeedResult(source_url=url, error=err)\n")
        rows = reachable_error_strings(self._write_planted(tmp_path, body))
        heads = [h for _w, _s, h in rows]
        assert any(h.startswith("HTTP ") for h in heads), (
            f"两跳之外写死的那句裸状态句没进语料：{heads}")

    def test_a_label_argument_is_not_read_as_a_sentence(self, tmp_path):
        """反向锁（r293 在真实树上量到的误伤形状）：递给别人的**档位标签**不是一句拒绝。

        两棵树，两种形状，各自钉住一处流精度（r295 在修前的收集器上验过这两棵都会红）：
          · **A** 抄 `server.py:8765`：`error=result.error` 转手返回对象的**一格**，而被转手那次
            调用是 `result or _over_budget("HTTP tier", "none")` —— 旧收集器顺着宿主名追进
            `_tier`，再扫 `or` 的整棵子树，把那两个**参数**读成两句没有名字的拒绝；
          · **B** 构造点用的是**裸名字**（那一格确实在射程里），helper 的 return 写成
            `err or _label("HTTP tier")` —— 旧收集器只扫子树，于是既报出标签、又漏掉真正
            上线的那一句 `refusing that stage outright`（错的读成红、对的读成缺席，一次两样）。
        实测（`r295_the_leg_pins_the_pre_fix_shape.json`）：修前 A 报 2 条标签、B 报 1 条且真句子
        缺席；修后两棵的标签都归零、真句子到场。这条腿钉的是重演过的形状，不是一条凭想象的红。
        """
        stubs = ("class FeedResult: pass\n"
                 "class ResponseModel: pass\nclass SysStateModel: pass\n"
                 "class CredDefaultsModel: pass\nclass FeedRefusalModel: pass\n"
                 "class CrawlPage: pass\nclass CrawlResponseModel: pass\n"
                 "class SearchResponseModel: pass\n")
        tree_a = stubs + (
            "def _over_budget(stage, fetcher_used):\n"
            "    return ResponseModel(source_url='-', "
            "error=f'{stage} gave up ({fetcher_used})')\n"
            "def _tier(url):\n"
            "    result = _over_budget('HTTP tier', 'none')\n"
            "    return result or _over_budget('HTTP tier', 'none')\n"
            "def fetch(url):\n"
            "    result = _tier(url)\n"
            "    return ResponseModel(source_url=url, error=result.error)\n")
        dir_a = tmp_path / "a"
        dir_a.mkdir()
        (dir_a / "field_forward.py").write_text(tree_a, encoding="utf-8")
        heads_a = [h for _w, _s, h in reachable_error_strings(dir_a)]
        assert not any(h.startswith(("HTTP tier", "none")) for h in heads_a), (
            f"转手对象的一格被追成了句子，参数标签进了语料：{heads_a}")

        tree_b = stubs + (
            "def _label(stage):\n"
            "    return ResponseModel(source_url='-', error=f'{stage} ran out')\n"
            "def _grab(url):\n"
            "    err = 'refusing that stage outright'\n"
            "    return b'', 404, 'utf-8', err or _label('HTTP tier'), {}\n"
            "def fetch(url):\n"
            "    body, status, encoding, err, hdrs = _grab(url)\n"
            "    return FeedResult(source_url=url, error=err)\n")
        tmp_path.joinpath("b").mkdir()
        tmp_path.joinpath("b", "bare_name_forward.py").write_text(tree_b, encoding="utf-8")
        rows_b = reachable_error_strings(tmp_path / "b")
        heads_b = [h for _w, _s, h in rows_b]
        assert not any(h.startswith("HTTP tier") for h in heads_b), (
            f"`or` 分支的子树扫描把调用的参数当成了句子：{heads_b}")
        assert "refusing that stage outright" in heads_b, (
            f"真正上线那一句反而没到场（分支只认操作数才追得到）：{heads_b}")
        assert layer_of("refusing that stage outright")[0] == "first-word", heads_b

    def test_the_shipped_helpers_are_all_registered(self):
        """正控制 + 未来的闸：真实源码里这一跳收到的每一句，都必须在册。

        r294 实测这一跳在今天的树上收 5 句（`feed._grab` 与 `_grab_with_retry` 共用的
        `body_too_large:` / `network_error:` 两支、`_friendly_parse_error` 的 `not a feed:`、
        `_since_cutoff` 的两支 `invalid_request:`，全部在册、0 嫌疑人）—— 少于 3 句就是收集器断了。
        """
        rows = helper_return_sentences()
        assert len(rows) >= 3, f"这一跳只收到 {len(rows)} 条 —— 收集逻辑八成断了"
        assert all(s.startswith("via-") and s.endswith("-return") for _w, s, _h in rows),             [s for _w, s, _h in rows]
        assert any("since" in h for _w, _s, h in rows), (
            "这一跳应当至少收到 `feed._since_cutoff` 那两支（F116 之后它们在册）")
        offenders = [(w, h, layer_of(h)) for w, _s, h in rows
                     if layer_of(h)[0] in ("first-word", "uncoded")]
        assert not offenders, (
            "这些句子写在 helper 的 return 里、由构造点转手进 error 格子，"
            f"却由措辞决定 code 或根本没有 code：{offenders}")
