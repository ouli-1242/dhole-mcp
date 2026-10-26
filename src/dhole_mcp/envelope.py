"""v10 研究级信封：每次抓取都计算页面类型、新鲜度与来源权威信号，让 agent 一次拿到
可信度 + 时效 + 下一步，不必再发一次调用。

所有函数只用标准库，跑在已抽取的 HTML / URL 字符串上，让信封自包含、低开销。

设计原则：
- 保守优先于召回。一个错的 ``is_official=True`` 或标错的
  ``page_type="list"`` 会把 agent 带上坏路径。信号弱时默认 "unknown" / False；
  只在证据强时下断言。
- page_type 是拆开的：结构信号在 _translate_response 里从原始 HTML 检测；错误派生
  的信号（js_shell/auth_wall）在 _with_agent_hints 里覆盖，因为它们是定论（抽取后由
  _annotate_quality 设置）。它只回答**结构**（正文 / 列表 / 某种媒体 / 一道墙）——
  17.0 §3 第 28 条删掉了它说的 forum / qa / docs，因为那三个值说的是
  「这是个什么站」，而那句话 source_type（classify_source）本来就在说。
- 新鲜度优先看修改/更新日期而非发布日期 —— 上周更新过的页面不算陈旧，即使它首次
  发表于 2014 年。
"""
from __future__ import annotations

import re
from datetime import datetime, date, timedelta, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

# 页面超过这个年龄就标记为陈旧。对无法判断类别的来源的扁平兜底；下面按域感知的
# 时限会细化它（G15）。
STALE_DAYS = 365
# 这一类来源通常能保持为真多久。扁平 365 天会把一篇 14 个月前的新闻判成新鲜、
# 把一篇 2001 年创建的 wiki 文章判成 24 岁 —— 两个都是答错了问题。新闻以周计；
# 参考资料、论文、文档、仓库与问答维护多年，很少因年龄而失效。
NEWS_STALE_DAYS = 30
LONG_LIVED_STALE_DAYS = 730

# ─── 来源权威分类 ──────────────────────────────────────────────────

# 已知新闻域名（保守集合）。尽力而为；不在名单里的 "unknown" 也没问题 ——
# source_type 是提示，不是判决。
_NEWS_DOMAINS = (
    "nytimes.com", "bbc.com", "bbc.co.uk", "reuters.com", "theguardian.com",
    "washingtonpost.com", "bloomberg.com", "apnews.com", "aljazeera.com",
    "cnbc.com", "ft.com", "economist.com", "techcrunch.com", "theverge.com",
    "arstechnica.com", "wired.com", "cnn.com", "npr.org", "politico.com",
    "axios.com", "businessinsider.com", "forbes.com", "theatlantic.com",
    "time.com", "wsj.com", "cnet.com", "zdnet.com", "independent.co.uk",
    "lemonde.fr", "spiegel.de", "elpais.com", "scmp.com",
    "36kr.com", "ithome.com", "huxiu.com", "pingwest.com", "thepaper.cn",
    # 注：nature.com / science.org 曾经住在这里。它们是期刊不是新闻：把它们放进
    # 30 天的新闻时限，会把每一篇超过一个月的论文标记为陈旧。它们现在在
    # _PAPER_DOMAINS 里。
)

_QA_DOMAINS = (
    "stackoverflow.com", "stackexchange.com", "serverfault.com",
    "superuser.com", "mathoverflow.com", "askubuntu.com", "quora.com",
    "stackapps.com",
)

_GITHUB_DOMAINS = (
    "github.com", "raw.githubusercontent.com", "gist.github.com",
)

# 百科全书 / 标准 / 权威参考站：耐久的页面，这份名单存在的目的就是决定它们落进
# 哪个陈旧桶（730 天，不是 30）。"reference" 刻意与搜索命中用同一个词，让抓取的
# 页面与搜索结果以同一种方式描述同一个站点 —— 而既然两类行现在共用**这一颗**函数，
# 它们就不会再次漂开。
# (体验修复刀2-S6：此前 `search._source_type` 另养了一套域名表，同一个字段名两套词表。
#  合并的理由不是整洁，是实测：拿 777 条真实 SERP URL 直接换调这颗函数，有 191 行
#  从"有名"掉成"unknown"（blog 88 / docs 40 / forum 33 …），净亏 121 个标签。所以
#  这一套表被折进来，而不是那一套被扔掉 —— 一套词表，两份知识。)
_REFERENCE_DOMAINS = (
    "wikipedia.org", "wikimedia.org", "wikiwand.com", "britannica.com",
    "iana.org", "ietf.org", "rfc-editor.org", "w3.org", "unicode.org",
    "w3schools.com", "geeksforgeeks.org", "tutorialspoint.com", "caniuse.com",
    "mdn.io",
)

# 按**名字**而非子域形状识别的文档站（上面的 `docs.` / `developer.` 测试看不见
# kubernetes.io 或 pytorch.org，而按任何合理解读它们都是文档站）。与 docs-site 一样
# 长寿命，且**不是** official：任何公司都能注册一个发布文档的域名。
_DOCS_SITE_DOMAINS = (
    # Python / ML
    "docs.python.org", "realpython.com", "python.readthedocs.io",
    "docs.anthropic.com", "docs.openai.com", "platform.openai.com",
    "pytorch.org", "tensorflow.org", "huggingface.co",
    "docs.llama.com", "docs.mistral.ai", "docs.cohere.com",
    # Web
    "developer.mozilla.org", "developers.google.com", "web.dev",
    "react.dev", "vuejs.org", "angular.dev", "svelte.dev", "nextjs.org",
    "nodejs.org", "expressjs.com", "fastapi.tiangolo.com",
    # 系统 / 云 / 厂商文档
    "docs.rs", "doc.rust-lang.org", "go.dev", "docs.golang.org",
    "learn.microsoft.com", "docs.microsoft.com", "docs.github.com",
    "docs.aws.amazon.com", "cloud.google.com", "docs.docker.com",
    "kubernetes.io", "developer.android.com", "developer.apple.com",
    "flutter.dev", "docs.flutter.dev", "kotlinlang.org", "swift.org",
    "pandas.pydata.org", "numpy.org", "scipy.org", "docs.sqlalchemy.org",
    "readthedocs.io", "gitbook.io", "docusaurus.io", "nginx.org",
)

# 是代码仓库但非 GitHub 的来源主机。注册表（pypi、npm、crates、pkg.go.dev）在这张
# 名单**之上**检查，保持 `reference` + is_official —— 它们决定一个名字解析到什么，
# 这是与托管代码不同的属性。
_CODE_HOST_DOMAINS = (
    "gitlab.com", "bitbucket.org", "codeberg.org", "sourceforge.net",
    "gitee.com", "gitea.com", "sr.ht",
)

# 搜索侧那份表认识、而这一份此前不认识的问答与社区站。`qa` 在 `forum` **之前**
# 检查：stackoverflow 过去被一侧报成 "forum"、另一侧报成 "qa"，而更具体的词正是
# 合并这两张表的重点。
_COMMUNITY_DOMAINS = (
    "reddit.com", "zhihu.com", "v2ex.com", "segmentfault.com", "discord.com",
    "news.ycombinator.com", "discuss.python.org", "internals.rust-lang.org",
    "forum.rust-lang.org",
)

_BLOG_PLATFORM_DOMAINS = (
    "medium.com", "wordpress.com", "substack.com", "dev.to", "hashnode.com",
    "freecodecamp.org", "blogspot.com", "ghost.io", "notion.so", "telegra.ph",
    "juejin.cn", "csdn.net", "cnblogs.com", "jianshu.com",
)

# 同行评审 / 预印本文献。与 _REFERENCE_DOMAINS 同一理由：耐久，但名字可被注册，
# 所以**不是** is_official。
_PAPER_DOMAINS = (
    "arxiv.org", "dl.acm.org", "ieeexplore.ieee.org", "openreview.net",
    "semanticscholar.org", "biorxiv.org", "medrxiv.org", "nature.com",
    "science.org", "sciencedirect.com", "link.springer.com", "pnas.org",
    "nejm.org", "pubmed.ncbi.nlm.nih.gov",
    "ieee.org", "springer.com", "wiley.com", "cell.com", "thelancet.com",
    "paperswithcode.com", "scholar.google.com",
)

# 持续被编辑的协作参考站。它们的"发布"日期是**文章创建**的时间，不是内容最后一次
# 为真的时间，所以从它推断陈旧度是系统性假阳性（见 _is_continuously_edited）。
_CONTINUOUSLY_EDITED_DOMAINS = (
    "wikipedia.org", "wikimedia.org", "wikiwand.com", "britannica.com",
    "fandom.com",
)

# 名字的**控制权**（不只是字符串本身）握在第三方买不通的机构手里的域名：注册局
# 运营者与标准机构本身，以及决定名字解析到什么的包注册表。这些是 is_official 可以
# 说 True 而不削弱其含义的参考域 —— 规则仍是"权威是运行这个名字的人"，不是"页面
# 看起来权威"。百科全书站（wikipedia.org）**不**在这里：内容由社区编辑，而这个字段
# 说的是来源，不是页面对不对。
_REGISTRY_CONTROLLED_DOMAINS = (
    "iana.org", "icann.org", "rfc-editor.org", "ietf.org", "w3.org",
    "unicode.org", "ieee.org",
    "pypi.org", "npmjs.com", "crates.io", "rubygems.org", "pkg.go.dev",
    "metacpan.org", "packagist.org", "nuget.org",
)

# 国别政府/学术形式："<x>.gov.<cc>" / "<x>.ac.<cc>"（gov.br、gov.cn、gov.uk、
# ac.uk）。这对标签由 ccTLD 注册局保留，第三方无法拥有。两字母尾巴让匹配安全：
# "foo.gov.attacker.com" 以 ".com" 结尾，不是 ".gov.<cc>"。
_CC_GOV_RE = re.compile(r"\.gov\.[a-z]{2}$")
_CC_AC_RE = re.compile(r"\.ac\.[a-z]{2}$")


def classify_source(url: str) -> tuple[str, bool]:
    """把一个 URL 的域名归类成 source_type + is_official 旗。

    返回 (source_type, is_official)。只有名字本身无法被第三方买走时 is_official 才
    为 True：注册局控制的命名空间（gov、edu、github）以及运行某个命名空间的机构 ——
    IANA/ICANN/RFC Editor/IETF/W3C/Unicode 与包注册表（见
    _REGISTRY_CONTROLLED_DOMAINS）。"docs.*" / "developer.*" 这类子域形状报成
    source_type="docs-site"，但**不是** official：它们只说明站点把一个子域命名为
    "docs"。社区编辑的参考站（wikipedia.org 之流）也不是 official —— 这个字段说的是
    谁控制来源，不是页面读起来对不对。其余一律 False（保守）。
    """
    if not url:
        return "unknown", False
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return "unknown", False
    # 去掉 userinfo@ 与 :port
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    if ":" in host:
        host = host.split(":", 1)[0]
    if not host:
        return "unknown", False

    # 政府 / 教育 / github：仅有的几类"名字本身"无法被第三方买走的域名
    # （注册局控制的命名空间）。
    #
    # 注：这里过去测的是 host 里的 ".gov."，任何攻击者可注册的域名都满足 ——
    # "foo.gov.attacker.com" 被归成 gov/is_official，并作为权威信号直接交给了 agent。
    if host == "gov" or host.endswith(".gov") or _CC_GOV_RE.search(host):
        return "gov", True
    if host.endswith(".edu") or host.endswith(".ac.uk") or _CC_AC_RE.search(host):
        return "edu", True
    if host in _GITHUB_DOMAINS or host.endswith(".github.io"):
        return "github", True

    # 运行某个命名空间的机构（IANA/ICANN/RFC Editor/IETF/W3C/Unicode）以及决定
    # 名字解析到什么的包注册表：与 gov/edu 同一属性 —— 别人拿不到这个名字 ——
    # 所以这些确实给 is_official=True（G17 问过为什么 iana.org 与 rfc-editor.org
    # 不是）。
    if any(host == d or host.endswith("." + d)
           for d in _REGISTRY_CONTROLLED_DOMAINS):
        return "reference", True

    # 厂商文档子域（docs.* / developer.*）。这是一个**形状**信号，不是权威信号：
    # 任何站点都能把一个子域命名为 "docs." —— 包括整个目的就是显得权威的那种。
    # 所以它**不是** is_official：agent 不能把它当成某个主题的规范来源。
    if host.startswith("docs.") or host.startswith("developer.") or host.startswith("developers."):
        return "docs-site", False

    # 按**名字**识别的文档站 —— 上面的形状规则看不见 kubernetes.io、pytorch.org
    # 或 learn.microsoft.com，而按任何合理解读它们都是文档站。与形状规则同样
    # 不是 official。
    if any(host == d or host.endswith("." + d) for d in _DOCS_SITE_DOMAINS):
        return "docs-site", False

    # 是代码仓库但非 GitHub 的来源主机（github 在上面就接住了，带 is_official，
    # 因为名字由注册局控制）。
    if any(host == d or host.endswith("." + d) for d in _CODE_HOST_DOMAINS):
        return "repo", False

    # 问答站 —— 在社区/论坛名单**之前**，因为 stackoverflow 过去一侧是 "forum"、
    # 另一侧是 "qa"，而更具体的词正是这两张表合并的全部理由。
    if host in _QA_DOMAINS or host.endswith(".stackexchange.com") or host.endswith(".stackoverflow.com"):
        return "qa", False

    # 论坛 / 社区。
    if any(m in host for m in ("forum", "forums", "community", "discourse", "board")):
        return "forum", False
    if any(host == d or host.endswith("." + d) for d in _COMMUNITY_DOMAINS):
        return "forum", False

    # 博客。
    if host.startswith("blog.") or any(host == d or host.endswith("." + d)
                                       for d in _BLOG_PLATFORM_DOMAINS):
        return "blog", False

    # 电商。
    if host.startswith("shop.") or host.startswith("store.") or host in ("amazon.com", "ebay.com") or host.endswith(".shop"):
        return "ecommerce", False

    # 参考 / 标准 / 百科全书，然后是同行评审文献。两者都耐久、对某主题权威，也都
    # 是可注册的名字（所以 is_official 保持 False）—— 重点是别再给 iana.org /
    # wikipedia.org / rfc-editor.org 报 "unknown"（G17）。
    if any(host == d or host.endswith("." + d) for d in _REFERENCE_DOMAINS):
        return "reference", False
    if any(host == d or host.endswith("." + d) for d in _PAPER_DOMAINS):
        return "paper", False

    # 新闻（已知集合）。
    if any(host == d or host.endswith("." + d) for d in _NEWS_DOMAINS):
        return "news", False

    # 最后，一条不需要域名知识的"页面是什么"路径形状：/docs/ 下的 URL 是文档，
    # /blog/ 或 /post/ 下的是一篇文章。这条从搜索侧那张表折进来，实测 747 条真实
    # SERP URL 里有 14 条唯一的命名依据就是路径 —— 每一条都是某张表都不认识的站点上
    # 的文档页（typescriptlang.org、redis.io、一个 MDN 镜像）。
    # `/api/` 刻意**不**折进来：API 路径是机器端点，不是文档，把它叫 docs-site 还会
    # 给它 730 天的陈旧时限（见 compute_freshness）。域名表在**这条规则之上**检查，
    # 所以新闻站的 "/post/" URL 仍然报 "news"。
    path = (urlparse(url).path or "").lower()
    if "/docs/" in path:
        return "docs-site", False
    if "/blog/" in path or "/post/" in path:
        return "blog", False

    return "unknown", False


def _host_of(url: str) -> str:
    """小写主机名，去掉 userinfo/端口；没有则返回 ""。"""
    try:
        host = urlparse(url or "").netloc.lower()
    except Exception:
        return ""
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    if ":" in host:
        host = host.split(":", 1)[0]
    return host


def _is_continuously_edited(url: str) -> bool:
    """内容总在被编辑的协作参考站为 True。

    这些站上通常唯一可得的日期是**文章创建**日期，它对"内容现在还是不是真的"什么都
    不说。修复前实测：en.wikipedia.org/wiki/HTTP 从一个 2001 年的创建日期报出
    content_age_days=9098 / is_stale=true，而那一页此后一直有人在编辑。差距报告
    要求的正是这种处理："或对 wiki 类站点禁用此推断"。
    """
    host = _host_of(url)
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in _CONTINUOUSLY_EDITED_DOMAINS)


def _stale_days_for(url: str) -> int:
    """这一类来源通常能保持为真多久（G15）。

    news -> 30 天；reference / paper / docs-site / github / repo / qa -> 730 天；
    其余保持原来的扁平 365。因此没有 URL 的调用方看到的正是 pre-16.0 的行为。

    `repo` 在这里加入 `github` 是刻意的：两者过去被拆开，只是因为 GitHub 住在
    注册局控制名单里、顺带拿到了长时限。gitlab 或 codeberg 的仓库页并不比 github
    的短命，而时限是对**页面种类**的声明，不是对名字归属的声明（那是 is_official
    另外的职责）。
    """
    if not url:
        return STALE_DAYS
    st, _ = classify_source(url)
    if st == "news":
        return NEWS_STALE_DAYS
    if st in ("reference", "paper", "docs-site", "github", "repo", "qa"):
        return LONG_LIVED_STALE_DAYS
    return STALE_DAYS


# ─── 新鲜度 ────────────────────────────────────────────────────────

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y")


_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
                 "%Y年%m月%d日")

# SERP 上那一枚日期章的形状。相对与绝对必须分开识别，因为它们的**精度**不同：
# "2 days ago" 的意义依赖抓取那一刻，而 `2026-10-01` 不依赖（4.6 的 `before=` 逐行判决
# 要拒的正是前者）。单位表按各家版面实测的形状列：bing_global 是英文 "… ago"，
# 中文三家是 "N天/周/月/年前"，另有 "刚刚 / 昨天 / 前天" 这一族不带数字的写法。
_RELATIVE_DATE_RE = re.compile(
    r"\d+\s*(?:second|minute|hour|day|week|month|year)s?\s+ago"
    r"|刚刚|刚才|just now"
    r"|\d+\s*(?:个月|秒|分钟|小时|天|日|周|星期|月|年)前"
    r"|前天|昨天", re.I)
_RELATIVE_UNIT_RE = re.compile(
    r"(\d+)\s*(second|minute|hour|day|week|month|year|秒|分钟|小时|天|日|周|星期|个月|月|年)",
    re.I)
# 只到月的写法：`2026-03` / `2026年3月` / `2026.3`。那一眼后面的 `-`、`/`、`日`、数字
# 是"还有日"的证据，必须让整条规则让位给 `_parse_date`，否则 `2026-03-14` 会被降级成
# 一个月精度并且把日子补成 1 —— 那是把一个已知的事实改写成猜测。
_MONTH_ONLY_RE = re.compile(r"(20\d{2})\s*[-/年.]\s*(\d{1,2})\s*月?(?![-/日\d月])")
_BARE_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def parse_serp_date(text: str, now: "date | None" = None) -> tuple[date | None, str, str]:
    """把 SERP 上那一段日期文本读成 (日期, 精度, 原文)。精度取 day/month/year/relative。

    存在的理由是**一句看着精确的谎**：搜索结果上写的是 "3 hours ago"、"5天前"、"昨天"，
    存成光秃秃的 ISO 就等于声称"这一天发布"，而那句话的真实精度是"按抓取那一刻推算"。
    4.6 的 `before=` 逐行判决要拒的正是这些行 —— 拒得对的前提是精度跟着值一起走。

    三条规则，每条都有它的理由：
      · 绝对形式**交给 `_parse_date`**（这一族唯一的作者），这里只负责判它到 day 的精度；
        所以 `2026-10-01`、`October 1, 2026`、`2026年10月1日` 都是 `day`。
      · 只到月或只到年的写法回 `month` / `year`，且**日补成 1**（那是"这一月/这一年"的
        最小值，不是一月一日发布的断言）；裸年份只在整个字符串就是一个年份时才认 ——
        "See 1984 by Orwell" 不是一条发布日期。
      · 相对写法回 `relative`，日期按 `now`（默认今天）推算；`now` 可注入是因为这条必须
        能被测试钉住，而 `date.today()` 会让守卫在某个跨月的凌晨自己变红。

    返回的第三个值是**命中的原文**，给 `published_basis` 引用：读者要能复核这一行的日期
    是从哪几个字读出来的，而不是相信有一个解析器猜对了。

    一条实测到的限度（so360 冻存页）：一行上同时写着相对与绝对两截时
    （`1个月前 · 55次浏览 · 发布时间：2026年5月8日`），命中的是**相对**那一截。绝对形式
    要求整串就是一个日期（`_parse_date` 走的是整串口径），而那一截前面还有别的字。后果
    是这一行落在 `relative`、因此不参与 `before=` 的比较 —— 是保守的那一侧（宁可少判一行
    并计入 `rows_relative_precision`，不猜一个日期把行丢掉），而 `published_basis` 印出的
    正是被读走的那几个字，读者看得见漏掉的是哪一截。
    """
    raw = (text or "").strip()
    if not raw:
        return None, "", ""
    today = now or datetime.now(timezone.utc).date()

    rel = _RELATIVE_DATE_RE.search(raw)
    if rel:
        token = rel.group(0)
        if "前天" in token:
            return today - timedelta(days=2), "relative", token
        if "昨天" in token:
            return today - timedelta(days=1), "relative", token
        c = _RELATIVE_UNIT_RE.search(token)
        if c is None:                      # 刚刚 / 刚才 / just now
            return today, "relative", token
        n, unit = int(c.group(1)), c.group(2).lower().rstrip("s")
        if unit in ("秒", "分钟", "小时", "second", "minute", "hour"):
            # 一小时前的"那一天"与今天在这一格里不可分辨：给今天，精度说 relative。
            return today, "relative", token
        days = {"day": 1, "天": 1, "日": 1, "week": 7, "周": 7, "星期": 7,
                "个月": 30, "month": 30, "月": 30, "year": 365, "年": 365}.get(unit)
        if days:
            return today - timedelta(days=n * days), "relative", token

    if (m := _MONTH_ONLY_RE.search(raw)):
        y, mo = int(m.group(1)), int(m.group(2))
        if 1 <= mo <= 12:
            return date(y, mo, 1), "month", m.group(0)
    for fmt in ("%B %Y", "%b %Y"):
        try:
            return datetime.strptime(raw, fmt).date(), "month", raw
        except ValueError:
            continue

    d = _parse_date(raw)
    if d is not None:
        return d, "day", raw

    if (y := _BARE_YEAR_RE.fullmatch(raw)) and 1900 <= int(y.group(0)) <= today.year + 1:
        return date(int(y.group(0)), 1, 1), "year", y.group(0)
    return None, "", ""


def _parse_date(s: str) -> date | None:
    """从元数据字符串解析日期。处理 ISO（带偏移/Z）、紧凑 YYYYMMDD 与几种人类
    格式。解析不了返回 None。"""
    if not s:
        return None
    s = s.strip()
    if not s:
        return None
    # 紧凑 YYYYMMDD（wayback 时间戳、部分元数据）。
    if re.fullmatch(r"\d{8}", s):
        try:
            return datetime.strptime(s, "%Y%m%d").date()
        except ValueError:
            return None
    # ISO 8601（3.11+ 的 fromisoformat 处理偏移与 'Z'）。从完整时间戳取日期；
    # 兜底取前 10 个字符。
    for cand in (s, s[:10]):
        try:
            return datetime.fromisoformat(cand.replace("Z", "+00:00")).date()
        except ValueError:
            continue
    # 人类格式 —— 先试整串，再试 32 字符前缀。
    for fmt in _DATE_FORMATS:
        for cand in (s, s[:32]):
            try:
                return datetime.strptime(cand, fmt).date()
            except ValueError:
                continue
    return None


def compute_freshness(metadata: dict[str, Any], fetched_at_iso: str,
                      url: str = "") -> tuple[int | None, bool]:
    """从页面自己的日期返回 (content_age_days, is_stale)。

    优先修改/更新日期而非发布日期（上周更新的页面仍然新鲜，即使首次发表于 2014 年）。
    拿不到任何日期、日期在未来（坏数据）、或持续编辑的站点上唯一日期是创建日期时，
    返回 (None, False)。

    ``url`` 可选，只让**陈旧**判决更准（G15）：年龄本身是关于页面的事实，但这个年龄
    是否意味着"陈旧"取决于这类来源通常能保持为真多久 —— 新闻 30 天，reference/docs/
    repo/QA 730 天，其余 365。省略它就用扁平的 365 天时限，与从前完全一致。

    是 None 不是 -1：负年龄读起来像"未来两天"，那是与"页面没有发布任何日期"不同的
    陈述。对它做算术的 agent 会悄悄拿一个哨兵值比较。
    """
    if not metadata:
        return None, False
    # 优先 modified > published > created > 泛用 'date'。还会记录日期来自哪个字段，
    # 因为它决定下面的一个分支。
    modified_str = (metadata.get("modified_time") or metadata.get("mod_date") or "")
    published_str = (metadata.get("published_time") or metadata.get("creation_date")
                     or metadata.get("date") or "")
    date_str = modified_str or published_str
    content_date = _parse_date(date_str) if date_str else None
    if content_date is None:
        return None, False
    # wiki 通常唯一的日期就是创建日期。对一页今早刚编辑过的内容报"这页已经 9098 天
    # 大了"是错答案，而错的 is_stale 会把 agent 送去重抓一份本来新鲜的东西。没有修改
    # 日期就没有时效信号可报，所以什么都不报（None = "没有我们能背书的日期"，与
    # "新鲜"不是同一个陈述）。只适用于持续编辑这一类。
    if not modified_str and url and _is_continuously_edited(url):
        return None, False
    fetched_date = _parse_date(fetched_at_iso) if fetched_at_iso else None
    if fetched_date is None:
        # 兜底用今天（UTC），让 fetched_at 缺失时新鲜度仍然可用。
        fetched_date = datetime.now(timezone.utc).date()
    delta = (fetched_date - content_date).days
    if delta < 0:
        # 未来日期 = 坏元数据；年龄信号不可信。
        return None, False
    return delta, delta > _stale_days_for(url)


# ─── 页面类型检测 ──────────────────────────────────────────────────

# 可见文本里的付费墙短语（匹配解析后的文本，绝不匹配原始 HTML —— 见
# _paywall_evidence）。
_PAYWALL_MARKERS = ("subscribe to continue", "subscribe to read", "this article is for subscribers",
                    "create a free account to continue", "sign in to continue reading",
                    "you've reached your free article limit", "subscriber-only content",
                    "premium content")
# 明确的 HTML data 属性是结构性付费墙信号。刻意**不**匹配裸词 "paywall"：它会出现在
# 普通 README 链接、解释性文案、脚本或元数据里。解析 HTML，好让属性名被精确匹配，
# 属性值也无法冒充属性名。
_PAYWALL_ATTRIBUTE_NAMES = frozenset({
    "data-paywall",
    "data-content-gate",
    "data-subscription-wall",
})
_FALSE_PAYWALL_ATTRIBUTE_VALUES = frozenset({
    "0", "false", "no", "off", "disabled", "none", "null",
})
_PAYWALL_IGNORED_TAGS = frozenset({"script", "style", "noscript", "template"})
_PAYWALL_METADATA_TAGS = frozenset({"base", "link", "meta"})
_PAYWALL_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
    "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
    "table", "td", "th", "tr", "ul",
})


class _PaywallEvidenceParser(HTMLParser):
    """收集可见文本与生效的、精确的付费墙属性。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.visible_text: list[str] = []
        self.has_active_attribute = False
        self._ignored_stack: list[str] = []

    def _inspect_attributes(self, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name.lower() not in _PAYWALL_ATTRIBUTE_NAMES:
                continue
            normalized = value.strip().lower() if value is not None else None
            if normalized not in _FALSE_PAYWALL_ATTRIBUTE_VALUES:
                self.has_active_attribute = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._ignored_stack:
            if tag in _PAYWALL_IGNORED_TAGS:
                self._ignored_stack.append(tag)
            return
        if tag in _PAYWALL_IGNORED_TAGS:
            self._ignored_stack.append(tag)
            return
        if tag not in _PAYWALL_METADATA_TAGS:
            self._inspect_attributes(attrs)
        if tag in _PAYWALL_BLOCK_TAGS:
            self.visible_text.append(" ")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if (
            not self._ignored_stack
            and tag not in _PAYWALL_IGNORED_TAGS
            and tag not in _PAYWALL_METADATA_TAGS
        ):
            self._inspect_attributes(attrs)
            if tag in _PAYWALL_BLOCK_TAGS:
                self.visible_text.append(" ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._ignored_stack:
            if tag in self._ignored_stack:
                while self._ignored_stack.pop() != tag:
                    pass
            return
        if tag in _PAYWALL_BLOCK_TAGS:
            self.visible_text.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._ignored_stack:
            self.visible_text.append(data)


def _paywall_evidence(html: str) -> tuple[str, bool]:
    """返回页面可见文本，以及是否存在生效的付费墙属性。"""
    parser = _PaywallEvidenceParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # 上游 HTML 畸形也不能弄坏抓取响应。
        return "", False
    return " ".join("".join(parser.visible_text).split()), parser.has_active_attribute


def visible_text_chars(html: str) -> int:
    """这一页**自己**有多少可见字符（不经过抽取器）。

    `detect_page_type` 的列表判据、抽取塌陷的分岔、以及"正文这么少到底是站点没给还是
    我没抽到"的那个问句，用的都是这一把尺 —— 各量一遍就会各有一套阈值。
    """
    return len(_paywall_evidence(html)[0])


#: 抽取器**本该**丢掉的站点外壳。刻意**不**含 <aside>：aside 可能是实质正文
#: （`/longaside` fixture 正是那个对照），删掉它会掩盖一次真实的抽取丢失。
_STRIP_CHROME_RE = re.compile(
    r"<(nav|header|footer|script|style|noscript)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)


def visible_text_chars_without_chrome(html: str) -> int:
    """去掉 nav/header/footer 之后的可见文本 —— 页面自己的散文地板。

    塌陷闸的整页计数包含站点外壳；一篇完整文章压在厚重导航下面，因此可能看起来像
    丢了（R04-016a）。这是第二把尺，只在第一把已经响过时才量。它保留 <aside>：
    删掉它会让一次真实的 aside 丢失变得不可见（`/longaside` 对照）。
    """
    return len(_paywall_evidence(_STRIP_CHROME_RE.sub("", html))[0])


#: 抽取塌陷的分界：抽出来的字数少于这一页自身可见字数的 50%，且这页至少给自己写了
#: 400 个字符。两处读它，所以只有一个定义 —— `trafilatura_extractor` 用它决定要不要换
#: recall 臂重试，`quality` 用它决定"正文很少"该不该被说成"这是个 JS 壳"。
#: 实测基准（同一把尺）：books.toscrape 的 travel 列表页 196/1374=0.14 ⇒ 塌；
#: quotes.toscrape 首页 2179/1681=1.30、gnu.org free-sw 25785/25245=1.02 ⇒ 不塌。
#: 下限那 400 个字符挡的是真壳：SPA 的空 #app 在 DOM 里本来就没几个字，visible 到不了
#: 这个门，于是它照样被判成壳、照样值得起浏览器。
EXTRACT_COLLAPSE_RATIO = 0.5
EXTRACT_COLLAPSE_MIN_VISIBLE = 400


def is_extraction_collapsed(extracted_chars: int, visible_chars: int) -> bool:
    """抽出来的字相对页面自己的字少到不成比例。"""
    if visible_chars < EXTRACT_COLLAPSE_MIN_VISIBLE:
        return False
    return extracted_chars < visible_chars * EXTRACT_COLLAPSE_RATIO


# 经 <meta http-equiv=refresh> 或 JS location.href 赋值实现的跳转。属性值可能不带引号
# （http-equiv=REFRESH）—— 真页面上量到过，而只认引号的模式把它归成了普通页面。
_META_REFRESH_RE = re.compile(
    r'<meta\b[^>]*?http-equiv\s*=\s*["\']?refresh["\']?[^>]*?content\s*=\s*["\'][^"\']*url=',
    re.IGNORECASE,
)
_JS_REDIRECT_RE = re.compile(
    r'(?:location\.href\s*=|location\.replace|window\.location\s*=)',
    re.IGNORECASE,
)
# 同域内容链接（粗略）：<a href="/..."> 或 <a href="https://host...">
_ANCHOR_RE = re.compile(r'<a\b[^>]*?href=["\']([^"\']+)["\']', re.IGNORECASE)
# 数链接之前要剥掉的块（nav/header/footer/aside/script/style）。
_STRIP_BLOCK_RE = re.compile(
    r'<(nav|header|footer|aside|script|style|noscript)\b[^>]*>.*?</\1>',
    re.IGNORECASE | re.DOTALL,
)
_ARTICLE_TAG_RE = re.compile(r'<article\b', re.IGNORECASE)

# 内容区文本里作为链接标签的占比低于此值时，页面被视为"链接很多的散文"，而非链接
# 索引。0.10 落在实测的两簇之间：真索引页 0.16-0.43（quotes.toscrape.com 列表 0.26、
# Python 文档索引 0.16），真文章/参考页 0.01-0.05（RFC 9110 0.01、GNU 自由软件定义
# 0.05、Gentoo wiki 文章 0.03）。样本存于 tests/page_fixtures，数字写在回归测试里。
LIST_LABEL_SHARE_MIN = 0.10

# 内容区带有一段**在任何链接标签之外**的长文字，页面就是"带链接的散文"，而不是链接
# 索引 —— 无论标签占比说什么。它存在是因为单看占比恰好在 agent 最常抓的那类页面上
# 误判：一篇 Wikipedia 文章跑到 0.12（它的链接是多词文章标题，References/Listed-
# elsewhere 两块几乎是纯链接文本），高于五页样本放下的 0.10 线。
# 实测最长非标签串：HN 首页 79、quotes.toscrape.com 列表 148、FSF 分类索引 279 ——
# 而 en.wikipedia.org/wiki/Hypertext_Transfer_Protocol 前 200KB 是 705（整篇更高）、
# RFC 9110 规范页 807、GNU 自由软件定义 4k+。400 在索引一侧留下整整一个单位的余量。
LIST_PROSE_RUN_MAX = 400

# 同一条串反过来读，就是让长文页面被叫作**文章**而非 "unknown" 的依据：<article> 是
# 强信号，但远非散文唯一的形状（Wikipedia 的皮肤、标准页与大多数自托管文档把正文放在
# 裸 <div> 里，这些从前都返回未分类）。600 高于每一个实测索引页（79 / 148 / 227 /
# 279），低于文章页（705 / 756 / 807），文本下限则把一段话的存根挡在外面。
ARTICLE_PROSE_RUN_MIN = 600
ARTICLE_VISIBLE_TEXT_MIN = 4000

# 2.3 · 技术文档信号组的三条线。量自 2026-10-24 四个真页（tests/page_fixtures
# 里有当日字节）：MDN Fetch_API <main> 内 <p>=20、<pre>+<code>=38、run 只有 196 ——
# 正文被代码块与定义列表切成短段，`longest_run` 这把尺对它结构性失真（够不到
# ARTICLE_PROSE_RUN_MIN 的 600），链接密度又被侧栏推荐抬高，整篇文档于是落进
# list 闸。反例三页的读数：HN <p>=0 / code=0，Python genindex <p>=3 / code=0，
# gov.cn 门户 <p>=12 / h2+h3=23 而 code=0 —— **段落数与标题数都拦不住门户**，
# 真正判别的是"这页用代码教东西"的密度；5 颗 <pre>/<code> 以下一概不算文档。
_TECH_DOC_MIN_PARAS = 8
_TECH_DOC_MIN_CODE = 5


class _TechDocParser(HTMLParser):
    """数技术文档那三样：<main> 在场、main 内的 <p>、全文的 <pre>/<code>。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.saw_main = False
        self.in_main = 0
        self.paras_in_main = 0
        self.code_marks = 0

    def handle_starttag(self, tag, attrs):
        if tag == "main":
            self.saw_main = True
            self.in_main += 1
        elif tag == "p" and self.in_main:
            self.paras_in_main += 1
        elif tag in ("pre", "code"):
            self.code_marks += 1

    def handle_endtag(self, tag):
        if tag == "main" and self.in_main:
            self.in_main -= 1


def _tech_doc_shape(html: str) -> bool:
    """这页是不是一篇被代码块切成短段的技术文档。

    只在 list 闸的最后一步被问（见 `detect_page_type`）：那里的两把尺
    （share / longest_run）对技术文档都在说谎 —— 量出的是"侧栏链接密、正文碎"，
    而这页分明是一篇文章，`list` 那句 next_action 会把调用方送去爬一堆它已经
    读到的页面。三条信号缺一不可，每条都对应一个实测的反例：没 <main> 的
    quotes 索引、<p> 不够的 genindex、没代码密度的 gov.cn 门户。
    """
    parser = _TechDocParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return False
    return (parser.saw_main
            and parser.paras_in_main >= _TECH_DOC_MIN_PARAS
            and parser.code_marks >= _TECH_DOC_MIN_CODE)


def _is_content_href(href: str, host: str) -> bool:
    """这个 ``<a href>`` 是不是同域内容链接（不是按种类算的外壳）。

    排除页内锚点（#）、``mailto:`` / ``tel:`` / ``javascript:`` 以及站外链接。保持为
    一个谓词，因为链接**计数**与链接**标签**文本必须描述同一组链接；两边不一致时，
    下面的占比就会在计数从未数过的链接上被测出来。
    """
    href = (href or "").strip()
    if not href:
        return False
    low = href.lower()
    if low.startswith(("#", "mailto:", "tel:", "javascript:")):
        return False
    # 相对路径或同主机 = 内容链接候选。
    if href.startswith("/") or href.startswith("?"):
        return True
    try:
        h = urlparse(href).netloc.lower()
    except Exception:
        return False
    return bool(h) and (h == host or h.endswith("." + host))


class _ListShapeParser(HTMLParser):
    """量内容区的形状：链接标签占比与散文串。

    统计 href 通过 _is_content_href 的锚点内文本 —— 与 _count_content_links 同一个
    谓词，所以占比恰好是在被计数的那些链接上测的。

    除了占比，它还跟踪**不是**这些标签的最长连续可见文本（``longest_run``）：索引页的
    条目是标签 + 一小段元数据，所以无论带多少链接，它的串都很短；而文章的段落很长。
    在块标签处重置，让两个相邻短条目无法合并成一条长串。
    """

    def __init__(self, host: str) -> None:
        super().__init__(convert_charrefs=True)
        self.host = host
        self.visible_chars = 0
        self.anchor_chars = 0
        self.longest_run = 0
        self._skip = 0
        self._anchor_depth = 0
        self._counted_anchor = False
        self._run = 0

    def _end_run(self) -> None:
        self.longest_run = max(self.longest_run, self._run)
        self._run = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _PAYWALL_IGNORED_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in _PAYWALL_BLOCK_TAGS:
            self._end_run()
        if tag == "a":
            self._anchor_depth += 1
            self._counted_anchor = _is_content_href(dict(attrs).get("href") or "", self.host)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _PAYWALL_IGNORED_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag in _PAYWALL_BLOCK_TAGS:
            self._end_run()
        if tag == "a" and self._anchor_depth:
            self._anchor_depth -= 1
            self._counted_anchor = False
            self._end_run()

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        n = len(" ".join(data.split()))
        if not n:
            return
        self.visible_chars += n
        if self._anchor_depth and self._counted_anchor:
            self.anchor_chars += n
            self._end_run()
        else:
            self._run += n


def _list_shape(html: str, host: str) -> tuple[float, int]:
    """内容区的 (链接标签占比, 最长非标签散文串)。

    两者各自分辨什么，见 LIST_LABEL_SHARE_MIN 与 LIST_PROSE_RUN_MAX。
    """
    return page_content_shape(html, host)[:2]


def page_content_shape(html: str, host: str) -> tuple[float, int, int]:
    """内容区的三个形状读数：(链接标签占比, 最长非标签串, 内容区自己的可见字数)。

    第三颗是 3.4 新要的：整页可见字数（`visible_text_chars`）与内容区字数的差，说的
    是"这页的字住在 chrome 里还是住在正文里"。一处解析给三个数，两处读者（`detect_page_type`
    的列表/文章闸、`quality` 的 substance 判决）才不会各数一遍再互相矛盾。
    """
    parser = _ListShapeParser(host)
    try:
        parser.feed(_STRIP_BLOCK_RE.sub("", html))
        parser.close()
    except Exception:
        return 0.0, 0, 0
    parser._end_run()
    return (parser.anchor_chars / max(parser.visible_chars, 1),
            parser.longest_run, parser.visible_chars)


def main_content_run(html: str, host: str) -> int:
    """内容区里最长一段"不是别人链接标签"的连续文字 —— 3.4 与列表判据共用的那一把尺。

    它回答的是"这页有没有一段能引用的话"，而 `visible_text_chars` 只回答"有多少字"：
    实测一份 666 字的导航壳与一篇 387 字的正文，字数同一量级，最长串却差一个量级。
    """
    return page_content_shape(html, host)[1]


def _label_share(html: str, host: str) -> float:
    """内容区可见文本里**作为已计数链接标签**的那部分占比。

    这就是区分"页面是一串链接"与"页面链接很多"的依据：索引页的散文就是它的链接标签
    （真实存档页上 0.16-0.43），文章的散文是标签之间的句子（0.01-0.05）。

    跑在剥掉 nav/header/footer/aside 的 HTML 上，让比例两侧都描述主内容区；数的 href
    与 _count_content_links 相同，所以两者不会对"哪些链接参与"产生分歧。只解析一趟，
    所以调用方只在更便宜的链接数与密度闸已经提示 list 之后才来要它。

    从不抛异常：解析不了的标记返回 0.0，即不做 list 判决。
    """
    return _list_shape(html, host)[0]


def _count_content_links(html: str, host: str) -> int:
    """数主内容区里的同域、非平凡 <a> 链接。

    先剥掉 nav/header/footer/aside/script/style，免得外壳链接把计数抬高。排除锚点
    （#）、mailto:、javascript: 与站外链接。
    """
    return sum(
        1 for m in _ANCHOR_RE.finditer(_STRIP_BLOCK_RE.sub("", html))
        if _is_content_href(m.group(1), host)
    )


#: 句末标点（中英各一套）。量的是"这段字像不像人说的话"，不是它有多长 —— 一条
#: 导航标签再长也没有句号，一段 40 字的导语也带着它。
_PROSE_SENTENCE_RE = re.compile(r"[。！？；]|[.!?](?=\s|$)")


def answer_is_prose(text: str, *, min_chars: int = 200,
                    sentences_per_100: float = 1.0) -> bool:
    """这堆字是一段话，还是一串标签？

    只看一个形状：每 100 字符里有多少个句末标点。实测（2026-10-24，六个真页）：
    中文新闻正文 1.5–2.0、英文索引页 0.3–0.5（HN 0.31、Python 文档 0.49、arXiv 摘要页
    0.30）。1.0 落在两簇中间的空带上。

    两个前置条件各有理由：`min_chars` 让极短的答案不参与这个判断（40 个字的片段里
    一个句号就是 2.5/100，比例在分母很小时没有意义），200 与壳闸的
    `_JS_SHELL_MIN_TEXT_CHARS` 同一条线，也让被 `max_content_chars` 切到 500 字的那一发
    仍然判得出来（切完的片段正是 478 字 —— 拿 500 当下限会让"调用方点了小预算"变成
    "这页塌陷了"）；`sentences_per_100` 取 1.0 而不是 0.5，是因为误判成"这是正文"会让
    调用方不去取那些链接，代价比多读一段带链接的正文更高。
    """
    body = (text or "").strip()
    if len(body) < min_chars:
        return False
    hits = len(_PROSE_SENTENCE_RE.findall(body))
    return hits * 100 >= len(body) * sentences_per_100


def detect_page_type(
    html: str,
    url: str,
    content_type: str = "",
    extracted_text_len: int = 0,
    extracted_text: str = "",
) -> str:
    """按原始 HTML + content-type 分类页面结构。

    保守：没有强信号时返回 "unknown"。错误派生的信号（js_shell / auth_wall）**不**在
    这里检测 —— 它们稍后由 _with_agent_hints 从 result.error 设置，并覆盖这个值。
    """
    ct = (content_type or "").lower()
    if ct.startswith("application/pdf"):
        return "pdf"
    if ct.startswith("application/json") or ct.startswith("text/json"):
        return "json"
    # 数据 XML（sitemap / RSS / Atom / +xml API）由抓取路径原样返回，所以结构判决
    # 必须点名它，而不是在一份满是标签的 body 旁边留一个 "unknown"。xhtml+xml 是
    # HTML 文档，留在外面。
    if (ct.startswith(("application/xml", "text/xml"))
            or (ct.endswith("+xml") and not ct.startswith("application/xhtml"))):
        return "xml"
    if ct.startswith("image/"):
        return "image"
    if not html or not html.strip():
        return "unknown"

    low = html.lower()

    # 跳转：meta refresh 或 JS location 赋值（且真实文本很少）。
    if _META_REFRESH_RE.search(low) or (_JS_REDIRECT_RE.search(low) and extracted_text_len < 500):
        return "redirect"

    # 解析可见文本与精确的结构属性。这里用原始 HTML 预过滤不安全，因为实体与内联
    # 标签会把真实信号切开。
    visible_text, has_active_paywall_attribute = _paywall_evidence(html)
    if any(marker in visible_text.lower() for marker in _PAYWALL_MARKERS):
        return "paywall"
    if has_active_paywall_attribute:
        return "paywall"

    # 17.0 §3 第 28 条删掉了这里原来的三个分支（qa / forum / docs，靠站方软件名和
    # class/id 子串匹配）：那三个值说的是「这是个什么站」，而这句话 source_type 已经在
    # 说（classify_source 回 qa / forum / docs-site），同一个回包里两处说就是两处会互相
    # 矛盾的事实。留下的值只回答结构：正文 / 列表 / 某种媒体 / 一道墙。少了「docs 先行
    # 豁免」这一行之后，Sphinx/mkdocs 手册按形状判 —— 正文页判成 article，目录页判成
    # list，而 list 的 next_action 正是「去取那些链接」。

    # List 页：许多同域内容链接，其**标签**占了大部分文本，且**不是** <article> 页。
    # 这接住索引 / 搜索结果 / 分类 / 归档页 —— 它们的主要价值是继续往前的链接。保守：
    # 一个 <article> 页即使带很多交叉引用也仍是文章（精确优先于召回 —— 把文章标成
    # list 会把 agent 送去错误的爬取）。
    #
    # 这里过去量错过两件事，都来自同一个习惯 —— 拿一个便宜的数字当成"这页有多少散文"：
    #
    #   * `extracted_text_len` 是**抽取器**交回的长度，不是页面持有的长度。Trafilatura
    #     报的是最密的块，所以在长篇多节文档上可能少算 50 倍：RFC 9110 规范页的存档
    #     副本有 446k 字符可见文本，抽取给了 8.4k，而 8.4k / 194 条链接读起来像"一个
    #     链接索引"。判决不能取决于抽取恰好做得多好，所以散文的度量取两者最大值
    #     （可见文本是整份文档，宁可多报也不可少报）。
    #   * 链接**计数**分不出文章与索引 —— 一篇 wiki 文章交叉引用几百个同域页面，仍是
    #     文章。区分属性是文本**是不是**链接标签，所以内容区文本落在已计数链接里的
    #     占比必须可观。真实存档页实测：索引 0.16-0.43、文章/参考 0.01-0.05
    #     （tests/page_fixtures）。
    #   * ……而且**占比**也不够，因为它是一个比例：一篇文章既链接密集、又带一个近乎
    #     纯链接文本的 References 块，就清过 0.10（en.wikipedia.org/wiki/
    #     Hypertext_Transfer_Protocol 跑到 0.12）。绝对形状在比例分不清的地方能分：
    #     没有索引页会有一段不是任何人链接标签的长文字。
    try:
        host = urlparse(url).netloc.lower()
        if ":" in host:
            host = host.split(":", 1)[0]
    except Exception:
        host = ""
    n_links = _count_content_links(html, host) if host else 0
    # `extracted_text_len` 只有在能当散文度量时才是散文度量。原始 HTML 抽取报的是
    # **标记**长度（标签、属性、实体），在链接密集的页面上是文本的好几倍 —— 实测：
    # 同一个 quotes 索引经 markdown 路径给 2179、经 extraction_type="html" 给 11021，
    # 只有第二个清过了 list 闸。从页面抽出的文本不可能超过页面自身可见文本（实体解码
    # 之外），所以明显更高的数字是标记，对散文什么也没说。这与 G16 修过的少报是同一个
    # 错误，只是方向相反：判决不能取决于调用方要的是哪种形状。
    visible_len = len(visible_text)
    if extracted_text_len > visible_len * 1.5:
        extracted_text_len = visible_len
    prose_len = max(extracted_text_len, visible_len)
    # >=20 条内容链接，且文本短或每链接文本量低。
    list_candidate = (n_links >= 20
                      and (prose_len < 1500 or prose_len / n_links < 200))
    # 一次解析给出两个判决要的数：list 闸的 share/longest-run，以及下面文章闸的
    # longest run。只在两个便宜闸之一已经响起时才要它，让小页面（与错误壳）免掉这趟
    # 额外解析；到这里 `_paywall_evidence` 已经解析过文档一次。
    share = longest_run = None
    if host and (list_candidate or prose_len >= ARTICLE_VISIBLE_TEXT_MIN):
        share, longest_run = _list_shape(html, host)

    if list_candidate and share is not None \
            and not _ARTICLE_TAG_RE.search(low):
        if share >= LIST_LABEL_SHARE_MIN and longest_run <= LIST_PROSE_RUN_MAX:
            # 链接密度高不等于"这页的答案是那些链接"。中文门户的正文页常年挂着几十条
            # 同域推荐（实测 163 一条 2,844 字的正文配 228 条同域链接、share 0.38），
            # 而真正的索引页靠 `share` 反而过不了这道闸 —— 判据量的是"同域链接占了多少
            # 字"，不是"这页要人点走"。所以最后再问一次答案自己的形状：抽取器交回的
            # 是一段带句号的话时，这页就有正文，`list` 那句话会把调用方送去爬一堆它
            # 已经读到的页面。
            if answer_is_prose(extracted_text):
                return "article"
            if _tech_doc_shape(html):
                # 技术文档（2.3）：走到这一步的页面 share 与 run 都在说"这是索引"，
                # 而 `<main>` 里 ≥8 段落、全文 ≥5 颗代码标记说的是"这是一篇被代码块
                # 切碎的文章"——两把尺对它都失真（MDN 实测 run 196 / p 20 / code 38），
                # 信后一组。
                return "article"
            return "list"

    # 文章：<article> 标签是强文章信号。
    if _ARTICLE_TAG_RE.search(low):
        return "article"
    # ……而且标签不是唯一的依据。不带 <article> 元素的长文页，在 agent 最常抓的
    # 站点上是常态：Wikipedia 的皮肤把正文放进 div、标准页放进 <pre> 味的流、大多数
    # 自托管文档放进裸 <div> —— 所以这些从前都返回 "unknown"，而 agent 把它读成
    # "没有任何东西分类了这一页"，可页面其实答上了。正面信号就是 list 闸拒绝的同一
    # 条非链接串：一段长到足以成为散文段落的文字，且页面上这样的文字足够多。实测：
    # GNU 自由软件定义 756 / 25k、Wikipedia HTTP 文章 705+ / 22k、RFC 9110 807 /
    # 53k —— 对比 HN 79、一个 quotes 列表 148、FSF 分类索引 279、Python genindex 227。
    if (longest_run is not None
            and longest_run >= ARTICLE_PROSE_RUN_MIN
            and prose_len >= ARTICLE_VISIBLE_TEXT_MIN):
        return "article"

    return "unknown"


def page_type_from_error(error: str) -> str:
    """把定稿的错误串映射成 page_type 覆盖（定论信号）。

    错误不蕴含 page_type 时返回 ""，让 detect_page_type 的结构 page_type 站住。
    """
    if not error:
        return ""
    e = error.lower()
    if e.startswith(("js_shell_detected", "obfuscated_payload")):
        return "js_shell"
    # auth_wall_detected 来自对抽取内容的 _is_auth_wall；auth_required/not_a_pdf+auth
    # 来自 PDF 管线。两者本该都设置 page_type="auth_wall"，却只有第二个做了，于是一道
    # 登录墙报出 error="auth_wall_detected" 而 page_type="unknown"，agent 便一直引用
    # 登录表单。
    if e.startswith(("auth_required", "auth_wall_detected")) or (e.startswith("not_a_pdf") and "auth" in e):
        return "auth_wall"
    if e.startswith(("bot_challenge_detected", "bot_wall_detected")):
        return "captcha"
    if e.startswith("geo_redirect_detected"):
        return "redirect"
    return ""
