# Contributing

Thanks for looking at Dhole. This is a small, opinionated MCP server; the notes
below are what you need to get a green local run.

## Setup

```bash
git clone https://github.com/ouli-1242/dhole-mcp.git
cd dhole-mcp
python -m pip install -e ".[dev]"
```

Optional extras (browser engine, PDF, OCR, local file parsing) are in `[all]`:

```bash
python -m pip install -e ".[dev,all]"
python -m patchright install chromium   # the anti-bot browser (~193MB); optional —
                                        # dhole installs it on the first browser-tier
                                        # call, and tests never download it
```

`patchright`, not `playwright`: the driver only launches the revision named in its own
`browsers.json`, so a browser installed by upstream playwright's CLI is a different
directory and does not count.

Without `[all]`, dhole degrades to HTTP-only mode instead of failing — that
degradation is a supported configuration and is covered by tests.

## Tests

```bash
pytest                    # default run; e2e tests are deselected, 8 workers
pytest -n 0               # one process, for debugging a single test
pytest -m e2e -n 0        # spawns a real dhole.exe subprocess
pytest tests/test_search.py -q
```

The default run is **parallel** (`-n 8` in `addopts`) because the suite is
7,415 tests whose slowest single case is 16s — there is no hotspot to optimise,
only volume. Measured on a 32-core/16GB box:

| command | wall clock |
|---|---|
| `pytest -n 0` | 617s |
| `pytest` (default, `-n 8`) | 131s |
| `pytest -n 16` | 107s |
| `pytest -n 32` | crashes |

**Never use `-n auto`.** xdist resolves `auto` to `cpu_count`, and every worker
imports its own copy of `trafilatura` + `patchright`; on a many-core box that
exhausts RAM and the workers die, which surfaces as an opaque
`INTERNALERROR> KeyError: <WorkerController gw9>` rather than an OOM message.
Raise `-n` only as far as your RAM allows (~0.4GB per worker).

`--timeout=120` is the other half: without it a deadlocked run sits at 0% CPU
forever with no output. It is 7x the slowest real test, so it can only fire on a
genuine hang — if it does, the failing name is the one to look at, not the clock.

Two things worth knowing before you debug a "test doesn't see my change":

1. **`pythonpath = ["src"]` in `pyproject.toml` is load-bearing.** The dev venv
   often has a *built* wheel installed rather than an editable install. Without
   that setting, pytest silently imports the stale installed copy and your edits
   to `src/` go untested. This has bitten the project more than once.
2. `addopts = ["-m", "not e2e"]` deselects the e2e tests by default. They are
   slower and need a built executable.

## Lint

```bash
ruff check src tests      # config lives in pyproject.toml [tool.ruff]
```

Run `pytest` and `ruff check src tests` locally before pushing — this fork has
no CI, checks are run by hand. The default run makes **no network calls**
(`tests/conftest.py` also redirects every state file to a temp dir, so running
the suite never rewrites a real `~/.dhole`).

## Engine fixtures and parser drift

The keyless engines are scraped HTML, so their parsers are snapshots of
somebody else's markup. `tests/test_engine_parsers.py` locks each parser against
a trimmed **real** SERP capture, and the expected item count comes from an
independent oracle (bs4 + `html.parser`, a different parser and query language
from the lxml/XPath under test) so the test can't just agree with itself.

Fixtures go stale when an engine redesigns. To re-capture or diff them (needs the
network, and for duckduckgo/brave a VPN on a CN connection):

```bash
python -m pytest -m live --engine-fixtures=capture tests/test_engine_fixtures_live.py -rs
python -m pytest -m live --engine-fixtures=check  tests/test_engine_fixtures_live.py -rs
```

Both are deliberately opt-in twice over, so a bare `pytest -m live` cannot
hammer five engines by accident. `check` only fails on a **confirmed** drift
(fixture parses, live page has result containers, live yields nothing usable);
a live SERP that legitimately returns zero results is weather, not a red test,
and is reported + skipped.

Two rules worth keeping: never assert that a fixture is *recent* (that turns the
suite red on a random Tuesday with no code change, and the maintainer learns to
delete tests), and never hand-write a fixture to make a test pass — a fixture
that no engine can produce is a gap to report, not to paper over.

## Layout

```
src/dhole_mcp/
  server.py              MCP tool schemas + dispatch + orchestration
  models.py              17 颗工具的响应形状（字段描述就是调用方读的那份说明书）
  wire.py                线格式压实 —— "字段缺席 = 默认值"这份契约的实现
  params.py              入参面 —— `_coerce_*` 把客户端发成字符串的数字读回类型，
                         `_validate_tool_args` 决定这颗工具到底收不收这个名字
  quality.py             内容质量判定与 agent 提示 —— 墙/空壳/该活多久/下一步
  context.py             按次调用的 ContextVar（pages / focus / session_id / auth …）
  rungs.py               反爬档位表与读它的那几处查表
  audit.py               `DHOLE_USAGE_LOG`：每次调用的收据与只往这里发的安全事件
  ops.py                 `dhole` 运维面（子命令、`--doctor`、技能安装、进程入口）
  fetcher.py             primp-based HTTP session and Response
  browser.py             patchright stealth browser
  search*.py             keyless metasearch backends, proxy pool, reranker
  crawl.py, sitemap.py   same-domain crawling
  pdf_extractor.py, ocr.py, parse.py, structured.py, metadata.py
  extractor.py, trafilatura_extractor.py    HTML -> markdown paths
  security.py            URL validation, SSRF checks, credential redaction
  envelope.py, errors.py, cache.py          cross-cutting helpers
tests/                   unit + integration tests (no live network by default)
```

上面那八块（models … ops）是 17.0 B6 从 server.py 里搬出来的，搬的理由各不相同，
但有一条共同的约束：**它们不许在模块级 import server**。唯一的例外是
`params._arg_defaults` 里那句函数内的 `from dhole_mcp.server import MasterFetchServer`
—— 它要的方法默认值必须从签名现读（抄一份会漂），而模块级读就成了循环。
新加一层时先问"它需要 server 吗"，需要就说明切法错了。

`tests/tool_payload_measure.py` 用 ast 从源码读 connect 面，且不 import 包；
常量搬到哪一块它就得跟着读到哪一块（`_package_trees()` 干的就是这件事）。
搬完它的表现是**拒绝出数**，不是出一个偏小的数。

## Conventions

- Commit messages follow `type(scope): summary` — e.g. `fix(search): ...`,
  `feat(server): ...`, `docs: ...`, `chore: ...`.
- Never commit credentials. API keys are read from environment variables only;
  see the note in `src/dhole_mcp/search_metasearch.py`. Error text and logs must
  go through `security.redact_api_key()` before being surfaced.
- Every behaviour change ships with a test that fails without it.
- Keep tool definitions token-lean — the tool descriptions are part of the
  prompt budget for every agent session.

## Comments and docstrings

Comments are part of the maintenance contract: they record decisions the code
cannot show, and they are reviewed like code.

- **Language.** Comments and docstrings are written in Chinese. English is
  reserved for language keywords, identifiers, code literals, external quotes,
  tool directives (`# noqa`, `# type:`), and short structural labels. Public
  docstrings use Chinese section labels (`参数:`, `返回:`, `异常:`). One comment is
  written in one language; do not mix languages inside one sentence.
- **Why, not what.** State the constraint, the failure that forced the choice,
  or the rule the next editor must preserve. A comment that only restates the
  next line is deleted. Test names and concrete observations are useful
  citations; a bare "handle edge case" is not.
- **Public APIs.** Every public module, class, function, and tool handler has a
  Chinese docstring: a one-line summary, then `参数:`, `返回:`, and `异常:`
  sections where those exist. Name boundary behaviour the caller can observe
  (`None`/empty input, clamps, truncation, what is not raised). Private helpers
  get a one-line purpose when the name is not enough. The agent-facing tool
  contract stays in `server._TOOL_DEFS`; docstrings never substitute for it.
- **Magic values, business rules, workarounds.** Give the constant a name and
  document the reason and the bound. Temporary code is marked
  `WORKAROUND(owner): reason; remove when <condition>` so it cannot become
  permanent by accident.
- **Follow-up markers.** `TODO(owner): next action` and
  `FIXME(owner): next action` — an owner and an actionable next step are
  mandatory. Anonymous markers are rejected, as are markers that history
  (CHANGELOG, issue numbers, review rounds) already resolved.
- **Never commit** commented-out code, sensitive values, stale comments, or
  comments that narrate the diff ("added for X", "removed Y"). Update a comment
  in the same change that makes it wrong. Test files follow the same rules; a
  test's docstring states the bug or contract it pins, not the implementation
  steps.

## Reporting bugs

Include the `dhole -v` output, the exact tool call, and whether the browser
extras were installed. `DHOLE_DEBUG=1` (or `-v`) increases log verbosity.
