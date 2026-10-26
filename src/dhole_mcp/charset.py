"""字节到文本的解码：决定一份 body 字符集的唯一地方。

两个调用方、两种情形，共享一套"这段文字看起来不对"的词汇：

* HTTP（``fetcher._decode_html_bytes``）从 Content-Type 的 charset 出发 ——
  但头会说谎，所以字节也有投票权。
* 本地文件（``doc_parse``）根本没有头：只有字节、可选的 BOM，以及调用方声明的
  名字。

两边需要回答同一个问题 ——"这些解码里哪一个受损最轻？" —— 所以标记与打分住在
这里，而不是任一个调用方里。

刻意不从 dhole_mcp 导入任何东西：parse.py 承诺在没有抓取栈（没有 primp）时也能
工作，而这就是 [all] extra 未安装时它需要的模块。
"""

from __future__ import annotations

import codecs

# UTF-8 多字节序列按 latin-1 / cp1252 解码后留下的前缀：U+00C2/U+00C3/U+00E2
# 后面还跟着字节，以及落单的 BOM。
_MOJIBAKE_MARKERS = ("Ã", "Â", "â€", "â\x80", "â\x9d", "ï»¿")

_PUA_START = 0xE000
_PUA_END = 0xF8FF


def misdecode_score(text: str) -> int:
    """一次解码看起来有多受损，忽略私用区字符。

    替换字符加上乱码标记。越低越好；0 表示干净。HTTP 路径就是用它比较两个候选。
    """
    if not text:
        return 0
    score = text.count("\ufffd")
    for marker in _MOJIBAKE_MARKERS:
        score += text.count(marker)
    return score


def decode_damage(text: str) -> int:
    """misdecode_score 加上私用区（PUA）字符。

    PUA 项住在这里而不在 misdecode_score，是因为它只有在候选里包含 GB18030 时
    才携带信息：GB18030 会把大量字节对直接映射进 PUA，所以一份 Big5 文档按
    GB18030 解码会每 30 个字符落进 5-6 个（4 个样本实测），而真正的 GBK 文档
    一个都不落（7 个样本实测，最坏 0）。HTTP 路径从不尝试 GB18030，所以这一项
    在那里只会加噪声。
    """
    score = misdecode_score(text)
    for ch in text:
        if _PUA_START <= ord(ch) <= _PUA_END:
            score += 1
    return score


# 每个乱码标记对应多少字符，才让标记成为文本中的真实占比，而不只是正文里引用的
# 一个例子。比任何真实误码都宽松（每个非 ASCII 字符都会中招，每个产生 2-3 个
# 标记字符），又足够紧，能拦住一份只展示了一个错误字符集的文档文件。
_MARKER_PER_CHAR = 50


def charset_is_in_doubt(text: str) -> bool:
    """一次解码里的损伤是否说明**字符集**猜错了。

    `decode_damage` 是一个比较分：越低越好，0 是干净。把它直接变成判决曾是那个
    错误 —— 它分不清"字符集错了"和"文本**引用了**错字符集"。在本仓库自己的
    CHANGELOG 上实测：它记录 `â€™` 这种失败模式，43,559 个字符里只有一个标记、
    按 UTF-8 逐字节完美解码，却被报成无法解码的文件。

    所以两种信号分开读：

    * 替换字符或 PUA 字符出现在**任何地方**都足以定罪 —— 丢失的字节与落在所有
      字母表之外的 GB18030 字节对，不可能在同一批字节的正确解码里幸存；
    * 拉丁乱码标记（`Ã©`、`â€™`）本身就是合法字符，所以只有当它们占到文档的
      一定比例时才说明字符集错了 —— 真实误码正是这样按比例扩张的。
    """
    if "\ufffd" in text:
        return True
    for ch in text:
        if _PUA_START <= ord(ch) <= _PUA_END:
            return True
    markers = 0
    for marker in _MOJIBAKE_MARKERS:
        markers += text.count(marker)
    return markers >= 2 and markers * _MARKER_PER_CHAR >= len(text)


# ─── 本地文件解码 ────────────────────────────────────────────────────────────
#
# 本地文件没有头来声明字符集。候选顺序：
#
#   1. BOM。文件自己声明了编码，而 BOM 从不意外出现。
#   2. 调用方的显式声明。是提示，不是命令：严格解不开的名字落回检测，返回的
#      编码名说的是真正胜出的那一个。
#   3. 头部里的 NUL 字节。BOM 被剥掉的 UTF-16/32 body 按 GB18030 会"干净地"
#      解成满是 NUL 的文本 —— 损伤 0，内容全垃圾。
#   4. 严格 UTF-8。能按 UTF-8 解码的字节流基本不可能出自旧式编码器，所以这是
#      一个安全的"是不是 UTF-8？"测试。
#   5. 严格 GB18030，只在干净解出时才接受。它是 GBK 与 GB2312 的超集，一个候选
#      覆盖整个家族 —— 7/7 个 GBK 样本实测干净。
#   6. utf-8 与 gb18030 之间的打分比较，保留损伤更少的那个。
#
# 已知边界，是量出来的不是假设的：GB18030 也能严格解码*其他* CJK 旧编码，所以
# Shift_JIS（"名前" -> "柤慜"）与 EUC-KR 会以损伤 0 变成错但看似合理的中文。
# Big5 是例外 —— 它落进 PUA 并被标记。没有便宜的办法区分前两者，所以请为它们
# 传 encoding=。
_BOM_ENCODINGS = (
    # UTF-32-LE 的 BOM 以 UTF-16-LE 的 BOM 开头，所以必须先测它。
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

# 没有 BOM、也没有可用声明时，按顺序严格尝试。刻意很短：big5/shift_jis/euc-kr
# 会把 GBK 字节解成错字，而 cp1252 把**所有东西**解成零损伤的拉丁文本，会让损伤
# 信号失去意义 —— 一个看起来干净的错答案比一个被标记的答案更糟。
_AUTO_ENCODINGS = ("utf-8", "gb18030")

# 打分的兜底：每个候选对这个文件都是有损的，所以损伤最少的胜出。cp1252 因上面
# 同样的理由缺席。
_SCORED_ENCODINGS = ("utf-8", "gb18030")


def _canon_charset(name: str) -> str:
    """`GBK` / `gbk` / ` utf_8 ` / `UTF-8` 是两颗字符集，不是四颗。"""
    return (name or "").strip().lower().replace("-", "").replace("_", "")


def encoding_was_declared(body: bytes, declared: str) -> bool:
    """声明的那一颗是不是**真的解得开**这份字节。

    这是 `encoding_basis` 的一半问题，也是 R02-021 那一刀的落点：判据说的是「有没有人
    声明过」，而设计说的是「解不开的名字落回检测」（本模块上面第 2 条）。名字解不开时
    跑的是检测那颗 —— 「有人说过」就再也不等于「这一份按谁读」。
    """
    name = (declared or "").strip()
    if not name:
        return False
    try:
        body.decode(name)
    except (LookupError, UnicodeError):
        return False
    return True


def contested_rival(body: bytes, used: str) -> str:
    """点名的这颗解得开时，同一批字节的**第二种干净读法**叫什么名字（没有则 ""）。

    三个条件都要成立，否则这一格不开口（做成噪声比不做更坏）：

      * 竞争者自己无可疑（``decode_damage`` 非 0 就出局 —— 真 big5 件被 gb18030 读成一堆
        PUA 时，那份读数没有资格说"我也能读"，实测 damage 14）；
      * 它是个**不同的名字**（归一化之后）；
      * 它的**文本与点名的这一份逐字不同**（GBK 件点名 gbk 时自动那颗走 gb18030，名字不同
        而同一份文本 —— 没有第二种读数可交代）。
    """
    try:
        named = body.decode(used)
    except (LookupError, UnicodeError):  # pragma: no cover - `used` 刚刚解码成功过
        return ""
    rival_text, rival_used, rival_damage = decode_file_bytes(body, "")
    if rival_damage or not rival_used or _canon_charset(rival_used) == used:
        return ""
    if rival_text == named:
        return ""
    return _canon_charset(rival_used)


def encoding_basis(body: bytes, declared: str, used: str) -> str:
    """这一份是**按谁**读的：``declared`` / ``contested`` / ``fallback`` / ``guessed`` / ``""``。

    一个作者回答一族问题，因为这五个读数在调用方手里是同一个问句（「我看到的这个编码
    名字是谁说了算的」）：

      * ``declared`` —— 声明的那一颗自己解开了，而且这份字节**没有第二种干净的读法**，
        读的就是它（调用方的，或文件里 meta 说过的那一颗）。这是判定。
      * ``contested`` —— 声明的那一颗解开了，正文也照它交，可同一批字节还有第二颗名字
        同样严格解得开、那份读数自身无可疑，而两份文本逐字不同。R02-037 量的是这一格：
        一份 GBK 文件点名 ``big5``，交回「詩寢艦瘧婓盄潰聆ㄩ疏…」—— 而
        ``decode_damage`` = 0、``misdecode_score`` = 0、``charset_is_in_doubt`` = False，
        因为错语言的每一个字都是 big5 表里的合法字符。名字与字节**相容**不等于字节**属于**
        那个名字；便宜的判别器不存在（上面 ``guessed`` 那条实测边界就是原因），能诚实说的
        只有「另一份读数也在，它叫 gb18030」。
      * ``fallback`` —— 有人声明过，但那颗名字解不开这份字节，最后跑的是检测那一颗。
        **这一格是 R02-021**：旧判据只看「有没有人声明过」，于是把检测的果实标成调用方
        的种子，连带关掉 `quality._agent_hints` 里那句唯一说真话的申报。
      * ``guessed`` —— 没人声明，gb18030 干净地解开了。GB18030 严格解得开 Shift_JIS 与
        EUC-KR 的字节（本模块上面那条实测边界），出来的是看着像中文的错字、damage 0，
        便宜的判别器不存在 —— 能诚实说的只有「这一个是 dhole 选的」。
      * ``""`` —— 不需要说话：UTF-8 严格解开的、BOM / UTF-16 自己说过自己的。

    搬运不许长第二个作者：`parse._decode_body` → `server.doc_parse` →
    `quality._agent_hints`。
    """
    decl = _canon_charset(declared)
    win = _canon_charset(used)
    if decl:
        if not encoding_was_declared(body, declared):
            return "fallback"
        if decl != win:
            return "fallback"
        return "contested" if contested_rival(body, win) else "declared"
    if win == "gb18030":
        if any(body.startswith(bom) for bom, _ in _BOM_ENCODINGS):
            return ""
        # BOM 被剥掉的 UTF-16/32 body 按 GB18030 会"干净地"解成满是 NUL 的
        # 文本；那个解码器不需要旁边再挂一面旗子。
        if b"\x00" in body[:64]:
            return ""
        return "guessed"
    return ""


def decode_file_bytes(body: bytes, declared: str = "") -> tuple[str, str, int]:
    """解码本地文件字节。返回 ``(text, encoding_used, damage)``。

    ``damage`` 是 :func:`decode_damage` 的计数，但只在解码**确实存疑**时非零：
    严格 UTF-8 或 GB18030 解出的文本，唯一瑕疵只是引用了一个乱码例子时，那是对
    字节的**正确**读取，而调用方用这个数只为了判词，所以给它 0，别的什么都不给。
    走到打分比较说明两个严格解码都失败了，那里的损伤必然带着丢失的字节。

    从不抛异常：调用方总能拿到文本、编码名与损伤分，于是它可以报告一次不可靠的
    解码，而不必从字符本身去猜。
    """
    for bom, enc in _BOM_ENCODINGS:
        if body.startswith(bom):
            try:
                return body.decode(enc), enc, 0
            except (LookupError, UnicodeError):  # pragma: no cover - 有 BOM 意味着有效
                break

    if declared:
        try:
            return body.decode(declared), declared, 0
        except (LookupError, UnicodeError):
            # 未知或错误的名字：不是可用的指令，所以去检测，
            # 而不是交回一锅替换字符。
            pass

    if b"\x00" in body[:64]:
        for enc in ("utf-16", "utf-32"):
            try:
                return body.decode(enc), enc, 0
            except (LookupError, UnicodeError):
                continue

    for enc in _AUTO_ENCODINGS:
        try:
            text = body.decode(enc)
        except UnicodeDecodeError:
            continue
        if not charset_is_in_doubt(text):
            return text, enc, 0
        # 严格解开了却带着损伤（GB18030 对 Big5 就是这样）。落进打分比较，
        # 而不是把它当成功交回 —— 调用方无论哪条路都需要这个损伤数。
        break

    best: tuple[str, str, int] | None = None
    for enc in _SCORED_ENCODINGS:
        try:
            text = body.decode(enc, errors="replace")
        except LookupError:  # pragma: no cover - 两者都是标准库编解码器
            continue
        damage = decode_damage(text)
        if best is None or damage < best[2]:
            best = (text, enc, damage)
    return best if best else ("", "utf-8", 0)
