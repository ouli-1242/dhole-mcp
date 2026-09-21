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
playwright install chromium          # required for the anti-bot browser path
```

Without `[all]`, dhole degrades to HTTP-only mode instead of failing — that
degradation is a supported configuration and is covered by tests.

## Tests

```bash
pytest                    # default run; e2e tests are deselected
pytest -m e2e             # spawns a real dhole.exe subprocess
pytest tests/test_search.py -q
```

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
network, and for duckduckgo/brave/yahoo a VPN on a CN connection):

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

## Conventions

- Commit messages follow `type(scope): summary` — e.g. `fix(search): ...`,
  `feat(server): ...`, `docs: ...`, `chore: ...`.
- Never commit credentials. API keys are read from environment variables only;
  see the note in `src/dhole_mcp/search_metasearch.py`. Error text and logs must
  go through `security.redact_api_key()` before being surfaced.
- Every behaviour change ships with a test that fails without it.
- Keep tool definitions token-lean — the tool descriptions are part of the
  prompt budget for every agent session.

## Reporting bugs

Include the `dhole -v` output, the exact tool call, and whether the browser
extras were installed. `DHOLE_DEBUG=1` (or `-v`) increases log verbosity.
