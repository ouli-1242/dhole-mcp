"""Smart proxy pool for search engine rotation.

Rotates search engine requests across multiple proxies so no single IP
gets rate-limited. Round-robin per search call: each search uses one proxy,
the next search uses the next proxy, cycling through the pool. Unhealthy
proxies (connection errors) are cooled for 60s and skipped.

Config sources (merged, deduped):
  1. CLI: ``dhole proxy add/list/remove/clear``
  2. Config file: ``~/.dhole/search_proxies.json``
     ``{"proxies": ["http://user:pass@ip:port", ...]}``
  3. Env var: ``DHOLE_SEARCH_PROXY`` (comma-separated for multiple), with
     HTTPS_PROXY / HTTP_PROXY / ALL_PROXY as single-proxy fallbacks.

A single proxy string in the env var is fully backward-compatible (pool of 1).
Max 20 proxies. State is in-memory only (resets on restart).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from dhole_mcp import paths

logger = logging.getLogger(__name__)

MAX_PROXIES = 20
_VALID_SCHEMES = ("http", "https", "socks5", "socks5h")


def _config_path() -> Path:
    """Return the path to ~/.dhole/search_proxies.json."""
    return paths.file("search_proxies.json")


def _validate_proxy(proxy: str) -> str | None:
    """Validate scheme + return stripped proxy, or None if invalid."""
    if not isinstance(proxy, str):
        return None
    p = proxy.strip()
    if not p:
        return None
    scheme = urlparse(p).scheme.lower()
    if scheme not in _VALID_SCHEMES:
        logger.warning("Skipping proxy with unsupported scheme '%s' (expected %s)",
                        scheme or "(none)", ", ".join(_VALID_SCHEMES))
        return None
    return p


def _read_config_file() -> list[str]:
    """Read proxies from the config file. Returns [] if missing or malformed."""
    path = _config_path()
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return []
        raw = data.get("proxies", [])
        if not isinstance(raw, list):
            return []
        return [p for p in (_validate_proxy(x) for x in raw) if p]
    except (json.JSONDecodeError, OSError) as ex:
        logger.warning("Failed to read search_proxies.json: %r", ex)
        return []


def _read_env_var() -> list[str]:
    """Read proxies from DHOLE_SEARCH_PROXY (comma-separated), with the
    standard HTTPS_PROXY / HTTP_PROXY / ALL_PROXY vars as single-proxy
    fallbacks when DHOLE_SEARCH_PROXY is unset.

    On Windows ``os.environ`` is case-insensitive, so a lowercase
    ``https_proxy`` (set by many sandboxes/CI runners) is picked up here too -
    that is intentional, but it is why a stray sandbox proxy can end up in the
    pool. ``_env_proxy_source`` names the variable that actually won.
    """
    raw = (
        os.environ.get("DHOLE_SEARCH_PROXY", "")
        or os.environ.get("HTTPS_PROXY", "")
        or os.environ.get("HTTP_PROXY", "")
        or os.environ.get("ALL_PROXY", "")
    ).strip()
    if not raw:
        return []
    return [p for p in (_validate_proxy(x) for x in raw.split(",")) if p]


def _env_proxy_source() -> str:
    """Name of the env var the pool is actually reading, or '' when none is set."""
    for name in ("DHOLE_SEARCH_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        if (os.environ.get(name) or "").strip():
            return name
    return ""


def load_proxies() -> list[str]:
    """Load proxies from env var + config file, deduped, order preserved.

    Env var proxies come first (priority), then config file proxies.
    Duplicates are removed (case-sensitive URL match).
    """
    env = _read_env_var()
    file = _read_config_file()
    seen: set[str] = set()
    merged: list[str] = []
    for p in env + file:
        if p not in seen:
            seen.add(p)
            merged.append(p)
    return merged[:MAX_PROXIES]


def save_proxies(proxies: list[str]) -> None:
    """Write proxies to the config file, creating the dhole home dir if needed.

    Invalid entries are dropped silently (``_validate_proxy`` logs a warning) -
    the file is the source of truth, so it must never hold something the loader
    would then refuse to use.

    The file holds proxy credentials in plaintext, so both it and its directory
    are tightened to 0600/0700 after writing (same helpers the other state files
    use). POSIX only in effect; see ``paths.harden_file``.
    """
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    validated = [p for p in (_validate_proxy(x) for x in proxies) if p]
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"proxies": validated}, f, indent=2)
    paths.harden_dir(path.parent)
    paths.harden_file(path)


def add_proxy(proxy: str) -> int:
    """Add a proxy to the config file. Returns total count after adding.

    Raises ValueError if the proxy is invalid, already present, or the pool is
    full. Writes only to the config file - a proxy set via DHOLE_SEARCH_PROXY
    stays env-owned and is not copied here.
    """
    p = _validate_proxy(proxy)
    if not p:
        raise ValueError(
            f"Invalid proxy '{proxy}'. Expected format: http://ip:port, "
            f"https://ip:port, socks5://ip:port (or with user:pass@)."
        )
    existing = _read_config_file()
    if p in existing:
        raise ValueError(f"Proxy already configured: {p}")
    if len(existing) >= MAX_PROXIES:
        raise ValueError(f"Proxy pool is full ({MAX_PROXIES} max). Remove one first.")
    existing.append(p)
    save_proxies(existing)
    return len(existing)


def remove_proxy(index: int) -> str:
    """Remove a proxy by index from the config file. Returns the removed proxy.

    Raises IndexError if the index is out of range.
    """
    existing = _read_config_file()
    if not existing:
        raise IndexError("No proxies configured.")
    if index < 0 or index >= len(existing):
        raise IndexError(f"Index {index} out of range (0-{len(existing) - 1}).")
    removed = existing.pop(index)
    save_proxies(existing)
    return removed


def clear_proxies() -> int:
    """Remove all proxies from the config file. Returns the count removed."""
    existing = _read_config_file()
    if existing:
        save_proxies([])
    return len(existing)


def list_proxies() -> list[str]:
    """List proxies from the config file (not the env var)."""
    return _read_config_file()


def _redact(proxy: str) -> str:
    """Redact credentials in a proxy URL for display."""
    try:
        parsed = urlparse(proxy)
        if parsed.username or parsed.password:
            netloc = f"***:***@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            return f"{parsed.scheme}://{netloc}"
        return proxy
    except Exception:
        return proxy


class ProxyPool:
    """Manages multiple proxies with round-robin rotation + health tracking.

    Round-robin per search call: each call gets the next proxy. If a proxy
    produces connection errors (all engines failed), it's cooled for 60s and
    its consecutive-failure counter increments. Proxies with >= 3 consecutive
    failures are treated as dead and skipped until a probe (``health_check``)
    or a successful call revives them. ``health_check`` actively probes every
    proxy once so a pool with stale dead entries heals by itself; it is only
    kicked when such an entry exists (``needs_probe``).

    State is in-memory only (not persisted). Resets on restart.
    """

    PROXY_COOLDOWN = 60.0
    MAX_CONSECUTIVE_FAILS = 3  # beyond this a proxy is skipped until revived

    def __init__(self, proxies: list[str]) -> None:
        if not proxies:
            raise ValueError("ProxyPool requires at least one proxy")
        self._proxies = list(proxies)
        self._state: dict[str, dict[str, float | str]] = {p: {} for p in self._proxies}
        self._stats: dict[str, dict[str, int]] = {
            p: {"success": 0, "fail": 0, "consecutive_fails": 0} for p in self._proxies
        }
        self._idx = 0  # round-robin pointer

    @property
    def size(self) -> int:
        return len(self._proxies)

    def _is_dead(self, proxy: str) -> bool:
        return self._stats.get(proxy, {}).get("consecutive_fails", 0) >= self.MAX_CONSECUTIVE_FAILS

    def needs_probe(self) -> bool:
        """True when at least one proxy is cooled or counted dead.

        The probe sends a real request through every proxy, and its only effect
        is reviving proxies that failed — so a fully healthy pool has nothing to
        gain from it (KB-3: it used to fire on every first search regardless).
        """
        now = time.time()
        for p in self._proxies:
            until = self._state.get(p, {}).get("cooled_until", 0)
            if isinstance(until, (int, float)) and until > now:
                return True
            if self._is_dead(p):
                return True
        return False

    def get_proxy(self) -> str | None:
        """Return the next available (non-cooled, not-dead) proxy.

        Returns None if all proxies are cooled/dead (caller falls back to
        direct). A dead proxy is skipped only while a live one exists; if every
        proxy is dead, the first one is returned anyway (stale probe results
        shouldn't hard-block a call that might succeed).
        """
        now = time.time()
        candidates = []
        for i in range(len(self._proxies)):
            proxy = self._proxies[(self._idx + i) % len(self._proxies)]
            state = self._state.get(proxy, {})
            until = state.get("cooled_until", 0)
            if isinstance(until, (int, float)) and until < now:
                candidates.append(proxy)
        if not candidates:
            return None
        live = [p for p in candidates if not self._is_dead(p)]
        chosen = live[0] if live else candidates[0]
        self._idx = (self._proxies.index(chosen) + 1) % len(self._proxies)
        return chosen

    def mark_failed(self, proxy: str) -> None:
        """Cool a proxy that produced connection errors and bump its fail stats."""
        self._state.setdefault(proxy, {})["cooled_until"] = time.time() + self.PROXY_COOLDOWN
        stats = self._stats.setdefault(proxy, {"success": 0, "fail": 0, "consecutive_fails": 0})
        stats["fail"] += 1
        stats["consecutive_fails"] += 1

    def mark_success(self, proxy: str) -> None:
        """Clear cooldown for a proxy that returned results and reset its fail streak."""
        self._state.setdefault(proxy, {}).pop("cooled_until", None)
        stats = self._stats.setdefault(proxy, {"success": 0, "fail": 0, "consecutive_fails": 0})
        stats["success"] += 1
        stats["consecutive_fails"] = 0

    async def health_check(self, probe_url: str = "https://example.com", timeout: int = 10) -> dict[str, bool]:
        """Probe every proxy once; healthy ones are revived (fail streak reset).

        Returns {proxy: alive} for diagnostics. Runs concurrently with bounded
        fan-out. A proxy that fails the probe is cooled for PROXY_COOLDOWN but
        NOT marked dead — a single probe failure is not proof of death.
        """
        import asyncio
        from dhole_mcp.fetcher import HTTPSession

        async def _probe(proxy: str) -> tuple[str, bool]:
            try:
                async with HTTPSession(proxy=proxy, stealthy_headers=False, retries=0, timeout=timeout) as session:
                    resp = await session.get(probe_url, follow_redirects="safe")
                return proxy, getattr(resp, "status", 0) < 500
            except Exception:
                return proxy, False

        sem = asyncio.Semaphore(min(5, len(self._proxies)))

        async def _bounded(proxy: str) -> tuple[str, bool]:
            async with sem:
                return await _probe(proxy)

        results = dict(await asyncio.gather(*(_bounded(p) for p in self._proxies)))
        for proxy, alive in results.items():
            if alive:
                self.mark_success(proxy)
            else:
                self._state.setdefault(proxy, {})["cooled_until"] = time.time() + self.PROXY_COOLDOWN
        return results

    def status(self) -> list[dict[str, str | float | bool]]:
        """Return per-proxy status for diagnostics."""
        now = time.time()
        result = []
        for p in self._proxies:
            state = self._state.get(p, {})
            until = state.get("cooled_until", 0)
            cooled = isinstance(until, (int, float)) and until > now
            stats = self._stats.get(p, {})
            result.append({
                "proxy": _redact(p),
                "cooled": cooled,
                "cooled_remaining": max(0, until - now) if cooled else 0,
                "dead": self._is_dead(p),
                "success": stats.get("success", 0),
                "fail": stats.get("fail", 0),
            })
        return result


# ── Module-level singleton ──────────────────────────────────────────

_pool: ProxyPool | None = None


def get_proxy_pool() -> ProxyPool | None:
    """Return the shared ProxyPool, or None if no proxies configured."""
    global _pool
    proxies = load_proxies()
    if not proxies:
        _pool = None
        return None
    if _pool is None or _pool.size != len(proxies) or _pool._proxies != proxies:
        _pool = ProxyPool(proxies)
    return _pool


def get_next_proxy() -> str | None:
    """Get the next proxy for a search call. Returns None if no pool / all cooled."""
    pool = get_proxy_pool()
    if pool is None:
        return None
    return pool.get_proxy()


def reset_pool() -> None:
    """Drop the cached pool so the next access re-reads config + env.

    Needed after a CLI edit: the singleton is keyed on the loaded proxy list, so
    without this a running process would keep serving the pre-edit pool.
    """
    global _pool
    _pool = None


# Fire-and-forget probe: after the first pool creation, kick off a background
# health check so dead proxies are detected without blocking the first search.
# Only when there is something to revive — see ProxyPool.needs_probe().
_health_task: "asyncio.Task | None" = None


def _kick_health_check() -> None:
    """Start the background proxy health probe once per process (no-op after).

    Returns without doing anything when the pool has nothing cooled or dead:
    the probe goes out through every configured proxy, and a pool that is fully
    healthy has nothing to gain from it.
    """
    global _health_task
    if _health_task is not None:
        return
    # 必须先确认有运行中的事件循环，再创建协程：
    # 直接写 asyncio.create_task(pool.health_check()) 时，协程对象先被求值，
    # 若 create_task 抛 RuntimeError（无事件循环），该协程已被创建却被丢弃，
    # 触发 "coroutine 'ProxyPool.health_check' was never awaited" 警告。
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running event loop (called outside async context) — skip; the
        # next search that creates the pool retries.
        return
    pool = get_proxy_pool()
    if pool is None:
        return
    if not pool.needs_probe():
        return

    _health_task = loop.create_task(pool.health_check())

    def _on_done(t: "asyncio.Task") -> None:
        # 任务完成后置回 None，让下一次搜索能重新探测新加入/恢复的代理
        global _health_task
        _health_task = None
        # 取回异常，否则失败的探测会打 "exception was never retrieved"
        if not t.cancelled() and t.exception() is not None:
            logger.debug("proxy health probe failed: %r", t.exception())

    _health_task.add_done_callback(_on_done)