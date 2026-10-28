"""``web_research(cross_check=true)`` 的来源一致性报告。

它回答的问题与 ``engines_consensus`` 不同：那边数「几个索引收录了同一个 URL」，
这边数「几个**独立可注册域名**对同一句话说了相近的话」—— 输入是已经读回来的
正文，不是搜索摘要。

全部是确定性的本地计算，没有模型、没有网络：

* claim 从 query 切出（中文标点、英文标点与并列连词）；
* 来源正文切句，按 TF-IDF 余弦归到最接近的 claim；
* 冲突判据：yes/no 问句的否定词奇偶、中英反义对、同单位数字不一致；
* ``agreement_score`` 只按支持方的独立域名数量计分，不加主观权重。

**这是来源一致性信号，不是事实核查。** ``confirmed`` 只表示多个独立域名说了
相近的话，不表示这件事为真；同一篇通稿被多家转载会被域名去重挡住一部分，
挡不住的部分写进 ``warnings``，而不是假装已经解决。中文用字符 2/3-gram，
不引入分词依赖。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlparse

MAX_CLAIMS = 5
MAX_CLAIM_CHARS = 200
MAX_URLS_PER_SIDE = 6
MAX_PERSPECTIVES = 3
MAX_SENTENCES_PER_SOURCE = 120
MAX_SENTENCES = 600
MAX_SENTENCE_CHARS = 400
SENTENCE_MIN_CHARS = 20
ATTRIBUTION_FLOOR = 0.12

_MODEL = "tfidf-cosine+cjk-ngram"

_CLAIM_BREAK = re.compile(r"[；;。！？!?]+|(?<=\.)\s+")
_COMPOUND_BREAK = re.compile(
    r"\s+(?:and|but|while|whereas|although)\s+"
    r"|(?:并且|但是|然而|而且|同时|不过|而是)")

_QUESTION_PREFIX = re.compile(
    r"^(?:is|are|was|were|does|do|did|can|could|will|would|should|has|have|had"
    r"|what|which|who|whom|whose|when|where|why|how)\b\s*(?:is|are|was|were|"
    r"does|do|did|many|much|long|old|tall|big)?\s*"
    r"|^(?:是否|什么是|什么|为何|为什么|如何|多少|几|哪)", re.I)

#: 开放式问句（wh-问句）没有极性：问“X 是什么”时，来源里的一个“不”不是在反对
#: 这个问题。yes/no 问句（Is/Does/Did…）**不**在此列 —— “Does coffee dehydrate?”
#: 与 “Coffee does not dehydrate” 确实是对立的两个回答。
_OPEN_QUESTION_EN = re.compile(
    r"^(?:what|which|who|whom|whose|when|where|why|how)\b", re.I)
_OPEN_QUESTION_ZH_HEAD = re.compile(
    r"^(?:什么|什么是|为何|为什么|如何|怎么|怎样|多少|哪个|哪些|哪里|哪儿|谁)")
_OPEN_QUESTION_ZH_TAIL = re.compile(
    r"(?:什么|为何|为什么|如何|怎么|怎样|多少|哪个|哪些|哪里|哪儿|谁)"
    r"\s*[？?]?\s*$")

#: yes/no 问句：只有这一种问句的肯定/否定回答才构成对立。陈述句、名词词组
#: （"mysql query not using index"）里的"不/not"是词的一部分，不是对 query
#: 的反对 —— 2026-10-28 真查询归因测量里，negation 型 oppose 几乎全是这类误判。
_YES_NO_QUESTION = re.compile(
    r"^(?:is|are|was|were|does|do|did|can|could|will|would|should|has|have|had"
    r"|am)\b"
    r"|^(?:是否|能否|会不会|有没有|是不是|可否)", re.I)
_YES_NO_QUESTION_ZH_TAIL = re.compile(r"吗\s*[？?]?\s*$")

_LATIN = re.compile(r"[a-z0-9]+")
_CJK_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_URL = re.compile(r"https?://\S+")
_MARKUP = re.compile(r"[*_`#>|\[\]()]+")

_EN_STOP = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "of", "to", "in", "on", "at", "for", "from", "by", "with", "as", "that",
    "this", "these", "those", "it", "its", "and", "or", "but", "if", "then",
    "than", "so", "such", "into", "over", "under", "about", "which", "who",
    "what", "when", "where", "why", "how", "not", "no", "their", "there",
    "they", "we", "you", "he", "she", "his", "her", "our", "your", "has",
    "have", "had", "do", "does", "did", "can", "could", "will", "would",
    "should", "may", "might", "must", "also", "more", "most",
})
_CJK_STOP = frozenset("的了是在和与也就都而及或这那有为对从到中上下个我你他它们吗呢吧啊")

_EN_NEGATION = re.compile(
    r"\b(?:not|no|never|without|none|nor|neither|cannot|isn't|aren't|wasn't|"
    r"weren't|don't|doesn't|didn't|won't|wouldn't|can't|couldn't|hasn't|"
    r"haven't|hadn't)\b", re.I)
# 这些词里的「不 / 无 / 非 / 没」不是否定（非常多、不同、无论……），先吃掉再数单个字。
_ZH_NEGATION_FALSE_FRIENDS = re.compile(
    "|".join(sorted({
        "不同", "不断", "不少", "不仅", "不但", "不论", "无论", "非常", "未来",
        "不错", "不久", "不已", "不得不", "不得不說", "说不定", "无比", "无论",
    }, key=len, reverse=True)))
_ZH_NEGATION = re.compile(r"并非|不是|没有|尚未|未能|未曾|无法|不能|不会|不再|不足|未|无|不|没|非")

_ANTONYMS = (
    ("increase", "decrease"), ("rise", "fall"), ("rising", "falling"),
    ("higher", "lower"), ("more", "fewer"), ("safe", "unsafe"),
    ("support", "oppose"), ("approve", "reject"), ("expand", "shrink"),
    ("profit", "loss"), ("faster", "slower"),
    ("上涨", "下跌"), ("增长", "下降"), ("增加", "减少"), ("提高", "降低"),
    ("支持", "反对"), ("安全", "危险"), ("盈利", "亏损"), ("成功", "失败"),
    ("高于", "低于"), ("提前", "推迟"), ("扩大", "缩小"), ("上升", "下降"),
)

_UNIT_ALIASES = {
    "%": "%", "percent": "%", "％": "%",
    "m": "m", "metre": "m", "metres": "m", "meter": "m", "meters": "m", "米": "m",
    "km": "km", "公里": "km", "千米": "km",
    "cm": "cm", "厘米": "cm", "mm": "mm", "毫米": "mm",
    "ft": "ft", "feet": "ft", "foot": "ft", "英尺": "ft",
    "kg": "kg", "公斤": "kg", "千克": "kg", "g": "g", "克": "g",
    "lb": "lb", "lbs": "lb", "磅": "lb", "t": "t", "吨": "t",
    "year": "year", "years": "year", "年": "year",
    "month": "month", "months": "month", "月": "month",
    "day": "day", "days": "day", "天": "day",
    "week": "week", "weeks": "week", "周": "week",
    "s": "s", "second": "s", "seconds": "s", "秒": "s",
    "min": "min", "minute": "min", "minutes": "min", "分钟": "min",
    "h": "h", "hour": "h", "hours": "h", "小时": "h",
    "倍": "x", "times": "x",
}
_NUMBER = re.compile(
    r"(\d+(?:\.\d+)?)\s*(" +
    "|".join(sorted((re.escape(u) for u in _UNIT_ALIASES), key=len, reverse=True)) +
    r")?",
    re.I)

# 两段式公共后缀：没有 PSL 依赖，只钉住最常见的这些，避免把 bbc.co.uk 与
# itv.co.uk 都压成 co.uk。
_MULTI_LABEL_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.cn", "net.cn", "org.cn",
    "gov.cn", "edu.cn", "com.au", "net.au", "org.au", "co.jp", "ne.jp",
    "or.jp", "co.kr", "com.br", "com.tw", "com.hk", "com.sg", "co.in",
    "com.mx", "com.tr", "co.nz", "co.za", "com.es", "com.it",
})


@dataclass
class _Evidence:
    score: float
    url: str
    sentence: str

    @property
    def domain(self) -> str:
        return _registrable_domain(self.url)


@dataclass
class _Claim:
    text: str
    yes_no_question: bool = False
    tokens: Counter = field(default_factory=Counter)
    support: list[_Evidence] = field(default_factory=list)
    oppose: list[_Evidence] = field(default_factory=list)


def _clean(text: str) -> str:
    text = _URL.sub(" ", text or "")
    text = _MARKUP.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _registrable_domain(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower().strip(".")
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    if len(labels) <= 2:
        return host
    if all(label.isdigit() for label in labels):
        return host
    if ".".join(labels[-2:]) in _MULTI_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _tokenize(text: str) -> Counter:
    """拉丁词 + 汉字 2/3-gram。中文不依赖分词器，靠 n-gram 保底。"""
    low = (text or "").lower()
    tokens: Counter = Counter()
    for word in _LATIN.findall(low):
        if len(word) >= 2 and word not in _EN_STOP:
            tokens[word] += 1
    for run in _CJK_RUN.findall(low):
        if len(run) == 1:
            if run not in _CJK_STOP:
                tokens[run] += 1
            continue
        for size in (2, 3):
            for i in range(len(run) - size + 1):
                gram = run[i:i + size]
                if size == 2 and gram[0] in _CJK_STOP and gram[1] in _CJK_STOP:
                    continue
                tokens[gram] += 1
    return tokens


def _vectors(counters: list[Counter]) -> list[dict[str, float]]:
    df: Counter = Counter()
    for counter in counters:
        df.update(set(counter))
    total = max(1, len(counters))
    idf = {token: math.log((1 + total) / (1 + seen)) + 1.0
           for token, seen in df.items()}
    vectors: list[dict[str, float]] = []
    for counter in counters:
        vec = {token: (1.0 + math.log(tf)) * idf.get(token, 1.0)
               for token, tf in counter.items() if tf > 0}
        norm = math.sqrt(sum(weight * weight for weight in vec.values()))
        vectors.append({token: weight / norm for token, weight in vec.items()}
                       if norm else {})
    return vectors


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(weight * b.get(token, 0.0) for token, weight in a.items())


def _is_open_question(query: str) -> bool:
    """开放式问句（wh-问句）没有可被否定的极性。

    “异步编程是什么？”与一句含“不/没”的正文并不构成对立；把它判成 contested
    是把“来源里出现了否定词”冒充成“来源之间意见相反”（2026-10-28 真机 h04）。
    yes/no 问句不在此列：那里的正负回答本身就是对立面。
    """
    text = _clean(query)
    if not text:
        return False
    if _OPEN_QUESTION_EN.match(text) or _OPEN_QUESTION_ZH_HEAD.match(text):
        return True
    return bool(_OPEN_QUESTION_ZH_TAIL.search(text))


def _is_yes_no_question(query: str) -> bool:
    """只有 yes/no 问句的否定/肯定回答构成对立。

    “Does coffee dehydrate?” 与 “Coffee does not dehydrate” 是一对矛盾；
    “mysql query not using index” 里的 not 只是查询的一部分，来源句子没有 not
    并不反对它；陈述句的“不”同理。开放式问句已由 `_is_open_question` 处理，
    这里再单独识别 yes/no 形状，避免把否定极性套到所有非 wh- 查询上。
    """
    text = _clean(query)
    if not text:
        return False
    if _OPEN_QUESTION_EN.match(text) or _OPEN_QUESTION_ZH_HEAD.match(text):
        return False
    return bool(_YES_NO_QUESTION.match(text)
                or _YES_NO_QUESTION_ZH_TAIL.search(text))


def _split_claims(query: str) -> tuple[list[str], bool]:
    text = _clean(query)
    if not text:
        return [], False
    fragments: list[str] = []
    for piece in _CLAIM_BREAK.split(text):
        piece = piece.strip(" ,，、:：")
        if not piece:
            continue
        parts = [p.strip(" ,，、:：") for p in _COMPOUND_BREAK.split(piece)]
        parts = [p for p in parts if len(p) >= 6] or [piece]
        fragments.extend(parts)
    if not fragments:
        fragments = [text]
    seen: dict[str, int] = {}
    claims: list[str] = []
    for fragment in fragments:
        key = fragment.casefold()
        if key in seen:
            continue
        seen[key] = 1
        claims.append(fragment)
    truncated = len(claims) > MAX_CLAIMS
    return [c[:MAX_CLAIM_CHARS] for c in claims[:MAX_CLAIMS]], truncated


def _split_sentences(text: str) -> list[str]:
    out: list[str] = []
    for line in (text or "").splitlines():
        line = _clean(line.strip(" \t-*+·"))
        if not line:
            continue
        for piece in re.split(r"(?<=[。！？!?；;])|(?<=\.)\s+", line):
            sentence = piece.strip()
            if len(sentence) < SENTENCE_MIN_CHARS:
                continue
            # 疑问句不做证据：它什么都没有断言。
            if sentence.endswith(("?", "？")):
                continue
            out.append(sentence[:MAX_SENTENCE_CHARS])
    return out


def _negation_parity(text: str) -> int:
    english = len(_EN_NEGATION.findall(text or ""))
    zh_text = _ZH_NEGATION_FALSE_FRIENDS.sub(" ", text or "")
    chinese = len(_ZH_NEGATION.findall(zh_text))
    return (english + chinese) % 2


def _has_term(text: str, term: str) -> bool:
    if term.isascii():
        return re.search(rf"\b{re.escape(term)}\b", text, re.I) is not None
    return term in text


def _antonym_flip(claim: str, sentence: str) -> bool:
    for left, right in _ANTONYMS:
        claim_left, claim_right = _has_term(claim, left), _has_term(claim, right)
        sent_left, sent_right = _has_term(sentence, left), _has_term(sentence, right)
        if (claim_left and not claim_right and sent_right and not sent_left) or \
           (claim_right and not claim_left and sent_left and not sent_right):
            return True
    return False


def _numbers(text: str) -> dict[str, set[float]]:
    grouped: dict[str, set[float]] = {}
    for raw_value, raw_unit in _NUMBER.findall(text or ""):
        try:
            value = round(float(raw_value), 3)
        except ValueError:
            continue
        unit = _UNIT_ALIASES.get((raw_unit or "").lower()) if raw_unit else None
        if unit is None:
            unit = "year" if float(value).is_integer() and 1900 <= value <= 2100 else "count"
        grouped.setdefault(unit, set()).add(value)
    return grouped


def _numeric_flip(claim: str, sentence: str) -> bool:
    claim_numbers = _numbers(claim)
    sentence_numbers = _numbers(sentence)
    for unit in claim_numbers.keys() & sentence_numbers.keys():
        if not (claim_numbers[unit] & sentence_numbers[unit]):
            return True
    return False


def _stance(claim: _Claim, sentence: str) -> str:
    if claim.yes_no_question and (
            _negation_parity(claim.text) != _negation_parity(sentence)):
        # 只有 yes/no 问句的肯定/否定回答构成对立；陈述与名词词组里的否定词
        # 是词义的一部分，不是对 query 的反对。
        return "oppose"
    if _antonym_flip(claim.text, sentence):
        return "oppose"
    if _numeric_flip(claim.text, sentence):
        return "oppose"
    return "support"


def _score(domains: int) -> float:
    if domains <= 0:
        return 0.0
    if domains == 1:
        return 0.3
    if domains == 2:
        return 0.7
    if domains <= 4:
        return 0.9
    return 1.0


def _dedupe_urls(rows: list[_Evidence]) -> list[str]:
    ordered = sorted(rows, key=lambda row: -row.score)
    out: list[str] = []
    for row in ordered:
        if row.url and row.url not in out:
            out.append(row.url)
        if len(out) >= MAX_URLS_PER_SIDE:
            break
    return out


def _majority_side(groups: dict) -> object:
    """返回按独立域名计数占多数的组键；平票返回 ``None``（无多数）。"""
    counts = {key: len({row.domain for row in rows} - {""})
              for key, rows in groups.items()}
    top = max(counts.values(), default=0)
    winners = [key for key, count in counts.items() if count == top]
    return winners[0] if len(winners) == 1 else None


def _resolve_unstated_conflicts(claim: _Claim) -> None:
    """claim 自己没说的冲突，靠来源之间互相矛盾来发现。

    例：问「这个浏览器份额多少」，A 说 45%、B 说 50%。claim 里没有数字，
    逐句对 claim 判立场时两边都像在回答，只有把来源彼此对上才看得见分歧。
    同单位数字与反义对各按独立域名占多数的一方留下，其余移进 ``oppose``；
    平票时全部算反对 —— 分不出谁对，就不假装分得出。
    """
    support = claim.support
    if len(support) < 2:
        return

    moved: set[int] = set()
    by_unit: dict[str, dict[float, list[_Evidence]]] = {}
    for row in support:
        numbers = _numbers(row.sentence)
        for unit, values in numbers.items():
            if len(values) == 1:
                value = next(iter(values))
                by_unit.setdefault(unit, {}).setdefault(value, []).append(row)
    for unit, groups in by_unit.items():
        if unit in _numbers(claim.text) or len(groups) < 2:
            continue
        winner = _majority_side(groups)
        for value, rows in groups.items():
            if winner is not None and value == winner:
                continue
            for row in rows:
                moved.add(id(row))

    for left, right in _ANTONYMS:
        left_rows = [row for row in support
                     if _has_term(row.sentence, left)
                     and not _has_term(row.sentence, right)]
        right_rows = [row for row in support
                      if _has_term(row.sentence, right)
                      and not _has_term(row.sentence, left)]
        if not left_rows or not right_rows:
            continue
        winner = _majority_side({"l": left_rows, "r": right_rows})
        if winner is None:
            moved.update(id(row) for row in left_rows + right_rows)
        else:
            moved.update(id(row)
                         for row in (right_rows if winner == "l" else left_rows))

    if not moved:
        return
    claim.support = [row for row in support if id(row) not in moved]
    claim.oppose.extend(row for row in support if id(row) in moved)


def _verdict(claim: _Claim) -> dict:
    support_urls = _dedupe_urls(claim.support)
    oppose_urls = _dedupe_urls(claim.oppose)
    # 域名来自**全部**命中句，不是被截到 6 条的那份 URL 列表：截断是输出预算，
    # 不该顺手改掉打分。
    support_domains = {row.domain for row in claim.support} - {""}

    if oppose_urls:
        verdict = "contested"
        perspectives = []
        for row in sorted(claim.oppose, key=lambda r: -r.score):
            perspectives.append(f"{row.sentence[:160]} ({row.url})")
            if len(perspectives) >= MAX_PERSPECTIVES:
                break
    elif len(support_domains) >= 2:
        verdict = "confirmed"
        perspectives = []
    else:
        verdict = "unverified"
        perspectives = []

    return {
        "claim": claim.text,
        "verdict": verdict,
        "agreement_score": _score(len(support_domains)),
        "support": support_urls,
        "oppose": oppose_urls,
        "perspectives": perspectives,
    }


def empty_report(reason: str = "") -> dict:
    """``cross_check`` 没有东西可判时的字段形状（证据包照常返回）。"""
    warnings = [reason] if reason else []
    return {
        "claims": [],
        "agreement_map": {"green": [], "yellow": [], "red": []},
        "cross_check_basis": {
            "sources_used": 0, "domains": 0, "claims": 0,
            "model": _MODEL, "warnings": warnings,
        },
        "cross_check_error": "",
    }


def cross_check(query: str, blocks: list[tuple[str, str]]) -> dict:
    """把已读回的来源正文压成一份来源一致性报告。

    Args:
        query: 原研究问题，claim 从这里切。
        blocks: ``[(url, text), ...]`` —— 每个成功读回的来源一条。

    Returns:
        ``claims`` / ``agreement_map`` / ``cross_check_basis`` / ``cross_check_error``
        四个字段。空输入返回空报告；任何计算都不会抛给调用方（这一层没有异常
        可报——最坏的情况是「没有证据可判」，那也是这个形状）。
    """
    warnings = ["agreement is a cross-source signal, not a fact-check: confirmed "
                "means several independent domains said similar things, not that "
                "the claim is true"]
    claim_texts, truncated_claims = _split_claims(query)
    open_question = _is_open_question(query)
    yes_no_question = _is_yes_no_question(query)
    if not blocks or not claim_texts:
        return empty_report(
            "no source text was read, so there was nothing to cross-check"
            if not blocks else "the query yielded no checkable claim")

    domains = sorted({_registrable_domain(url) for url, _text in blocks} - {""})
    sentences: list[tuple[str, str]] = []
    sentence_cap_hit = False
    for url, text in blocks:
        per_source = _split_sentences(text)[:MAX_SENTENCES_PER_SOURCE]
        for sentence in per_source:
            sentences.append((url, sentence))
    if len(sentences) > MAX_SENTENCES:
        sentences = sentences[:MAX_SENTENCES]
        sentence_cap_hit = True

    if not sentences:
        return empty_report(
            "none of the read sources yielded a usable sentence "
            f"(sentences under {SENTENCE_MIN_CHARS} chars are skipped)")

    claims = [_Claim(text=text, yes_no_question=yes_no_question)
              for text in claim_texts]
    for claim in claims:
        claim.tokens = _tokenize(_QUESTION_PREFIX.sub("", claim.text))
    sentence_tokens = [_tokenize(sentence) for _url, sentence in sentences]
    vectors = _vectors([claim.tokens for claim in claims] + sentence_tokens)
    claim_vectors = vectors[:len(claims)]
    sentence_vectors = vectors[len(claims):]

    for (url, sentence), vector in zip(sentences, sentence_vectors):
        best_index, best_score = -1, 0.0
        for index, claim_vector in enumerate(claim_vectors):
            score = _cosine(vector, claim_vector)
            if score > best_score:
                best_index, best_score = index, score
        if best_index < 0 or best_score < ATTRIBUTION_FLOOR:
            continue
        claim = claims[best_index]
        stance = _stance(claim, sentence)
        (claim.support if stance == "support" else claim.oppose).append(
            _Evidence(best_score, url, sentence))

    for claim in claims:
        _resolve_unstated_conflicts(claim)

    results = [_verdict(claim) for claim in claims]
    support_sentences = sum(len(claim.support) for claim in claims)
    support_domains = {row.domain for claim in claims for row in claim.support}
    support_domains -= {""}
    over_url_cap = sum(
        max(0, len({row.url for row in side}) - MAX_URLS_PER_SIDE)
        for claim in claims for side in (claim.support, claim.oppose))
    if over_url_cap:
        warnings.append(
            f"{over_url_cap} URL(s) were left out: each side of a claim lists at "
            f"most {MAX_URLS_PER_SIDE}")
    if support_sentences > len(support_domains) and len(support_domains) > 0:
        warnings.append(
            f"{support_sentences} supporting sentence(s) sit on "
            f"{len(support_domains)} registrable domain(s); copies of one story "
            "count once")
    if truncated_claims:
        warnings.append(f"the query split into more than {MAX_CLAIMS} claims; "
                        f"only the first {MAX_CLAIMS} were checked")
    if sentence_cap_hit:
        warnings.append(f"sentence text was capped at {MAX_SENTENCES} sentences")
    if open_question:
        warnings.append(
            "the query is an open question, so a negation inside a source sentence "
            "is not counted as opposition; a claim turns contested only when "
            "sources differ in numbers or antonyms, or disagree with each other")
    elif not yes_no_question:
        warnings.append(
            "the query is not a yes/no question, so a negation inside a source "
            "sentence is not counted as opposition; a claim turns contested only "
            "when sources differ in numbers or antonyms, or disagree with each "
            "other")

    return {
        "claims": results,
        "agreement_map": {
            "green": [row["claim"] for row in results
                      if row["verdict"] == "confirmed"],
            "yellow": [row["claim"] for row in results
                       if row["verdict"] == "unverified"],
            "red": [row["claim"] for row in results
                    if row["verdict"] == "contested"],
        },
        "cross_check_basis": {
            "sources_used": len(blocks),
            "domains": len(domains),
            "claims": len(results),
            "model": _MODEL,
            "warnings": warnings,
        },
        "cross_check_error": "",
    }
