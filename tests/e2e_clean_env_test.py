"""17.0 B3 · 干净环境三任务 —— "零配置"那句承诺的唯一验证。

现有的 `-m e2e` 测不到这件事：它跑在一台早就装好浏览器和模型的机器上。这一条反过来，
把世界清空再问三个真问题：

* 状态目录是空的临时目录（没有 config.json、没有缓存、没有模型、没有登录态）；
* chromium 不在（`PLAYWRIGHT_BROWSERS_PATH` 指到空目录）；
* **系统 Chrome 被藏起来** —— win32 上 `_detect_chrome_channel()` 只看
  PROGRAMFILES / PROGRAMFILES(X86) / LOCALAPPDATA 拼出来的三个路径，把它们指到空目录，
  这台机器就不会以"用户的 Chrome"混过这次检查；POSIX 上它只问 PATH，同理。
* 除了 DHOLE_HOME 之外没有设任何 `DHOLE_*`（DHOLE_HOME 是隔离手段，不是旋钮）。

然后按 MCP 的线协议做三条真实任务：读一个必须靠浏览器才读得到的页面、跑一次搜索、
读一份 PDF。三条全绿才算 B3 完成 —— 尤其是第一条：它同时是"第一次需要时我会自己装
chromium"这句话在真机上的证据，也是"下载时长不吃调用预算"的证据（安装要慢链路要几十分钟，
页面预算不许被它吃掉）。

跑法（默认那条路会真下载约 193MB 的 chromium；慢链路要几十分钟）：

    python tests/e2e_clean_env_test.py
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

pytestmark = pytest.mark.e2e

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"

#: 必须靠 JS 才渲染得出来的页面 —— HTTP 档在这里只能拿到一个壳。
JS_ONLY_PAGE = "https://quotes.toscrape.com/js/"
#: 公开 PDF 候选：一个极小的经典样例，一个大而稳的标准文档。
PDF_URLS = [
    "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",
    "https://www.rfc-editor.org/rfc/rfc2616.pdf",
]
SEARCH_QUERY = "python asyncio structured concurrency"

#: 见 `_clean_env`：同版本同制品的另一条 CDN，不是 dhole 的旋钮。
MIRROR_HOST = "https://cdn.npmmirror.com/binaries/playwright"

_MAX_EMPTY_READS = 50


#: 只改 `_detect_chrome_channel` 这一个判据：它返回 'chromium'，launch 就只会用
#: 打包版 chromium，而打包版在隔离目录里一开始是不存在的。
_HIDE_CHROME = (
    "import sys as _s; _s.path.insert(0, r'%s');"
    "import dhole_mcp.browser as _b;"
    "_b._chrome_channel_cache = 'chromium';"
    "_b._detect_chrome_channel = lambda: 'chromium';" % SRC
)


def _clean_env(root: pathlib.Path) -> dict:
    """造一个"刚 pip 装完"的世界：状态目录、浏览器目录、`DHOLE_*` 全部隔离。

    `PLAYWRIGHT_DOWNLOAD_HOST` 是第二处也是最后一处替身，它只换下载走哪条 CDN：
    npmmirror 上那两个 zip 的 Content-Length 与 cdn.playwright.dev 完全一致
    （119,099,822 / 192,511,857，实测），制品是同一份。换源的理由也是实测的：
    这台机器直连 cdn.playwright.dev 拿到过 50KB/s ~ 2.4MB/s（同一小时内 48 倍差），
    而慢的那端一次安装要 40 分钟 —— 那是链路事实，不是被验机制的事实。它不是 dhole
    的旋钮（`DHOLE_*` 一个都没设），装的仍然是同一条 `patchright install` 路径。
    传 `DHOLE_ACCEPT_DIRECT_CDN=1` 就不换源，用真实默认 CDN 跑（慢，但那才是端到端）。
    """
    env = dict(os.environ)
    for key in [k for k in env if k.upper().startswith("DHOLE_")]:
        del env[key]
    (root / "home").mkdir(parents=True, exist_ok=True)
    (root / "browsers").mkdir(parents=True, exist_ok=True)
    env["DHOLE_HOME"] = str(root / "home")
    env["PLAYWRIGHT_BROWSERS_PATH"] = str(root / "browsers")
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    if not os.environ.get("DHOLE_ACCEPT_DIRECT_CDN"):
        env["PLAYWRIGHT_DOWNLOAD_HOST"] = MIRROR_HOST
    return env


def _python_args(body: str) -> list[str]:
    r"""子进程的命令行：先藏掉系统 Chrome，再跑 body。

    这是这份挂具**唯一**一处模拟。原因是实测的：这台机器的 Chrome 藏不住 —— 把
    PROGRAMFILES / LOCALAPPDATA 指到空目录，子进程里读回来的还是 `C:\Program Files`
    （LOCALAPPDATA 与自建变量都能覆盖成功，唯独它不行；见 `_detect_chrome_channel`
    只看这三个变量）。而藏不住 Chrome 的"干净环境"根本不是干净环境：`chromium_status()`
    会以 `system-chrome` 报 present，首用安装永远不会触发。所以直接钉住那个判据。

    除这一处之外全是真的：真的 stdio 子进程、真的空状态目录、真的空浏览器目录、真的
    patchright driver 下载、真的网络。
    """
    return [sys.executable, "-c", _HIDE_CHROME + body]


def _preflight(env: dict) -> dict:
    """先证明世界真的是干净的。不证明这一点的用例是在验证自己的假设。"""
    probe = (
        "import json;"
        "from dhole_mcp import browser;"
        "print(json.dumps(browser.chromium_status()))"
    )
    out = subprocess.run(_python_args(probe), env=env, cwd=str(REPO_ROOT),
                         capture_output=True, text=True, timeout=120)
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except Exception:  # noqa: BLE001
        raise AssertionError(
            f"probe failed: {out.stdout[-400:]} / {out.stderr[-400:]}") from None


class Client:
    """最小 stdio MCP 客户端（形状与 tests/e2e_mcp_test.py 一致）。"""

    def __init__(self, env: dict):
        self.proc = subprocess.Popen(
            _python_args("import sys;"
                         "from dhole_mcp.server import main;"
                         "sys.exit(main())"),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, cwd=str(REPO_ROOT))
        self._id = 0
        self._send("initialize", {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "clean-env", "version": "17.0"}})

    def _send(self, method, params=None):
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params:
            msg["params"] = params
        self.proc.stdin.write((json.dumps(msg) + "\n").encode())
        self.proc.stdin.flush()
        return self._read()

    def _read(self):
        empty = 0
        while True:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"server exited ({self.proc.returncode}) mid-call; "
                    f"stderr tail: {_tail(self.proc)}")
            line = self.proc.stdout.readline()
            if not line:
                empty += 1
                if empty > _MAX_EMPTY_READS:
                    raise RuntimeError(f"no data after {empty} reads; {_tail(self.proc)}")
                time.sleep(0.05)
                continue
            text = line.decode(errors="replace").strip()
            if not text:
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                # 服务器只许在 stdout 上讲协议；冒出来的任何一行都会在这里变成证据。
                raise RuntimeError(f"non-JSON stdout line: {text[:200]!r}") from None

    def call(self, name, args):
        return _data(self._send("tools/call", {"name": name, "arguments":args}).get(
            "result", {}))

    def close(self):
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass


def _tail(proc) -> str:
    try:
        return (proc.stderr.read() or b"")[-2000:].decode(errors="replace")
    except Exception:  # noqa: BLE001
        return "(unavailable)"


def _data(result: dict) -> dict:
    if "structuredContent" in result:
        return result["structuredContent"]
    content = result.get("content") or []
    if content:
        try:
            return json.loads(content[0].get("text", ""))
        except Exception:  # noqa: BLE001
            pass
    return {}


def _chars(data: dict) -> int:
    return int(data.get("total_extracted_chars") or 0)


def task_browser_fetch(client: Client, report: dict) -> list[str]:
    """任务 1 · 读一个必须靠浏览器才读得到的页面。这一条同时是一次真的首用安装。

    `timeout` 给到工具允许的上限 120000ms（`validate_timeout` 的上界）：干净机器的第一
    次抓取要付"冷启动一个刚下好的浏览器"这笔钱，30s 默认值测的是页面，而这里要测的是
    首用安装这条路径。安装的时长不计在这里面（B3 的第 2 条规矩），所以两个数是分开的。
    """
    started = time.perf_counter()
    data = client.call("smart_fetch", {
        "url": JS_ONLY_PAGE, "cache_ttl": 0, "timeout": 120000,
        "options": {"force_fetcher": "stealthy"}})
    took = time.perf_counter() - started
    report["fetch_s"] = round(took, 1)
    report["fetcher_used"] = data.get("fetcher_used", "")
    report["fetch_chars"] = _chars(data)
    report["fetch_escalation"] = data.get("escalation_path", "")
    report["fetch_summary"] = str(data.get("summary", ""))[:120]
    failures = []
    if data.get("error"):
        failures.append(f"smart_fetch error: {str(data['error'])[:300]}")
    if data.get("fetcher_used") != "stealthy":
        failures.append(f"fetcher_used={data.get('fetcher_used')!r}，浏览器档没跑起来")
    if _chars(data) < 50:
        failures.append(f"只拿到 {_chars(data)} 字符，页面是壳")
    return failures


def task_search(client: Client, report: dict) -> list[str]:
    """任务 2 · 一次真实搜索（没有任何引擎 key、没有代理）。"""
    started = time.perf_counter()
    data = client.call("smart_search", {
        "query": SEARCH_QUERY, "options": {"max_results": 3, "cache_ttl": 0}})
    report["search_s"] = round(time.perf_counter() - started, 1)
    results = data.get("results") or []
    report["search_results"] = len(results)
    if data.get("error") and not results:
        return [f"smart_search error: {str(data['error'])[:300]}"]
    if not results:
        return [f"搜索返回 0 条（error={str(data.get('error'))[:120]}）"]
    return []


def task_pdf(client: Client, report: dict) -> list[str]:
    """任务 3 · 读一份 PDF。第一个候选失败就换第二个，但结论只看一条：真拿到文字。"""
    for url in PDF_URLS:
        started = time.perf_counter()
        data = client.call("smart_fetch", {"url": url, "cache_ttl": 0})
        took = round(time.perf_counter() - started, 1)
        if _chars(data) >= 100 and not data.get("error"):
            report["pdf_url"] = url
            report["pdf_s"] = took
            report["pdf_chars"] = _chars(data)
            report["pdf_page_type"] = data.get("page_type", "")
            report["pdf_quality"] = data.get("quality_score")
            if data.get("page_type") != "pdf":
                return [f"page_type={data.get('page_type')!r}，没被认成 PDF"]
            return []
        report.setdefault("pdf_attempts", []).append(
            f"{url} -> status={data.get('status')} chars={_chars(data)} "
            f"error={str(data.get('error'))[:120]}")
    return [f"两个 PDF 候选都没读出来：{report.get('pdf_attempts')}"]


def run_acceptance(keep: bool = False) -> tuple[dict, list[str]]:
    root = pathlib.Path(tempfile.mkdtemp(prefix="dhole_clean_"))
    env = _clean_env(root)
    report: dict = {}
    failures: list[str] = []
    client = None
    try:
        status = _preflight(env)
        report["preflight"] = status
        report["download_host"] = ("cdn.playwright.dev (默认)"
                                   if env.get("PLAYWRIGHT_DOWNLOAD_HOST") is None
                                   else env["PLAYWRIGHT_DOWNLOAD_HOST"])
        if status.get("present"):
            return report, [
                "世界没清干净：chromium_status 说二进制已经在 "
                f"({status.get('source')}) —— 这条用例的全部价值就在于它不在"]
        client = Client(env)
        # "第一次**用到**时同步装"里的用到的是抓取，不是启动。证据要趁第一次抓取之前取：
        # 状态文件是安装第一步就写的，启动那侧偷偷装过就一定在这里露出来。
        time.sleep(3.0)
        startup_state = root / "home" / "browser_install.json"
        report["startup_state_file"] = startup_state.exists()
        report["startup_installed"] = sorted(
            p.name for p in (root / "browsers").iterdir() if p.is_dir())
        if startup_state.exists():
            failures.append("服务器一启动就去下 chromium 了 —— 每个干净机器连上宿主都要"
                            "花 193MB，哪怕一次浏览器档都不需要")
        for task in (task_browser_fetch, task_search, task_pdf):
            failures += task(client, report)
        # 装机的证据要落在**隔离目录**里，而不是用户那一份。认标记不认目录名：
        # 两个 artifact 都叫 chromium 打头（headless shell 与 Chrome for Testing），
        # 抓错一个就等于验错了东西。
        complete = sorted(p.name for p in (root / "browsers").iterdir()
                          if p.is_dir() and (p / "INSTALLATION_COMPLETE").exists())
        report["installed_dirs"] = complete
        state_file = root / "home" / "browser_install.json"
        report["install_state"] = (json.loads(state_file.read_text(encoding="utf-8"))
                                   if state_file.exists() else None)
        report["models_after"] = sorted(p.name for p in (root / "home" / "models").glob("*")
                                        ) if (root / "home" / "models").exists() else []
        # dhole 传的是显式 channel（'chrome' 或 'chromium'），而驱动里显式 channel 优先于
        # headless 的 shell 选择 —— 所以这次真起的是完整的 chromium-<rev>（实测见
        # `browser._ARTIFACT_DIRS`）。认错的目录等于验错了东西：shell 在盘上也照样起不来。
        if not any(n.startswith("chromium-") for n in complete):
            failures.append(
                "浏览器档跑通了，但隔离目录里没有带标记的 chromium-* —— "
                "那句「第一次自己装」是假的（它可能用了别处的二进制）")
        landed = (report["install_state"] or {}).get("path", "")
        if landed and not landed.startswith(str(root / "browsers")):
            failures.append(
                f"install_state 说二进制在 {landed}，那不是隔离目录 —— 这次验收没有证明"
                "干净机器能自己装")
    finally:
        if client:
            client.close()
        if not keep:
            shutil.rmtree(root, ignore_errors=True)
    return report, failures


@pytest.mark.skipif(os.environ.get("DHOLE_CLEAN_ENV") != "1",
                    reason="要真下载约 193MB 的浏览器：显式 DHOLE_CLEAN_ENV=1 才跑")
def test_clean_environment_three_tasks():
    report, failures = run_acceptance()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    assert not failures, "\n".join(failures)


def main() -> int:
    report, failures = run_acceptance(keep=True)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS - 干净环境三任务全绿（首用安装真的发生 in 隔离目录）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
