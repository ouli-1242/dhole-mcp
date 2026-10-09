# -*- coding: utf-8 -*-
r"""我们自己写的拒绝，不许由句子的第一个词命名。

fields.md 曾经把 `diagnosis.code` 承诺成"a stable snake_case enum"，而 `classify_error_text`
的最后一档是"拿首词当 code"。这一档对**外人写的句子**是对的（httpx 的原文、站方解析器的
报错 —— 总得给个名字，缺席比一个临时名字更坏）；对**本仓库作者写下的字面量**是错的：
第一个词是措辞的一部分，改写句子 code 就跟着改，而调用方被教的是"照 code 分支，别读散文"。
所以这里量两件事：产地写的每一个字面量都得有个不是措辞给的 token，以及手册那句承诺得
收窄到真话的范围（设计过的失败 = 产地写死的 token；外来的错误原文 = 首词，认不出就别猜）。

量出来的口径（`.scratch/liveverify/code_reachable2.py` + 本测试的收集器，2026-10-09）：真会
进 diagnosis 的**静态可判**串 **52 条** —— 46 条在前缀表、2 条在子串表、4 条自带 `token:`
记号，**0 条落在首词兜底、0 条落空**。另有 25 处的头是变量（异常原文、`str(e)[:200]`、
`_tier_error_text(exc)`），那些正是"外人写的句子"，兜底就是为它们存在的。

修前唯一那条违约是备用数据流那句门：`_agent_hints` 按 `startswith` 认得出它、diagnosis 表
认不出，于是 code 空、整格缺席，而同一份回执的 next_action 照样说话 —— 一句两读者，只有一位
在册（现已补进 `_DIAGNOSIS_PREFIXES`，code `alternate_data_stream`）。本测试把这条分工钉住：
产地写下的每一个字面量，必须要么在三张表里，要么自己带着 `token:` 记号。

两处上一版探针的高估也跟着写死了，否则判据会去量根本不存在的格子：
  · `SysStateModel` / `CredDefaultsModel` **没有 diagnosis 字段**（diagnosis 只在
    ResponseModel 上，行级只有 feed 的 `row_diagnosis`）—— 它们的拒答串不参与本判据；
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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from dhole_mcp import quality  # noqa: E402
from dhole_mcp.feed import FeedResult  # noqa: E402
from dhole_mcp.models import (BulkResponseModel, CredDefaultsModel, ResponseModel,  # noqa: E402
                              SysStateModel)
from dhole_mcp.quality import (_DIAGNOSIS_CONTAINS, _DIAGNOSIS_PREFIXES,  # noqa: E402
                               _LEADING_CODE_RE, classify_error_text)
from dhole_mcp.errors import classify_network_error  # noqa: E402

SRC_DIR = pathlib.Path(inspect.getsourcefile(quality)).parent
# 有 diagnosis 格子的模型。BulkResponseModel 的行就是 ResponseModel，同一处填。
DIAGNOSED_MODELS = {"ResponseModel"}
DIAGNOSED_ROW_MODELS = {"FeedResult"}
NOT_DIAGNOSED_MODELS = {"SysStateModel", "CredDefaultsModel"}
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


def prefix_composers():
    """自动读出两种接线：往上拼 `prefix: ` 的函数，和把参数转手给 error 的函数。

    只认**单一构造**的函数（`_invalid_request_result` 那种专职拒答壳）。一个函数若在-body
    里构造好几个不同形状的 error（`web_read` 就是），它就不是"转手"而是自己写话 —— 那些
    字面量已经以 envelope 槽位收到语料里了，把它们再当成每个调用点的公共前缀，等于用一
    个不存在的前缀给所有调用点发证。
    """
    composers, forwards = {}, {}
    for path in sorted(SRC_DIR.glob("*.py")):
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


def reachable_error_strings():
    """[(file:line, 槽位, 上线串头)] —— 只收静态写得出的字面量头。"""
    composers, forwards = prefix_composers()
    found = []
    for path in sorted(SRC_DIR.glob("*.py")):
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
    return [(w, s, h) for w, s, h in found if h]


def layer_of(text: str) -> tuple[str, str]:
    """与 classify_error_text 同序，但记下是哪一档给的 code。"""
    for prefix, code, _r, _rec in _DIAGNOSIS_PREFIXES:
        if text.startswith(prefix):
            return "table-prefix", code
    for needle, code, _r, _rec in _DIAGNOSIS_CONTAINS:
        if needle in text:
            return "table-contains", code
    category, _hint = classify_network_error(text)
    if category != "unknown":
        return "network-classifier", category
    m = _LEADING_CODE_RE.match(text)
    if not m:
        return "uncoded", ""
    code = m.group(0)
    if text[len(code):len(code) + 1] == ":":
        return "self-token", code
    return "first-word", code


class TestThePremises:
    """判据的两块地基：谁有 diagnosis 格子，以及这台探针真能认出违约。"""

    def test_only_the_diagnosed_models_carry_a_code_slot(self):
        # 上一版探针把 SysState/CredDefaults 的拒答也算成违约 —— 那两个模型根本没有
        # diagnosis 格子，于是判据去量一个不存在的字段。这行前提要是哪天变了（有人给
        # SysStateModel 加了 diagnosis），本测试会红，口径跟着改。
        assert "diagnosis" in ResponseModel.model_fields
        assert "diagnosis" in FeedResult.model_fields
        for model in (SysStateModel, CredDefaultsModel, BulkResponseModel):
            assert "diagnosis" not in model.model_fields, model.__name__

    def test_a_prose_sentence_does_land_in_the_first_word_layer(self):
        # 反向守卫：静态可判语料全绿不等于探针是瞎的。手工一句散文（小写起头，兜底的
        # 正则就长这样）必须落在 first-word，手工一个 `token:` 记号必须落在 self-token，
        # 一个大写起头的句子则落在 uncoded —— 三档都能认出，语料才不是空跑。
        assert layer_of("refusing to create that path for you")[0] == "first-word"
        assert layer_of("some_self_explaining: prose follows")[0] == "self-token"
        assert layer_of("Access denied: nothing we authored matches")[0] == "uncoded"

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
    """第四位产地：绕过三张表、直接往 `result.diagnosis` 写字典的那几处。

    目前只有一条（`_resolver_guard_result` —— 它要说的是"参数没错，错的是进程早前记下的
    判词"，`invalid_request` 那颗 token 会把人送去改一个本来就对的值）。名字同样是作者
    写死的，不是从措辞取的，所以不违约；但它是**枚举的第二个作者**，所以这里把它连同位置
    一起量出来：手册必须点名它，而它不许悄悄长出第三处、第四处。
    """
    codes, sites = set(), []
    for path in sorted(SRC_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for t in node.targets:
                if not (isinstance(t, ast.Attribute) and t.attr == "diagnosis"):
                    continue
                if isinstance(node.value, ast.Dict):
                    for k, v in zip(node.value.keys, node.value.values):
                        if (isinstance(k, ast.Constant) and k.value == "code"
                                and isinstance(v, ast.Constant)
                                and isinstance(v.value, str)):
                            codes.add(v.value)
                            sites.append(f"{path.name}:{node.lineno}={v.value}")
    return codes, sites


class TestTheManualSaysWhatIsMeasured:
    """手册那句承诺不许说满 —— 兜底那一档是给外人句子留的，它交的不是固定枚举。"""

    def test_the_two_texts_that_describe_the_code_slot_agree(self):
        fields = (SRC_DIR / "skills" / "dhole-web" / "references" / "fields.md"
                  ).read_text(encoding="utf-8")
        models = (SRC_DIR / "models.py").read_text(encoding="utf-8")
        row = next(line for line in fields.splitlines() if line.startswith("| `diagnosis`"))
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
        # 手册列的名字必须是分类器真会给出的 code（表里的、产地自带的、或 `_diagnose` 按
        # 页面形状给的）—— 列一颗不存在的名字，等于教调用方分一个永远不会命中的支。
        from dhole_mcp.quality import _diagnose
        table_codes = {c for _p, c, _r, _x in _DIAGNOSIS_PREFIXES}
        table_codes |= {c for _n, c, _r, _x in _DIAGNOSIS_CONTAINS}
        table_codes |= {layer_of(h)[1] for _w, _s, h in reachable_error_strings()
                        if layer_of(h)[0] == "self-token"}
        # 没有 error 的那一支（wall 形状 / 空正文 / 截断）在 `_diagnose` 里给名字，
        # 从它自己的源码读，别在测试里抄第二份清单。
        table_codes |= set(re.findall(r'"([a-z][a-z0-9_]{3,})"', inspect.getsource(_diagnose)))
        direct, sites = directly_written_codes()
        table_codes |= direct
        fields = (SRC_DIR / "skills" / "dhole-web" / "references" / "fields.md"
                  ).read_text(encoding="utf-8")
        row = next(line for line in fields.splitlines() if line.startswith("| `diagnosis`"))
        listed = re.findall(r"[a-z][a-z0-9_]{3,}", row.split("(", 1)[1].split(")")[0])
        assert listed, "手册那行的清单读不出来"
        invented = [name for name in listed if name not in table_codes]
        assert not invented, f"手册点名而分类器给不出的 code: {invented}"
        # 反方向也要成立：绕过表直接写进格子的名字，手册必须点名（否则那条真回执的 code
        # 对调用方不存在 —— 与 D-32 那个"影子描述"同族，只是这次影子在枚举里）。
        unlisted = sorted(name for name in direct if name not in listed)
        assert not unlisted, f"直接写进 diagnosis 格子却没在手册点名的 code: {unlisted} ({sites})"

    def test_the_enum_has_at_most_one_author_besides_the_table(self):
        # 三张表是这份枚举的唯一作者；绕过它们直接写格子的地方**只许有一条**，而且必须是
        # 那条有理由的（resolver_guard）。第二处出现要先回答"为什么不进表"——表就是为
        # "一句人话 + 一颗稳定 token"存在的。
        _codes, sites = directly_written_codes()
        assert sites == ["server.py:3129=resolver_guard"], sites
