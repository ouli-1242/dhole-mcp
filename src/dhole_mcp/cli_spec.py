"""命令行面的唯一规格：命令、旗标、示例、退出码。

这张表同时驱动四件事：

* 解析  —— `ops._cli_parse` 的旗标名与类型从这里生成；
* 帮助  —— `dhole <cmd> --help`、`dhole help`、`dhole --help`；
* 发现  —— `dhole commands [--json]`、shell 补全；
* 对账  —— `tests/test_cli_spec.py` 拿工具 schema 与这里逐颗比对，漏一颗就红。

它只依赖标准库和 `dhole_mcp.__version__`。`dhole --help` / `dhole completion`
因此不必 import 服务器（那是 0.4–0.6s 的固定开销），也不必为一个补全脚本付它。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


#: 随包发布的 13 颗工具。`dhole call` 与补全用这份名单；测试拿它和
#: `MasterFetchServer._TOOL_DEFS` 对账。
TOOL_NAMES = (
    "web_read",
    "web_search",
    "web_crawl",
    "web_discover",
    "web_research",
    "web_recall",
    "web_request",
    "web_act",
    "doc_parse",
    "feed_read",
    "sys_state",
    "cred_defaults",
    "sys_guide",
)

#: `web_search.sources` 的封闭值集；测试拿它与
#: `search_verticals.SOURCE_KINDS` 对账。`web` 是默认那一池。
SOURCE_CHOICES = ("web", "news", "academic", "qa", "code", "community",
                  "package", "repo")

#: `sys_state.show/clear` 的类别；测试拿它们与 server 的两张表对账。
#: `config` 与 `models` 只读：它们是清单，不是可以擦掉的状态。
STATE_CLASSES = ("cache", "engine_state", "sessions", "cred_defaults",
                 "cdp_hosts", "config", "models", "all")
STATE_SHOW_ONLY = ("config", "models")
STATE_CLEAR_CLASSES = tuple(c for c in STATE_CLASSES
                            if c not in STATE_SHOW_ONLY)

#: 重排器注册表的键；测试拿它与 `reranker.MODELS` 对账。`dhole models fetch`
#: 只下载字节，**不**选模型——选择仍是启动期的 `DHOLE_RERANK_MODEL`。
RERANK_MODELS = ("bge-zh", "mmarco")


@dataclass(frozen=True)
class Flag:
    """一颗命令行旗标。

    ``transform`` 决定绑定阶段怎么把它变成工具参数：

    * ``""``（默认）—— 原样放进 ``args[target or key]``；
    * ``timeout`` —— 秒换毫秒，0/负数拒绝；
    * ``json`` / ``json_object`` / ``json_array`` —— 解析 JSON，后两者校验形状；
    * ``password_file`` / ``body_file`` —— 从文件或 stdin 读文本；
    * ``urls_file`` —— 读一份 URL 清单，交给位置参数合并。
    """

    name: str
    key: str
    kind: str
    help: str
    metavar: str = "VALUE"
    target: str | None = None
    transform: str = ""
    single_only: bool = False
    #: kind 为 str / list 时的封闭值集。解析期就拒掉笔误，理由与 params.py 那张
    #: 表相同：非法值过去会安静地滑进"不相等"那一支，或者付一次往返才被拒。
    choices: tuple[str, ...] = ()

    @property
    def tool_arg(self) -> str:
        return self.target or self.key

    @property
    def takes_value(self) -> bool:
        return self.kind != "bool"

    def display(self) -> str:
        return f"{self.name} {self.metavar}" if self.takes_value else self.name


@dataclass(frozen=True)
class Positional:
    name: str
    help: str
    required: bool = True
    multiple: bool = False
    target: str = ""


@dataclass(frozen=True)
class Command:
    name: str
    tool: str
    summary: str
    usage: str
    positionals: tuple[Positional, ...] = ()
    flags: tuple[Flag, ...] = ()
    examples: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    exit_note: str = ""
    kind: str = "content"
    #: 位置参数怎么变成工具参数：urls / query / one_url / file / none / custom
    positional_mode: str = "none"
    #: positional_mode='urls' 时的 (单 URL 键, 多 URL 键)。两个键不必不同：
    #: web_read 同时收 url 与 urls，feed_read 只收 urls —— 单条也装进列表。
    url_keys: tuple[str, str] = ("url", "urls")
    #: search 的 find_similar 允许空查询词
    query_optional_mode: str = ""
    #: 这些旗标只在单 URL 时有意义（批量读会在门口拒绝）
    #: 互斥的原始 opts 键（request 的 body / body_file）
    mutually_exclusive: tuple[tuple[str, str], ...] = ()
    #: 绑定完仍必须存在的工具参数
    required_args: tuple[str, ...] = ()
    #: 读/feed 支持 --urls-file 与位置参数 -
    urls_file: bool = False
    #: 自定义处理：state / creds / guide
    custom: str = ""
    #: 自定义子命令覆盖了哪些工具参数（creds 的 set/unset/show）
    covered_params: frozenset[str] = frozenset()
    subcommands: tuple[tuple[str, str], ...] = ()


def output_flags(*, ndjson: bool = False, progress: bool = False,
                 arg: bool = False, dry_run: bool = False) -> tuple[Flag, ...]:
    flags = [
        Flag("--json", "json", "bool",
             "print the full receipt as compact JSON on stdout"),
        Flag("--pretty", "pretty", "bool", "indent JSON (with --json)"),
        Flag("--verbose", "verbose", "bool",
             "add summary, next_action and headers to stderr"),
        Flag("--quiet", "quiet", "bool",
             "suppress stderr metadata (errors still print)"),
    ]
    if ndjson:
        flags.append(Flag("--ndjson", "ndjson", "bool",
                          "one JSON object per row (results, pages, evidence, "
                          "claims, state records)"))
    if progress:
        flags.append(Flag("--progress", "progress", "bool",
                          "show elapsed time on stderr while the call runs"))
    if dry_run:
        flags.append(Flag("--dry-run", "dry_run", "bool",
                          "validate and print the tool call this would make, "
                          "then exit 0 (no network)"))
    if arg:
        flags.append(Flag("--arg", "args", "kv",
                          "pass any tool argument by name (NAME=VALUE; JSON when parseable)",
                          metavar="NAME=VALUE"))
    return tuple(flags)


_READ_FLAGS = (
    Flag("--max-chars", "max_content_chars", "int",
         "content cap in chars (tool: max_content_chars)", metavar="N"),
    Flag("--offset", "offset", "int",
         "resume a truncated page at this char offset", metavar="N",
         single_only=True),
    Flag("--timeout", "timeout", "float",
         "whole-call budget in seconds (tool: timeout ms)", metavar="S",
         transform="timeout"),
    Flag("--focus", "focus", "str", "return only blocks matching this query",
         metavar="Q"),
    Flag("--focus-mode", "focus_mode", "str",
         "how --focus narrows the page", metavar="MODE",
         choices=("filter", "best")),
    Flag("--focus-engine", "focus_engine", "str",
         "scoring used by --focus", metavar="ENGINE",
         choices=("bm25", "neural")),
    Flag("--css-selector", "css_selector", "str",
         "narrow extraction to this CSS selector", metavar="CSS"),
    Flag("--extraction-type", "extraction_type", "str",
         "how to read the page back", metavar="TYPE",
         choices=("markdown", "html", "text", "article", "structured", "check")),
    Flag("--force-fetcher", "force_fetcher", "str",
         "pin one fetch tier instead of the ladder", metavar="TIER",
         choices=("http", "stealthy")),
    Flag("--anti-detect", "anti_detect", "str",
         "anti-detect rung (off / low / ...)", metavar="LEVEL"),
    Flag("--cache-ttl", "cache_ttl", "int",
         "cache seconds; 0 forces a fresh fetch", metavar="S"),
    Flag("--include-links", "include_links", "bool",
         "also return citations / navigation / external links"),
    Flag("--include-media", "include_media", "bool",
         "also return up to 20 image URLs"),
    Flag("--max-links", "max_links", "int",
         "per-list cap for --include-links", metavar="N"),
    Flag("--include-headers", "include_headers", "bool",
         "also return response headers (skips cache)", single_only=True),
    Flag("--save-to", "save_to", "str",
         "write the raw bytes to this file instead of text", metavar="FILE",
         single_only=True),
    Flag("--max-save-bytes", "max_save_bytes", "int",
         "ceiling for --save-to (default 256MB, cap 1GB)", metavar="N",
         single_only=True),
    Flag("--session-id", "session_id", "str",
         "carry cookies across calls under this name", metavar="ID",
         single_only=True),
    Flag("--headers", "headers", "json",
         "extra request headers as a JSON object", metavar="JSON",
         transform="json_object"),
    Flag("--schema", "schema", "json",
         "structured extraction schema (JSON object)", metavar="JSON",
         transform="json_object"),
    Flag("--check-login", "check_login", "bool",
         "probe whether the page is logged in", single_only=True),
    Flag("--cdp-url", "cdp_url", "str",
         "attach the user's Chrome over CDP", metavar="URL", single_only=True),
    Flag("--confirm", "confirm", "bool",
         "approve a sensitive action (first CDP attach)", single_only=True),
    Flag("--ignore-robots", "ignore_robots", "bool",
         "skip robots.txt (only with permission)"),
    Flag("--pages", "pages", "str",
         "PDF pages: '1-5' or '1,3,5-7'", metavar="RANGE", single_only=True),
    Flag("--password-file", "password_file", "str",
         "read a PDF password from FILE (or - for stdin)", metavar="FILE",
         target="password", transform="password_file", single_only=True),
    Flag("--nested-selector", "nested_selector", "str",
         "two layers: CSS selector for elements to follow", metavar="CSS"),
    Flag("--nested-fields", "nested_fields", "json",
         "what to read on each second-layer page (JSON)", metavar="JSON",
         transform="json"),
    Flag("--nested-max", "nested_max", "int",
         "how many second-layer pages to follow", metavar="N"),
    Flag("--urls-file", "urls_file", "str",
         "read URLs from FILE (one per line; - for stdin)", metavar="FILE",
         transform="urls_file"),
)

_SEARCH_FLAGS = (
    Flag("--max-results", "max_results", "int",
         "number of ranked results to return", metavar="N"),
    Flag("--site", "site", "str", "restrict to one domain", metavar="DOMAIN"),
    Flag("--exclude-sites", "exclude_sites", "list",
         "domains to exclude (comma or repeat)", metavar="DOMAIN[,DOMAIN]"),
    Flag("--engines", "engines", "list",
         "engines to ask (comma or repeat)", metavar="NAME[,NAME]"),
    Flag("--sources", "sources", "list",
         "vertical sources; package/repo take exact names, academic = arXiv + Crossref",
         metavar="NAME[,NAME]", choices=SOURCE_CHOICES),
    Flag("--sort", "sort", "str", "engine sort order", metavar="ORDER",
         choices=("relevance", "newest")),
    Flag("--mode", "mode", "str", "search mode", metavar="MODE",
         choices=("auto", "neural", "find_similar")),
    Flag("--similar-to", "similar_to", "str",
         "URL for --mode find_similar (tool: url)", metavar="URL", target="url"),
    Flag("--page", "page", "int", "result page number", metavar="N"),
    Flag("--freshness", "freshness", "str",
         "preset time window", metavar="WINDOW",
         choices=("day", "week", "month", "year")),
    Flag("--after", "after", "str",
         "only results published after this date", metavar="DATE"),
    Flag("--before", "before", "str",
         "only results published before this date", metavar="DATE"),
    Flag("--language", "language", "str", "result language code", metavar="LANG"),
    Flag("--region", "region", "str", "result region code", metavar="REGION"),
    Flag("--location", "location", "str", "geo location hint", metavar="PLACE"),
    Flag("--fetch-content", "fetch_content", "bool",
         "fetch and include the top result pages"),
    Flag("--fetch-schema", "fetch_schema", "json",
         "structured extraction for fetched pages (JSON object)", metavar="JSON",
         transform="json_object"),
    Flag("--min-relevance", "min_relevance", "float",
         "0-1 cutoff on the normalized rerank score", metavar="F"),
    Flag("--min-raw-relevance", "min_raw_relevance", "float",
         "0-1 floor on the raw reranker score", metavar="F"),
    Flag("--fetch-min-consensus", "fetch_min_consensus", "int",
         "keep fetched pages corroborated by N families", metavar="N"),
    Flag("--cache-ttl", "cache_ttl", "int",
         "cache seconds; 0 forces a fresh search", metavar="S"),
)

_CRAWL_FLAGS = (
    Flag("--max-pages", "max_pages", "int", "page cap for this crawl", metavar="N"),
    Flag("--max-depth", "max_depth", "int", "link depth cap", metavar="N"),
    Flag("--timeout", "timeout", "float",
         "whole-call budget in seconds", metavar="S", transform="timeout"),
    Flag("--deadline-ms", "deadline_ms", "int",
         "absolute crawl deadline in milliseconds", metavar="MS"),
    Flag("--path-include", "path_include", "list",
         "only URLs whose path matches (comma or repeat)", metavar="PATTERN[,PATTERN]"),
    Flag("--path-exclude", "path_exclude", "list",
         "URLs whose path matches are skipped", metavar="PATTERN[,PATTERN]"),
    Flag("--max-content-chars-per", "max_content_chars_per", "int",
         "per-page content cap", metavar="N"),
    Flag("--max-total-chars", "max_total_chars", "int",
         "total content cap across pages", metavar="N"),
    Flag("--concurrency", "concurrency", "int",
         "parallel page fetches", metavar="N"),
    Flag("--sitemap", "sitemap", "bool",
         "seed the crawl from sitemap.xml (--arg sitemap=auto for three-state)"),
    Flag("--search", "search", "str",
         "site search query used to seed the crawl", metavar="QUERY"),
    Flag("--ignore-robots", "ignore_robots", "bool",
         "skip robots.txt (only with permission)"),
    Flag("--resume-token", "resume_token", "str",
         "continue a truncated crawl from this token", metavar="TOKEN"),
    Flag("--focus", "focus", "str",
         "return only blocks matching this query", metavar="Q"),
    Flag("--focus-engine", "focus_engine", "str",
         "scoring used by --focus", metavar="ENGINE",
         choices=("bm25", "neural")),
    Flag("--cache-ttl", "cache_ttl", "int", "cache seconds", metavar="S"),
    Flag("--force-fetcher", "force_fetcher", "str",
         "pin one fetch tier instead of the ladder", metavar="TIER",
         choices=("http", "stealthy")),
    Flag("--selector", "selector", "str",
         "CSS selector for the crawl plan projection", metavar="CSS"),
    Flag("--schema", "schema", "json",
         "structured extraction schema for every page (JSON)", metavar="JSON",
         transform="json"),
)

_DISCOVER_FLAGS = (
    Flag("--sitemap", "sitemap", "bool",
         "map from sitemap.xml (--arg sitemap=auto for three-state)"),
    Flag("--max-pages", "max_pages", "int", "URL cap for this map", metavar="N"),
    Flag("--search", "search", "str",
         "site search query used to seed the map", metavar="QUERY"),
    Flag("--path-include", "path_include", "list",
         "only URLs whose path matches", metavar="PATTERN[,PATTERN]"),
    Flag("--path-exclude", "path_exclude", "list",
         "URLs whose path matches are skipped", metavar="PATTERN[,PATTERN]"),
    Flag("--timeout", "timeout", "float",
         "whole-call budget in seconds", metavar="S", transform="timeout"),
    Flag("--resume-token", "resume_token", "str",
         "continue a truncated map from this token", metavar="TOKEN"),
    Flag("--max-total-chars", "max_total_chars", "int",
         "total content cap used while mapping", metavar="N"),
)

_REQUEST_FLAGS = (
    Flag("--method", "method", "str",
         "POST, PUT, PATCH, DELETE or HEAD (required)", metavar="METHOD"),
    Flag("--body", "body", "str", "literal request body", metavar="TEXT"),
    Flag("--body-file", "body_file", "str",
         "read the request body from FILE (or - for stdin)", metavar="FILE",
         target="body", transform="body_file"),
    Flag("--content-type", "content_type", "str",
         "Content-Type header for the body", metavar="TYPE"),
    Flag("--extraction-type", "extraction_type", "str",
         "how to read the page back", metavar="TYPE",
         choices=("markdown", "html", "text", "article", "structured")),
    Flag("--timeout", "timeout", "float",
         "whole-call budget in seconds", metavar="S", transform="timeout"),
    Flag("--headers", "headers", "json",
         "extra request headers as a JSON object", metavar="JSON",
         transform="json_object"),
    Flag("--confirm", "confirm", "bool",
         "approve this write (GET is refused; HEAD needs none)"),
    Flag("--include-headers", "include_headers", "bool",
         "also return response headers"),
    Flag("--session-id", "session_id", "str",
         "carry cookies across calls under this name", metavar="ID"),
)

_ACT_FLAGS = (
    Flag("--actions", "actions", "str",
         "JSON array of actions (or @file / - for stdin)", metavar="JSON",
         transform="json_array"),
    Flag("--extraction-type", "extraction_type", "str",
         "how to read the page back", metavar="TYPE",
         choices=("markdown", "html", "text", "article", "structured")),
    Flag("--max-chars", "max_content_chars", "int",
         "content cap after the chain", metavar="N"),
    Flag("--focus", "focus", "str",
         "return only blocks matching this query", metavar="Q"),
    Flag("--timeout", "timeout", "float",
         "whole-call budget in seconds", metavar="S", transform="timeout"),
    Flag("--anti-detect", "anti_detect", "str",
         "anti-detect rung (off / low / ...)", metavar="LEVEL"),
    Flag("--session-id", "session_id", "str",
         "act inside this named session", metavar="ID"),
    Flag("--cdp-url", "cdp_url", "str",
         "attach the user's Chrome over CDP", metavar="URL"),
    Flag("--confirm", "confirm", "bool",
         "approve the first CDP attach (submit confirms inside the action)"),
    Flag("--check-login", "check_login", "bool",
         "probe the settled page for login state"),
    Flag("--headed", "headed", "bool",
         "run this chain in a visible window"),
    Flag("--pin", "pin", "bool",
         "keep the page for the next call on this session"),
)

_DOC_FLAGS = (
    Flag("--cwd", "cwd", "str", "base directory for a relative file path", metavar="DIR"),
    Flag("--encoding", "encoding", "str",
         "text encoding override", metavar="NAME"),
    Flag("--offset", "offset", "int",
         "char offset to resume a truncated document", metavar="N"),
    Flag("--pages", "pages", "str",
         "PDF pages: '1-5' or '1,3,5-7'", metavar="RANGE"),
    Flag("--password-file", "password_file", "str",
         "read a PDF password from FILE (or - for stdin)", metavar="FILE",
         target="password", transform="password_file"),
)

_FEED_FLAGS = (
    Flag("--max-items", "max_items", "int",
         "entries per feed (newest first)", metavar="N"),
    Flag("--since", "since", "str",
         "only entries newer than this date (incremental polling)", metavar="DATE"),
    Flag("--timeout", "timeout", "float",
         "whole-call budget in seconds", metavar="S", transform="timeout"),
    Flag("--urls-file", "urls_file", "str",
         "read feed URLs from FILE (one per line; - for stdin)", metavar="FILE",
         transform="urls_file"),
)


CONTENT_COMMANDS: dict[str, Command] = {
    "read": Command(
        name="read", tool="web_read",
        summary="fetch a page (or a batch of URLs) as markdown on stdout",
        usage="dhole read <url> [url2 ...] [options]",
        positionals=(Positional("<url> ...",
                                "one URL, or several for a parallel batch",
                                required=True, multiple=True, target="urls"),),
        flags=_READ_FLAGS + output_flags(ndjson=True, progress=True, arg=True, dry_run=True),
        examples=(
            "dhole read https://example.com",
            "dhole read https://a.test https://b.test --max-chars 8000",
            "dhole read https://example.com --json | jq .content_ok",
        ),
        notes=(
            "stdout: page text; a batch is one '## url' section per page.",
            "stderr: host, status, tier, chars, duration, next offset.",
        ),
        positional_mode="urls",
        urls_file=True,
    ),
    "search": Command(
        name="search", tool="web_search",
        summary="keyword search; one ranked row per result on stdout",
        usage="dhole search <query> [options]",
        positionals=(Positional("<query> ...",
                                "query words; may be omitted for --mode find_similar",
                                required=False, multiple=True, target="query"),),
        flags=_SEARCH_FLAGS + output_flags(ndjson=True, progress=True, arg=True, dry_run=True),
        examples=(
            "dhole search python asyncio timeout --max-results 5",
            "dhole search --mode find_similar --similar-to https://example.com/post",
            "dhole search webpack --sources package",
            "dhole search torvalds/linux --sources repo --json",
            "dhole search 注意力机制 --fetch-content --json",
        ),
        notes=(
            "stdout: 1. title / url / snippet per row.",
            "stderr: result count, engines or vertical sources, rerank mode, duration.",
            "--sources package/repo take exact names; every backend's blocked/empty"
            " reason is named on stderr.",
        ),
        positional_mode="query",
        query_optional_mode="find_similar",
    ),
    "crawl": Command(
        name="crawl", tool="web_crawl",
        summary="crawl one site; one '## url' section per page on stdout",
        usage="dhole crawl <url> [options]",
        positionals=(Positional("<url>", "the single start URL", target="url"),),
        flags=_CRAWL_FLAGS + output_flags(ndjson=True, progress=True, arg=True, dry_run=True),
        examples=(
            "dhole crawl https://example.com --max-pages 20",
            "dhole crawl https://example.com --path-include '/docs/' --json",
        ),
        notes=(
            "stdout: one section per usable page.",
            "stderr: page/skip counts, truncation, --resume-token.",
        ),
        positional_mode="one_url",
    ),
    "discover": Command(
        name="discover", tool="web_discover",
        summary="map a site; stdout is one URL per line",
        usage="dhole discover <url> [options]",
        positionals=(Positional("<url>", "the single start URL", target="url"),),
        flags=_DISCOVER_FLAGS + output_flags(ndjson=True, progress=True, arg=True, dry_run=True),
        examples=(
            "dhole discover https://example.com",
            "dhole discover https://example.com --sitemap --max-pages 500",
        ),
        notes=(
            "stdout: one URL per line (directly pipeable into read/feed).",
            "stderr: source (sitemap/crawl), truncation, --resume-token.",
        ),
        positional_mode="one_url",
    ),
    "research": Command(
        name="research", tool="web_research",
        summary="one call, a cited evidence pack on stdout",
        usage="dhole research <query> [options]",
        positionals=(Positional("<query> ...", "research question", target="query"),),
        flags=(
            Flag("--max-sources", "max_sources", "int",
                 "how many sources to read", metavar="N"),
            Flag("--per-source-chars", "per_source_chars", "int",
                 "content cap per source", metavar="N"),
            Flag("--timeout", "timeout", "float",
                 "whole-call budget in seconds", metavar="S", transform="timeout"),
            Flag("--cross-check", "cross_check", "bool",
                 "add a source-agreement report (claims, verdicts, agreement_score)"),
        ) + output_flags(ndjson=True, progress=True, arg=True, dry_run=True),
        examples=(
            "dhole research 'python asyncio cancellation' --max-sources 4",
            "dhole research 'What causes inflation?' --cross-check",
            "dhole research '注意力机制' --json",
        ),
        notes=("stdout: cited evidence, one section per source.",
               "stderr: source/failure counts, claims, agreement_map and warnings.",
               "--cross-check is a source-agreement signal, not a fact-check."),
        positional_mode="query",
    ),
    "recall": Command(
        name="recall", tool="web_recall",
        summary="search pages this machine already read (local cache only)",
        usage="dhole recall <query> [options]",
        positionals=(Positional("<query> ...", "words to look for", target="query"),),
        flags=(
            Flag("--max-results", "max_results", "int",
                 "how many hits to return (tool: limit)", metavar="N", target="limit"),
        ) + output_flags(ndjson=True, arg=True, dry_run=True),
        examples=(
            "dhole recall 'asyncio timeout'",
            "dhole recall 注意力机制 --max-results 5 --json",
        ),
        notes=("stdout: URL + snippet per hit.", "stderr: recall mode and hit count."),
        positional_mode="query",
    ),
    "request": Command(
        name="request", tool="web_request",
        summary="send one non-GET request (writes need --confirm)",
        usage="dhole request <url> --method METHOD [options]",
        positionals=(Positional("<url>", "the single URL", target="url"),),
        flags=_REQUEST_FLAGS + output_flags(progress=True, arg=True, dry_run=True),
        examples=(
            "dhole request https://api.test/x --method HEAD",
            "dhole request https://api.test/x --method POST --body-file - --confirm",
        ),
        notes=(
            "A write is sent once: not retried, cached or escalated.",
            "stdout: answer body; stderr: status line and headers.",
        ),
        positional_mode="one_url",
        mutually_exclusive=(("body", "body_file"),),
        required_args=("method",),
    ),
    "act": Command(
        name="act", tool="web_act",
        summary="run an actions JSON chain, then read the page back",
        usage="dhole act <url> --actions JSON|@file|- [options]",
        positionals=(Positional("<url>", "the page to act on", target="url"),),
        flags=_ACT_FLAGS + output_flags(progress=True, arg=True, dry_run=True),
        examples=(
            "dhole act https://example.com --actions '[{\"snapshot\": true}]'",
            "dhole act https://example.com --actions @chain.json --confirm",
        ),
        notes=(
            "Each action leaves a receipt on stderr; a failed step exits 1.",
            "submit pauses without {\"submit\": {\"confirm\": true}}.",
        ),
        positional_mode="one_url",
        required_args=("actions",),
    ),
    "doc": Command(
        name="doc", tool="doc_parse",
        summary="parse a local file as markdown on stdout (no network)",
        usage="dhole doc <file> [options]",
        positionals=(Positional("<file>", "local file (PDF, DOCX, XLSX, HTML, ...)",
                                target="file_path"),),
        flags=_DOC_FLAGS + output_flags(arg=True, dry_run=True),
        examples=(
            "dhole doc ./report.pdf --pages 1-5",
            "dhole doc ./scan.pdf --password-file - < password.txt",
        ),
        notes=("stdout: markdown.", "stderr: file, format, chars, duration.",
               "An image-only PDF (OCR reads nothing) exits 1 with pdf_no_text;"
               " re-sending the same file cannot change that."),
        positional_mode="file",
    ),
    "feed": Command(
        name="feed", tool="feed_read",
        summary="read RSS/Atom feeds; one section per source on stdout",
        usage="dhole feed <url> [url2 ...] [options]",
        positionals=(Positional("<url> ...", "one or more RSS/Atom URLs",
                                required=True, multiple=True, target="urls"),),
        flags=_FEED_FLAGS + output_flags(ndjson=True, progress=True, arg=True, dry_run=True),
        examples=(
            "dhole feed https://example.com/feed.xml --max-items 10",
            "dhole feed https://a.test/rss https://b.test/atom --since 2026-10-01",
        ),
        notes=("stdout: '## source_url' + entries, newest first.",
               "stderr: item/error/flagged counts."),
        positional_mode="urls",
        # feed_read 只收 urls（没有单 URL 参数）：一条也装进列表，否则命令面
        # 自己造出一个工具不认识的 `url`，示例必然 2 退出。
        url_keys=("urls", "urls"),
        urls_file=True,
    ),
    "state": Command(
        name="state", tool="sys_state",
        summary="census of local state, or --clear CLASS --confirm",
        usage="dhole state [--show CLASS] [--clear CLASS --confirm] [options]",
        flags=(
            Flag("--show", "show", "list",
                 "classes to census (comma or repeat)", metavar="CLASS[,CLASS]",
                 choices=STATE_CLASSES),
            Flag("--clear", "clear", "list",
                 "classes to erase (needs --confirm)", metavar="CLASS[,CLASS]",
                 choices=STATE_CLEAR_CLASSES),
            Flag("--confirm", "confirm", "bool", "approve --clear"),
        ) + output_flags(),
        examples=(
            "dhole state",
            "dhole state --show cache,engine_state",
            "dhole state --show models",
            "dhole state --clear cache --confirm",
        ),
        notes=("config and models are show-only: they are inventories, not erasable state.",
               "sessions / credentials are process-local unless DHOLE_PERSIST_CREDENTIALS=1.",
               "clearing them needs --confirm and only affects what this process can see."),
        custom="state",
    ),
    "creds": Command(
        name="creds", tool="cred_defaults",
        summary="the redacted identity store (set reads JSON)",
        usage="dhole creds [show] | dhole creds set JSON | dhole creds unset KEY ...",
        flags=output_flags(),
        examples=(
            "dhole creds show",
            "dhole creds set @identity.json",
            "dhole creds set '{\"profile\": \"work\"}'",
            "dhole creds unset proxy auth",
        ),
        notes=("set/unset need DHOLE_PERSIST_CREDENTIALS=1 plus encrypted storage (Windows DPAPI);",
               "without it every CLI call starts with an empty in-memory store, so writes are refused.",
               "profile picks the persistent browser profile; profile='' asks for a one-shot session."),
        custom="creds",
        covered_params=frozenset({"set", "unset", "show"}),
        subcommands=(
            ("show", "print the stored names and redacted values"),
            ("set", "store a JSON object of defaults (literal, @file or -)"),
            ("unset", "remove one or more keys"),
        ),
    ),
    "guide": Command(
        name="guide", tool="sys_guide",
        summary="read dhole's own handbook",
        usage="dhole guide [topic] [section] [options]",
        positionals=(
            Positional("topic", "handbook topic (omit for the topic list)", required=False),
            Positional("section", "section inside that topic", required=False),
        ),
        flags=output_flags(),
        examples=(
            "dhole guide",
            "dhole guide behavior",
            "dhole guide behavior 'Structured extraction'",
        ),
        notes=("stdout: the handbook prose itself.",),
        custom="guide",
        covered_params=frozenset({"topic", "section"}),
    ),
}


# ── 运维与元命令：帮助里只需要用法与一句话，解析仍由各自的命令函数负责 ──

OPS_COMMANDS: dict[str, Command] = {
    "serve": Command(
        name="serve", tool="", kind="ops",
        summary="serve MCP over stdio (no arguments) or streamable HTTP (--http)",
        usage="dhole [--http] [--host HOST] [--port PORT] [--cache-ttl S]",
        examples=("dhole", "dhole --http --host 127.0.0.1 --port 8765"),
        notes=("stdio mode writes nothing to stdout: that channel is the MCP wire.",
               "HTTP mode prints one banner, then uvicorn's own URL line."),
    ),
    "doctor": Command(
        name="doctor", tool="", kind="ops",
        summary="the 12-item install check, with a fix command per failure",
        usage="dhole doctor [--json]",
        examples=("dhole doctor", "dhole doctor --json"),
        notes=("Spawns a server and launches the browser once; the live probe sends one search",
               "and one fetch. Use --json for machine-readable checks."),
    ),
    "engines": Command(
        name="engines", tool="", kind="ops",
        summary="engine cooldown state, live probe, and reset",
        usage="dhole engines list | probe [--all] [--query Q] [engine ...] | reset",
        examples=("dhole engines list", "dhole engines probe --all", "dhole engines reset"),
        notes=("list reads what this machine remembers; probe asks each engine right now.",
               "list/probe/reset accept --json."),
        subcommands=(("list", "cooldowns, yield history and backoff"),
                     ("probe", "ask every engine right now (real traffic)"),
                     ("reset", "clear the cooldown and yield files")),
    ),
    "models": Command(
        name="models", tool="", kind="ops",
        summary="download a registered reranker model in full (resumable)",
        usage="dhole models fetch [--model NAME] [--json]",
        flags=(
            Flag("--model", "model", "str",
                 "registered model to fetch (default: DHOLE_RERANK_MODEL or bge-zh)",
                 metavar="NAME", choices=RERANK_MODELS),
        ) + output_flags(),
        examples=("dhole models fetch", "dhole models fetch --model mmarco --json"),
        notes=("Blocks until every required file is on disk and verified; a cut "
               "download keeps its .part and the next run resumes.",
               "This only downloads bytes - choosing a model is still "
               "DHOLE_RERANK_MODEL."),
        subcommands=(("fetch", "download every file for one registered reranker model"),),
    ),
    "config": Command(
        name="config", tool="", kind="ops",
        summary="persisted knobs in ~/.dhole/config.json",
        usage="dhole config list | get NAME | set NAME VALUE | unset NAME",
        examples=("dhole config list", "dhole config set DHOLE_DEFAULT_CONTENT_CHARS 8000",
                  "dhole config get DHOLE_DEFAULT_CONTENT_CHARS --json"),
        notes=("env still outranks the file; import-time knobs say so when written.",
               "list/get/set/unset accept --json."),
        subcommands=(("list", "show every persisted value and its shadowing"),
                     ("get", "show one knob"), ("set", "write one knob"),
                     ("unset", "remove one knob from the file")),
    ),
    "watch": Command(
        name="watch", tool="", kind="ops",
        summary="a persistent page-change watchlist",
        usage="dhole watch add URL [--label TEXT] | list | remove URL | run [--only URL] [--notify]",
        examples=("dhole watch add https://example.com --label docs",
                  "dhole watch run --notify"),
        notes=("run exit codes: 0 unchanged, 1 changed, 2 check failed.",
               "watch run reuses web_read extraction_type='check'."),
        subcommands=(("add", "watch one URL"), ("list", "show the watchlist"),
                     ("remove", "stop watching one URL"), ("run", "check every watched page")),
    ),
    "wire": Command(
        name="wire", tool="", kind="ops",
        summary="what the MCP connect face costs, and its lock",
        usage="dhole wire ledger [--all] [--json] | lock [--write --reason TEXT]",
        examples=("dhole wire ledger", "dhole wire ledger --all --json",
                  "dhole wire lock --write --reason 'added nested_fields'"),
        notes=("ledger measures chars per tool/parameter; lock is the only writer of the lock file.",
               "ledger/lock accept --json."),
        subcommands=(("ledger", "chars per tool and parameter, priced against the connect face"),
                     ("lock", "show the lock; --write records a change with a reason")),
    ),
    "skill": Command(
        name="skill", tool="", kind="ops",
        summary="install the bundled agent skill",
        usage="dhole skill status | install [--force] [--target DIR]",
        examples=("dhole skill status", "dhole skill install --target ~/.agents/skills"),
        notes=("The bundled copy is the source of truth; --target points at a host's own root.",
               "status/install accept --json."),
        subcommands=(("status", "compare the installed copy with the bundled one"),
                     ("install", "write the bundled copy into a skill root (--target DIR)")),
    ),
    "version": Command(
        name="version", tool="", kind="ops",
        summary="version + the capability panel",
        usage="dhole version [--json]",
        examples=("dhole -v", "dhole version --json"),
        notes=("Each missing capability degrades silently; this command is where it becomes visible.",),
    ),
}

META_COMMANDS: dict[str, Command] = {
    "help": Command(
        name="help", tool="", kind="meta",
        summary="show this help, or one command's full help",
        usage="dhole help [command]",
        examples=("dhole help", "dhole help read", "dhole read --help"),
    ),
    "commands": Command(
        name="commands", tool="", kind="meta",
        summary="list every command, or print the machine-readable catalog",
        usage="dhole commands [--json] [--pretty]",
        examples=("dhole commands", "dhole commands --json --pretty"),
    ),
    "completion": Command(
        name="completion", tool="", kind="meta",
        summary="print a shell completion script",
        usage="dhole completion bash|zsh|fish|powershell",
        examples=("dhole completion bash >> ~/.bashrc",
                  "dhole completion powershell | Out-String | Invoke-Expression"),
        subcommands=(("bash", "bash / sh completion script"),
                     ("zsh", "zsh completion script"),
                     ("fish", "fish completion script"),
                     ("powershell", "PowerShell completion script")),
    ),
    "call": Command(
        name="call", tool="", kind="meta",
        summary="call any MCP tool by name with loose JSON arguments",
        usage="dhole call TOOL [--arg NAME=VALUE ...] [--args JSON|@file|-] [options]",
        positionals=(Positional("<tool>", "one of dhole tools", target="tool"),),
        flags=(
            Flag("--args", "args_json", "str",
                 "a JSON object of tool arguments (or @file / -)", metavar="JSON",
                 transform="json_object"),
            Flag("--text", "text", "bool",
                 "print the content channel and put metadata on stderr"),
        ) + output_flags(ndjson=True, progress=True, arg=True, dry_run=True),
        examples=("dhole call web_read --arg url=https://example.com --arg max_content_chars=500",
                  "dhole call sys_state --json",
                  "dhole call web_search --args '{\"query\": \"asyncio timeout\"}'"),
        notes=("JSON is the default output because this face has no per-command renderer.",
               "--text prints the content channel and puts metadata on stderr.",
               "Use it when a tool argument has no named flag yet; named flags stay preferred."),
        positional_mode="custom",
    ),
    "tools": Command(
        name="tools", tool="", kind="meta",
        summary="list the 13 MCP tools and their parameters",
        usage="dhole tools [--json] [--pretty]",
        examples=("dhole tools", "dhole tools --json"),
        notes=("Reads the live tool definitions; this is the authority dhole call accepts.",),
    ),
}

ALL_COMMANDS: dict[str, Command] = {
    **CONTENT_COMMANDS, **OPS_COMMANDS, **META_COMMANDS,
}
SUBCOMMANDS: tuple[str, ...] = tuple(ALL_COMMANDS)
SHELLS: tuple[str, ...] = ("bash", "zsh", "fish", "powershell")

#: 可以放在子命令前面的全局旗标。带值的 `--color` 单独处理。
GLOBAL_FLAGS: tuple[str, ...] = (
    "--json", "--ndjson", "--pretty", "--verbose", "--quiet", "--progress",
    "--no-color",
)

EXIT_CODES: tuple[tuple[str, str], ...] = (
    ("0", "success with content"),
    ("1", "fetched but unusable (content_ok=false, error, or a failed action)"),
    ("2", "usage error or tool refusal"),
)


def command(name: str) -> Command | None:
    return ALL_COMMANDS.get(name)


def reorder_globals(argv: list[str]) -> list[str]:
    """把子命令前的全局旗标挪到它后面。

    ``dhole --json read URL`` 与 ``dhole read URL --json`` 是同一件事；GNU 风格
    的用户不该记"全局旗标必须放后面"。只挪已知的全局旗标，其余原样交给 argparse
    （`-u` 之类仍然要被明确拒绝，而不是被这里吞掉）。
    """
    prefix: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in GLOBAL_FLAGS:
            prefix.append(arg)
            i += 1
            continue
        if arg == "--color" and i + 1 < len(argv):
            prefix.extend(argv[i:i + 2])
            i += 2
            continue
        if arg.startswith("--color="):
            prefix.append(arg)
            i += 1
            continue
        break
    if i < len(argv) and argv[i].lower() in ALL_COMMANDS:
        return argv[i:i + 1] + prefix + argv[i + 1:]
    return argv


def parse_spec(name: str) -> dict[str, tuple[str, str]]:
    """`_cli_parse` 要的 {``--flag``: (key, kind)}。"""
    cmd = ALL_COMMANDS[name]
    return {flag.name: (flag.key, flag.kind) for flag in cmd.flags}


def flag_for(name: str, flag_name: str) -> Flag | None:
    cmd = ALL_COMMANDS.get(name)
    if cmd is None:
        return None
    for flag in cmd.flags:
        if flag.name == flag_name:
            return flag
    return None


def usage(name: str) -> str:
    cmd = ALL_COMMANDS[name]
    return cmd.usage


def _flag_help(flag: Flag) -> str:
    """帮助里的一行说明：值集与"只对单 URL 有效"都贴在说明后面。"""
    text = flag.help
    if flag.choices:
        text += " (one of: " + ", ".join(flag.choices) + ")"
    if flag.single_only:
        text += " (single URL only)"
    return text


def _group(flags: Iterable[Flag]) -> list[str]:
    flags = list(flags)
    width = max((len(f.display()) for f in flags), default=0)
    width = min(max(width, 18), 30)
    lines = []
    for flag in flags:
        lines.append(f"  {flag.display():<{width}}  {_flag_help(flag)}")
    return lines


def command_help(name: str) -> str:
    cmd = ALL_COMMANDS[name]
    lines = [f"dhole {cmd.name} - {cmd.summary}", "", "usage:", f"  {cmd.usage}"]
    if cmd.positionals:
        lines.extend(["", "positionals:"])
        width = max(len(p.name) for p in cmd.positionals)
        for pos in cmd.positionals:
            mark = "" if pos.required else "  (optional)"
            lines.append(f"  {pos.name:<{width}}  {pos.help}{mark}")
    if cmd.subcommands:
        lines.extend(["", "subcommands:"])
        width = max(len(s[0]) for s in cmd.subcommands)
        for sub, text in cmd.subcommands:
            lines.append(f"  {sub:<{width}}  {text}")
    if cmd.flags:
        lines.extend(["", "options:"])
        lines.extend(_group(cmd.flags))
    if cmd.examples:
        lines.extend(["", "examples:"])
        lines.extend(f"  {ex}" for ex in cmd.examples)
    if cmd.kind in ("content", "meta") or cmd.exit_note:
        lines.extend(["", "exit codes:"])
        for code, text in EXIT_CODES:
            lines.append(f"  {code}  {text}")
    if cmd.notes:
        lines.extend(["", "notes:"])
        lines.extend(f"  {note}" for note in cmd.notes)
    return "\n".join(lines)


def _top_help_lines() -> list[str]:
    from dhole_mcp import __version__
    from dhole_mcp import cli_ui as ui

    lines = [
        ui.branded(ui.ver(__version__),
                   ui.dim("web research for AI agents · $0 · no keys")),
        "",
        ui.dim("usage:"),
        f"  {ui.cyan('dhole')}                          serve MCP over stdio",
        f"  {ui.cyan('dhole --http')}  [--host H] [--port P]   serve MCP over HTTP",
        f"  {ui.cyan('dhole <command>')} [options]      one-shot CLI",
        f"  {ui.cyan('dhole --doctor')}                 same as `dhole doctor`",
        "",
        ui.dim("content:"),
    ]
    width = max(len(name) for name in CONTENT_COMMANDS)

    def _row(name: str, summary: str) -> str:
        plain = "dhole " + name
        pad = " " * max(1, width + 12 - len(plain))
        return f"  {ui.cyan(plain)}{pad}  {ui.dim(summary)}"

    for name, cmd in CONTENT_COMMANDS.items():
        lines.append(_row(name, cmd.summary))
    lines.extend(["", ui.dim("ops:")])
    for name, cmd in OPS_COMMANDS.items():
        lines.append(_row(name, cmd.summary))
    lines.extend(["", ui.dim("discovery:")])
    for name, cmd in META_COMMANDS.items():
        lines.append(_row(name, cmd.summary))
    lines.extend([
        "",
        ui.dim("global options (before or after the command):"),
        "  --json  --ndjson  --pretty  --verbose  --quiet  --progress",
        "  --no-color  --color=auto|always|never",
        "",
        ui.dim("exit codes: 0 success · 1 fetched but unusable · 2 usage error"),
        ui.dim("docs: ") + ui.cyan("https://github.com/ouli-1242/dhole-mcp"),
        ui.dim("full help: dhole help <command>, or dhole <command> --help"),
    ])
    return lines


def top_help() -> str:
    return "\n".join(_top_help_lines())


def print_help(args: list[str]) -> int:
    """`dhole help [command]`。"""
    import difflib
    import sys

    from dhole_mcp import cli_ui as ui

    ui.utf8_stdio()
    args = [a for a in args if a not in ("--json", "--pretty")]
    if not args:
        print(top_help())
        return 0
    name = args[0].lower()
    if name in ALL_COMMANDS:
        print(command_help(name))
        return 0
    near = difflib.get_close_matches(name, list(ALL_COMMANDS), n=1, cutoff=0.4)
    hint = f"did you mean: dhole help {near[0]}?" if near else "try: dhole commands"
    print(ui.err(f"unknown command: {name} ({hint})"), file=sys.stderr)
    return 2


def catalog() -> dict:
    """`dhole commands --json`：给 agent 与补全脚本的稳定目录。"""
    from dhole_mcp import __version__

    def _cmd(cmd: Command) -> dict:
        return {
            "name": cmd.name,
            "kind": cmd.kind,
            "tool": cmd.tool or None,
            "summary": cmd.summary,
            "usage": cmd.usage,
            "positional_mode": cmd.positional_mode,
            "positionals": [
                {"name": p.name, "help": p.help, "required": p.required,
                 "multiple": p.multiple, "target": p.target or None}
                for p in cmd.positionals
            ],
            "flags": [
                {"name": f.name, "key": f.key, "kind": f.kind,
                 "target": f.tool_arg if f.transform != "urls_file" else None,
                 "metavar": f.metavar if f.takes_value else None,
                 "choices": list(f.choices),
                 "help": f.help, "single_only": f.single_only,
                 "transform": f.transform or None}
                for f in cmd.flags
            ],
            "required_args": list(cmd.required_args),
            "mutually_exclusive": [list(pair) for pair in cmd.mutually_exclusive],
            "subcommands": [{"name": n, "help": h} for n, h in cmd.subcommands],
            "examples": list(cmd.examples),
        }

    return {
        "catalog_version": 2,
        "version": __version__,
        "tool_names": list(TOOL_NAMES),
        "commands": [_cmd(c) for c in CONTENT_COMMANDS.values()],
        "ops": [_cmd(c) for c in OPS_COMMANDS.values()],
        "meta": [_cmd(c) for c in META_COMMANDS.values()],
        "exit_codes": {code: text for code, text in EXIT_CODES},
    }


def print_commands(args: list[str]) -> int:
    """`dhole commands [--json] [--pretty]`。"""
    import json
    import sys

    from dhole_mcp import cli_ui as ui

    ui.utf8_stdio()
    unknown = [a for a in args if a not in ("--json", "--pretty", "-h", "--help")]
    if unknown:
        print(ui.err(f"unknown flag: {unknown[0]}"), file=sys.stderr)
        print(ui.dim("  usage: dhole commands [--json] [--pretty]"), file=sys.stderr)
        return 2
    if "-h" in args or "--help" in args:
        print(command_help("commands"))
        return 0
    pretty = "--pretty" in args
    if "--json" in args:
        print(json.dumps(catalog(), ensure_ascii=False,
                         indent=2 if pretty else None,
                         separators=None if pretty else (",", ":")))
        return 0
    width = max(len(name) for name in ALL_COMMANDS)
    for group, label in ((CONTENT_COMMANDS, "content"),
                         (OPS_COMMANDS, "ops"),
                         (META_COMMANDS, "discovery")):
        print("  " + ui.dim(label + ":"))
        for name, cmd in group.items():
            print(f"    {ui.cmd('dhole ' + name):<{width + 10}}  {ui.dim(cmd.summary)}")
    print("  " + ui.dim("machine-readable: dhole commands --json"))
    return 0


def _flags_words(name: str) -> list[str]:
    cmd = CONTENT_COMMANDS.get(name)
    if cmd is None:
        return []
    return [f.name for f in cmd.flags]


def completion(shell: str) -> str:
    """一份静态补全脚本。只读这张表，不 import 服务器。"""
    commands = list(ALL_COMMANDS)
    command_words = " ".join(commands)
    shells = " ".join(SHELLS)
    tool_words = " ".join(TOOL_NAMES)
    global_words = ("--json --ndjson --pretty --verbose --quiet --progress "
                    "--no-color --color")
    if shell == "bash":
        cases = []
        for name in CONTENT_COMMANDS:
            cases.append(f"        {name}) words=\"{' '.join(_flags_words(name))}\" ;;")
        cases.append(f"        call) words=\"{tool_words} {global_words} --args --arg --text\" ;;")
        cases.append(f"        help) words=\"{command_words}\" ;;")
        cases.append(f"        completion|completions) words=\"{shells}\" ;;")
        cases.append(f"        *) words=\"{global_words}\" ;;")
        return "\n".join([
            "# bash completion for dhole (generated by `dhole completion bash`)",
            "_dhole() {",
            "    local cur words",
            "    COMPREPLY=()",
            "    cur=\"${COMP_WORDS[COMP_CWORD]}\"",
            "    if [[ $COMP_CWORD -eq 1 ]]; then",
            f"        COMPREPLY=( $(compgen -W \"{command_words}\" -- \"$cur\") )",
            "        return 0",
            "    fi",
            "    case \"${COMP_WORDS[1]}\" in",
            *cases,
            "    esac",
            "    COMPREPLY=( $(compgen -W \"$words\" -- \"$cur\") )",
            "    return 0",
            "}",
            "complete -F _dhole dhole",
            "",
        ])
    if shell == "zsh":
        cases = []
        for name in CONTENT_COMMANDS:
            cases.append(f"        {name}) flags=({' '.join(_flags_words(name))}) ;;")
        cases.append(f"        call) flags=({tool_words} {global_words} --args --arg --text) ;;")
        cases.append(f"        help) flags=({command_words}) ;;")
        cases.append(f"        completion|completions) flags=({shells}) ;;")
        cases.append(f"        *) flags=({global_words}) ;;")
        return "\n".join([
            "#compdef dhole",
            "# zsh completion for dhole (generated by `dhole completion zsh`)",
            "_dhole() {",
            "    local -a commands flags",
            f"    commands=({command_words})",
            "    if (( CURRENT == 2 )); then",
            "        _describe 'command' commands",
            "        return",
            "    fi",
            "    case \"$words[2]\" in",
            *cases,
            "    esac",
            "    _describe 'option' flags",
            "}",
            "_dhole \"$@\"",
            "",
        ])
    if shell == "fish":
        lines = [
            "# fish completion for dhole (generated by `dhole completion fish`)",
            "complete -c dhole -f",
        ]
        for name, cmd in ALL_COMMANDS.items():
            desc = cmd.summary.replace("'", "")
            lines.append(f"complete -c dhole -n '__fish_use_subcommand' -a {name} -d '{desc}'")
        for name, cmd in ALL_COMMANDS.items():
            for flag in cmd.flags:
                flag_name = flag.name.lstrip("-")
                req = " -r" if flag.takes_value else ""
                desc = flag.help.replace("'", "")
                lines.append(
                    f"complete -c dhole -n '__fish_seen_subcommand_from {name}' "
                    f"-l {flag_name}{req} -d '{desc}'")
        for word in ("--json", "--pretty", "--verbose", "--quiet", "--progress",
                     "--no-color"):
            lines.append(f"complete -c dhole -l {word.lstrip('-')}")
        lines.append("complete -c dhole -n '__fish_seen_subcommand_from completion' "
                     f"-a '{shells}'")
        lines.append("")
        return "\n".join(lines)
    if shell == "powershell":
        cases = []
        for name in CONTENT_COMMANDS:
            flags = "','".join(_flags_words(name))
            cases.append(f"        '{name}' {{ @('{flags}') }}")
        cases.append(f"        'call' {{ @('{tool_words}','--args','--arg','--text') }}")
        cases.append(f"        'help' {{ @('{command_words}') }}")
        cases.append(f"        'completion' {{ @('{shells}') }}")
        return "\n".join([
            "# PowerShell completion for dhole (generated by `dhole completion powershell`)",
            "Register-ArgumentCompleter -Native -CommandName dhole -ScriptBlock {",
            "    param($wordToComplete, $commandAst, $cursorPosition)",
            "    $tokens = @($commandAst.CommandElements | ForEach-Object { $_.ToString() })",
            f"    $commands = @('{command_words}')",
            "    if ($tokens.Count -le 1) {",
            "        $commands | Where-Object { $_ -like \"$wordToComplete*\" } | ForEach-Object {",
            "            [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)",
            "        }",
            "        return",
            "    }",
            "    $flags = switch ($tokens[1]) {",
            *cases,
            "        default { @() }",
            "    }",
            "    $flags | Where-Object { $_ -like \"$wordToComplete*\" } | ForEach-Object {",
            "        [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterName', $_)",
            "    }",
            "}",
            "",
        ])
    raise ValueError(shell)


def print_completion(args: list[str]) -> int:
    """`dhole completion SHELL`。"""
    import sys

    from dhole_mcp import cli_ui as ui

    ui.utf8_stdio()
    if not args or args[0] in ("-h", "--help"):
        print(command_help("completion"))
        return 0 if args else 2
    shell = args[0].lower()
    if shell not in SHELLS:
        print(ui.err(f"unknown shell: {shell} (try: {', '.join(SHELLS)})"),
              file=sys.stderr)
        return 2
    print(completion(shell))
    return 0
