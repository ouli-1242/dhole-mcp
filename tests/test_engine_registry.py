"""Engine-name registry + keyed JSON engines (brightdata/tavily/exa/bocha).

策略：keyed 引擎默认不跑，只有 engines= 显式点名才执行 —— 每次调用都花钱。
"""

import time

import httpx
import pytest

from dhole_mcp.search import _validate_engines


class TestBrightdataSelectable:
    """'brightdata' is a real backend, so it must be selectable by name."""

    def test_brightdata_passes_validation(self):
        assert _validate_engines(["brightdata"]) == ["brightdata"]

    def test_all_keyed_names_pass_validation(self):
        from dhole_mcp.search_metasearch import KEYED_ENGINES

        names = sorted(KEYED_ENGINES)
        assert names == ["bocha", "brightdata", "exa", "tavily"]
        assert _validate_engines(names) == names


class TestBrightdataWithoutKey:
    """Asking for the paid backend with no key must blame the key, not the proxy."""

    @pytest.mark.asyncio
    async def test_message_names_missing_key_not_proxy(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        monkeypatch.delenv("DHOLE_BRIGHTDATA_API_KEY", raising=False)
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)

        with pytest.raises(m.MetaSearchException) as ei:
            await m.metasearch("test query", engines=["brightdata"])

        msg = str(ei.value)
        assert "DHOLE_BRIGHTDATA_API_KEY" in msg
        assert "DHOLE_SEARCH_PROXY" not in msg


class TestBrightdataSelectedWithKey:
    """With a key set, selecting it by name must actually deliver its results."""

    @pytest.mark.asyncio
    async def test_returns_brightdata_results(self, monkeypatch):
        from types import SimpleNamespace

        from dhole_mcp import search_metasearch as m

        monkeypatch.setenv("DHOLE_BRIGHTDATA_API_KEY", "fake-key")
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)

        # 用假的 httpx.post 返回 Bright Data 解析层认识的结构
        import json as _json

        body = _json.dumps({"organic": [
            {"title": "T", "url": "https://example.com/a", "snippet": "B"}
        ]})

        def fake_post(*a, **k):
            return SimpleNamespace(status_code=200, text="", body=body,
                                   json=lambda: {"body": body})

        monkeypatch.setattr(httpx, "post", fake_post)

        results, status = await m.metasearch("test query", 3, engines=["brightdata"])

        assert [r["href"] for r in results] == ["https://example.com/a"]
        assert status.get("brightdata") == "ok"


class TestBrightdataAuthError:
    """A wrong/expired key must not look like 'Google returned nothing'."""

    @pytest.mark.asyncio
    async def test_401_is_distinguishable_from_empty(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        class _Denied:
            status_code = 401
            text = "invalid api key"

        monkeypatch.setenv("DHOLE_BRIGHTDATA_API_KEY", "wrong-key")
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _Denied())

        results, status = await m.metasearch("test query", 3, engines=["brightdata"])

        assert results == []
        assert status["brightdata"] == "error:BrightDataAuthError"

    @pytest.mark.asyncio
    async def test_401_does_not_trip_the_circuit_breaker(self, monkeypatch):
        """A bad key is permanent -- cooling it down for 60s fixes nothing."""
        from dhole_mcp import search_metasearch as m

        class _Denied:
            status_code = 403
            text = "zone not authorized"

        monkeypatch.setenv("DHOLE_BRIGHTDATA_API_KEY", "wrong-key")
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _Denied())

        await m.metasearch("test query", 3, engines=["brightdata"])

        assert m._is_circuit_open("brightdata") is False


class _OkEmpty:
    status_code = 200
    text = "{}"

    def json(self):
        return {}


class TestBrightdataTimeout:
    """The paid call must honour DHOLE_SEARCH_DEADLINE instead of a fixed 20s."""

    @staticmethod
    def _capture(monkeypatch, deadline):
        from dhole_mcp import search_metasearch as m

        seen: dict = {}

        def fake_post(*args, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            return _OkEmpty()

        monkeypatch.setenv("DHOLE_BRIGHTDATA_API_KEY", "fake-key")
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(m, "_SEARCH_DEADLINE", deadline)
        monkeypatch.setattr(httpx, "post", fake_post)
        return seen

    @pytest.mark.asyncio
    async def test_raised_deadline_reaches_http_timeout(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        seen = self._capture(monkeypatch, 30.0)

        await m.metasearch("test query", 3, engines=["brightdata"])
        assert seen["timeout"] == 30.0

    @pytest.mark.asyncio
    async def test_tight_deadline_keeps_the_paid_call_viable(self, monkeypatch):
        """Floor: a 5s overall deadline must not cut the SERP render short --
        the credit is spent the moment the request goes out."""
        from dhole_mcp import search_metasearch as m

        seen = self._capture(monkeypatch, 5.0)

        await m.metasearch("test query", 3, engines=["brightdata"])
        assert seen["timeout"] == 20.0


class TestNewKeyedEngines:
    """tavily / exa / bocha：可选、可跑、失败可诊断、默认不花钱。"""

    def test_new_names_pass_validation(self):
        assert _validate_engines(["tavily"]) == ["tavily"]
        assert _validate_engines(["exa"]) == ["exa"]
        assert _validate_engines(["bocha"]) == ["bocha"]

    @pytest.mark.asyncio
    async def test_tavily_end_to_end_and_only_tavily_called(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        calls: list = []

        class _TavilyOk:
            status_code = 200

            def json(self):
                return {"results": [
                    {"title": "R1", "url": "https://a.com/1", "content": "正文一"},
                ]}

        def fake_post(url, *a, **k):
            calls.append(url)
            return _TavilyOk()

        monkeypatch.setenv("DHOLE_TAVILY_API_KEY", "tvly-fake")
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(httpx, "post", fake_post)

        results, status = await m.metasearch("test query", 3, engines=["tavily"])

        assert [r["href"] for r in results] == ["https://a.com/1"]
        assert [r["title"] for r in results] == ["R1"]
        assert status.get("tavily") == "ok"
        assert calls == ["https://api.tavily.com/"], "只应调用 tavily 端点"

    @pytest.mark.asyncio
    async def test_keyed_engines_do_not_run_unselected(self, monkeypatch):
        """策略：不设 engines（走免费默认池）时，任何 keyed 引擎都不许花钱。

        免费后端全部 circuit_open -> 若 keyed 被错误触发，calls 就非空；
        即使因此整体失败，报错也必须指出 keyed 是选填项，而不是让人去配 key。
        """
        from dhole_mcp import search_metasearch as m

        calls: list = []

        class _Empty:
            status_code = 200

            def json(self):
                return {}

        def fake_post(url, *a, **k):
            calls.append(url)
            return _Empty()

        for var in ("DHOLE_BRIGHTDATA_API_KEY", "DHOLE_TAVILY_API_KEY",
                    "DHOLE_EXA_API_KEY", "DHOLE_BOCHA_API_KEY"):
            monkeypatch.setenv(var, "key-set")
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(httpx, "post", fake_post)
        # 用"没有可用的免密引擎"来制造"什么都起不来"，而不是把全体塞进冷却：
        # 后者现在是一条正常返回（circuit_open -> engine_blocked），不是报错路径。
        monkeypatch.setattr(m, "_TEXT_ENGINES", {})

        with pytest.raises(m.MetaSearchException) as ei:
            await m.metasearch("test query", 3)

        assert calls == [], "keyed 引擎在未被 engines= 点名时不得发起调用"
        assert "engines=" in str(ei.value), "报错应提示 keyed 引擎是显式选填的"

    @pytest.mark.asyncio
    async def test_exa_402_is_auth_error(self, monkeypatch):
        """实测 Exa 对无 key/欠费回 402 —— 必须可诊断，不进熔断。"""
        from dhole_mcp import search_metasearch as m

        class _PaymentRequired:
            status_code = 402
            text = '{"error":"Payment required to access"}'

        monkeypatch.setenv("DHOLE_EXA_API_KEY", "expired")
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _PaymentRequired())

        results, status = await m.metasearch("q", 3, engines=["exa"])

        assert results == []
        assert status["exa"] == "error:ExaAuthError"
        assert m._is_circuit_open("exa") is False

    @pytest.mark.asyncio
    async def test_bocha_maps_timelimit_to_freshness(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        payloads: list = []

        class _BochaOk:
            status_code = 200

            def json(self):
                return {"data": {"webPages": {"value": [
                    {"name": "N", "url": "https://b.cn/1", "snippet": "摘"}
                ]}}}

        def fake_post(url, json=None, *a, **k):
            payloads.append(json)
            return _BochaOk()

        monkeypatch.setenv("DHOLE_BOCHA_API_KEY", "bk")
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(httpx, "post", fake_post)

        results, status = await m.metasearch("q", 3, engines=["bocha"], timelimit="week")

        assert payloads[-1]["freshness"] == "oneWeek"
        assert payloads[-1]["summary"] is False
        assert [r["href"] for r in results] == ["https://b.cn/1"]

    @pytest.mark.asyncio
    async def test_bocha_without_key_blames_env(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        monkeypatch.delenv("DHOLE_BOCHA_API_KEY", raising=False)
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)

        with pytest.raises(m.MetaSearchException) as ei:
            await m.metasearch("q", engines=["bocha"])

        assert "DHOLE_BOCHA_API_KEY" in str(ei.value)


class TestDefaultPoolEnv:
    """DHOLE_DEFAULT_ENGINES：国内用户可把默认池收敛到直连可达的引擎。"""

    def test_env_override(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        monkeypatch.setenv("DHOLE_DEFAULT_ENGINES", "bing, sogou_weixin")
        assert m._resolve_backends(None) == ["bing", "sogou_weixin"]

    def test_unknown_names_dropped_not_fatal(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        monkeypatch.setenv("DHOLE_DEFAULT_ENGINES", "bing, brvae")
        assert m._resolve_backends(None) == ["bing"]

    def test_unset_keeps_upstream_default(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        monkeypatch.delenv("DHOLE_DEFAULT_ENGINES", raising=False)
        assert m._resolve_backends(None) == list(m._DEFAULT_BACKENDS)
        # 这一行是默认池的快照，不是逻辑：池子换组合时跟着改（两处定义的一致性由
        # test_default_pool_definitions_agree 保证，这里只钉住"env 没设时用的是它"）。
        assert m._DEFAULT_BACKENDS == ["baidu", "bing", "so360",
                                       "bing_global", "yandex", "brave"]


class TestConnectionFailureCooldown:
    """被墙引擎连接失败不应每轮陪跑：连续失败 3 次后冷却 10 分钟。"""

    @staticmethod
    def _install_failing_bing(monkeypatch):
        from dhole_mcp import search_metasearch as m

        calls = {"constructed": 0, "searched": 0}

        class _Broken:
            disabled = False

            def __init__(self, **kwargs):
                calls["constructed"] += 1

            def search(self, *a, **k):
                calls["searched"] += 1
                raise OSError("connection refused")

        monkeypatch.setitem(m._TEXT_ENGINES, "bing", _Broken)
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(m, "_SEARCH_DEADLINE", 1.5)
        return calls

    @pytest.mark.asyncio
    async def test_third_consecutive_failure_cools_the_backend(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        calls = self._install_failing_bing(monkeypatch)
        monkeypatch.setattr(m, "_BACKEND_HEALTH", {})

        for _ in range(3):
            results, status = await m.metasearch("q", 3, engines=["bing"])
            assert results == []
            assert status["bing"].startswith("error:")
        assert calls["searched"] == 3

        # 第 4 次：bing 已被冷却，不再构造/请求。整池都在冷却时**不抛异常**：冷却是
        # 一个有期限、原因已写进 status 的状态，抛异常会让 engine_blocked 与
        # "稍后重试"那条既有链整个失效（实测 next_action 反而叫用户改写查询）。
        results, status = await m.metasearch("q", 3, engines=["bing"])
        assert results == [] and status == {"bing": "circuit_open"}
        assert calls["constructed"] == 3, "冷却期内不得再构造实例"

    @pytest.mark.asyncio
    async def test_success_resets_the_failure_count(self, monkeypatch):
        from types import SimpleNamespace

        from dhole_mcp import search_metasearch as m

        calls = {"constructed": 0, "searched": 0}

        class _Flaky:
            disabled = False
            fail = True

            def __init__(self, **kwargs):
                calls["constructed"] += 1

            def search(self, *a, **k):
                calls["searched"] += 1
                if type(self).fail:
                    raise OSError("connection refused")
                return [SimpleNamespace(title="T", href="https://x.com/1", body="b")]

        monkeypatch.setitem(m._TEXT_ENGINES, "bing", _Flaky)
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(m, "_SEARCH_DEADLINE", 1.5)
        monkeypatch.setattr(m, "_BACKEND_HEALTH", {})

        for _ in range(2):
            results, status = await m.metasearch("q", 3, engines=["bing"])
            assert results == []
            assert status["bing"].startswith("error:")

        _Flaky.fail = False  # 第 3 次成功 -> 计数清零
        await m.metasearch("q", 3, engines=["bing"])

        _Flaky.fail = True
        for _ in range(2):  # 只有 2 次连续失败，不应触发冷却
            results, status = await m.metasearch("q", 3, engines=["bing"])
            assert status["bing"].startswith("error:")

        assert calls["constructed"] == 5, "成功应清零失败计数，2 次失败不足以冷却"


def test_default_pool_definitions_agree():
    """默认池在两个模块里各有一份，必须同步。

    search_engines.DEFAULT_ENGINES 是 dhole 侧名字，search_metasearch._DEFAULT_BACKENDS
    是 backend 侧名字。合成一处需要 search_engines 顶层 import metasearch 链
    （primp/lxml），会破坏它刻意保留的惰性导入 —— 所以留两份、由这条测试钉住。
    历史上这里漂移过一次：test_default_engines_has_four 是个过时的快照，已删。
    """
    from dhole_mcp import search_metasearch as ms
    from dhole_mcp.search_engines import DEFAULT_ENGINES

    as_backends = [ms._DHOLE_TO_BACKEND.get(n, n) for n in DEFAULT_ENGINES]
    assert as_backends == list(ms._DEFAULT_BACKENDS), (
        f"默认池两处定义不一致: {as_backends} vs {list(ms._DEFAULT_BACKENDS)}")


def test_opt_in_engines_are_registered_but_not_pooled():
    """百科 / 公众号 / 搜狗主站 / mwmbl / duckduckgo / yahoo 是**显式点名**的 opt-in：
    注册表里有、默认池里没有。

    默认池决定每轮真实打哪些站点 —— 误加进去会平白多一份限流风险（360、搜狗、百度
    都按 IP 限流）。duckduckgo/yahoo 与 bing 同一个索引家族，进池只多入口不多家族，
    所以它们腾出的席位给了 so360（第三个国内独立索引）。sogou 与 so360 同生态位，
    默认只点一家。baidu_baike/wikipedia/grokipedia 是知识库、mwmbl 覆盖窄，都不该
    替用户默认打开。

    bing_global 是**有意**留在池里的例外：它与 bing 同家族（不增加分母），补的是
    覆盖面 —— 实测 cn 版与国际版结果标题只 1/17 重合，同一个索引的两套入口。
    """
    from dhole_mcp import search_metasearch as ms
    from dhole_mcp.search_engines import DEFAULT_ENGINES

    for name in ("sogou", "baidu_baike", "sogou_weixin",
                 "mwmbl", "duckduckgo", "yahoo"):
        assert name in ms._TEXT_ENGINES, f"{name} 应当是注册过的 opt-in 引擎"
        assert name not in DEFAULT_ENGINES, f"{name} 不许进默认池"
    # 别名也走同一条路（"360" 是 so360 的顺手写法）
    assert ms._DHOLE_TO_BACKEND["360"] == "so360"


def test_readme_documents_every_selectable_engine():
    """README 的「可选的搜索引擎」表是用户挑引擎的唯一出处 —— 漏一个名字，那个引擎
    在对用户来说就等于不存在（默认池那次漏改 `dhole -v` 是同一类漂移：同一份名单
    多处手抄，总有一处先烂）。别名也算：`ddg` / `360` 写在 README 里才敢让人用。
    """
    from pathlib import Path

    from dhole_mcp import search_metasearch as ms

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    missing = [name for name in sorted(set(ms._DHOLE_TO_BACKEND))
               if f"`{name}`" not in readme]
    assert not missing, (
        f"README 没写这些可选引擎（含别名）: {missing} —— "
        f"新增/改名引擎时同步「可选的搜索引擎」表")


def test_every_default_pool_engine_is_registered_and_enabled():
    """默认池里不许出现未注册或被禁用的引擎 —— core 集合那个 bug 的同类。"""
    from dhole_mcp import search_metasearch as ms
    from dhole_mcp.search_engines import DEFAULT_ENGINES

    for name in DEFAULT_ENGINES:
        backend = ms._DHOLE_TO_BACKEND.get(name, name)
        cls = ms._TEXT_ENGINES.get(backend)
        assert cls is not None, f"默认池里的 {name} 没有对应 backend ({backend})"
        assert not cls.disabled, f"默认池包含已禁用引擎 {name}"


def test_index_family_map_covers_the_default_pool():
    """_INDEX_FAMILY 漏一个名字，那个引擎就会被当成独立家族、把共识分母虚报。"""
    from dhole_mcp import search_metasearch as ms
    from dhole_mcp.search_engines import DEFAULT_ENGINES, _INDEX_FAMILY

    missing = [n for n in DEFAULT_ENGINES
               if ms._DHOLE_TO_BACKEND.get(n, n) not in _INDEX_FAMILY]
    assert not missing, f"这些默认引擎不在 _INDEX_FAMILY 里: {missing}"
    # 默认池 6 引擎 / 5 家族（bing 与 bing_global 同家族）：渲染出来只可能是
    # "x of 5"，"x of 6" 不可能出现
    families = {_INDEX_FAMILY[ms._DHOLE_TO_BACKEND.get(n, n)] for n in DEFAULT_ENGINES}
    assert families == {"baidu", "bing", "so360", "yandex", "brave"}, families


def test_vertical_set_only_names_real_engines():
    """_VERTICAL_BACKENDS 是手工维护的第二张名单 —— 它得指着真实存在的引擎。

    名单本身只有一处定义（不是类属性），所以这里钉的是"别腐烂"：
    名字写错时，那条兜底排序规则会静默失效（垂直引擎又回到靠"答得快"霸榜）。
    """
    from dhole_mcp import search_metasearch as ms
    from dhole_mcp.search_engines import _VERTICAL_BACKENDS

    ghost = {n for n in _VERTICAL_BACKENDS if n not in ms._TEXT_ENGINES}
    assert not ghost, f"_VERTICAL_BACKENDS 里有不存在的引擎: {sorted(ghost)}"
    assert "sogou_weixin" in _VERTICAL_BACKENDS, (
        "sogou_weixin 是垂直索引（只覆盖公众号）—— 摘掉它，无重排器的兜底顺序里"
        "它会靠 0.2s 的响应速度排在 bing/yandex 前面")
    # 垂直引擎仍然要有索引家族（共识分母靠它），否则会静默虚报分母
    from dhole_mcp.search_engines import _INDEX_FAMILY
    assert not (_VERTICAL_BACKENDS - set(_INDEX_FAMILY))


def test_vertical_sets_agree():
    """同一张垂直名单在两层各有一份（search_engines 给排序用，search_metasearch 给
    早退配额用），必须同步 —— 与默认池那两份同一个惰性导入理由。"""
    from dhole_mcp import search_metasearch as ms
    from dhole_mcp.search_engines import _VERTICAL_BACKENDS as adapter_side

    assert set(adapter_side) == set(ms._VERTICAL_BACKENDS), (
        f"垂直名单两处定义不一致: {sorted(adapter_side)} vs {sorted(ms._VERTICAL_BACKENDS)}")


class TestVerticalResultsCannotFillTheQuota:
    """早退配额只算通用引擎的产出，否则"加宽池子"的垂直引擎反而把池子变窄。

    实测（2026-09-21 本机默认池）：sogou_weixin 0.2-0.9s 回 10 条，2s 软截止一到就把
    还在跑的 yandex（3.1s）cancel 掉了。这里把软截止压到 0 复现同一形状。

    时钟：快引擎 0s / 慢引擎 0.3s，软截止 0 秒 —— 判据只依赖"谁先完成"，不依赖真实秒数。
    """

    @staticmethod
    def _install(monkeypatch, spec: dict):
        """spec: backend -> (delay_seconds, result_count)。"""
        from types import SimpleNamespace

        from dhole_mcp import search_metasearch as m

        for name, (delay, count) in spec.items():
            # 逐个绑定：直接写内层 class 会闭包到循环变量，两个假引擎会变成同一个。
            def _make(name=name, delay=delay, count=count):
                class _Fake:
                    disabled = False

                    def __init__(self, **kwargs):
                        pass

                    def search(self, *a, **k):
                        time.sleep(delay)
                        return [SimpleNamespace(title=f"{name}-{i}",
                                                href=f"https://{name}.test/{i}", body="b")
                                for i in range(count)]
                _Fake.name = name
                return _Fake
            monkeypatch.setitem(m._TEXT_ENGINES, name, _make())
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(m, "_BACKEND_HEALTH", {})
        monkeypatch.setattr(m, "_SOFT_DEADLINE", 0.0)

    @pytest.mark.asyncio
    async def test_slow_general_engine_survives_a_fast_vertical_flood(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        self._install(monkeypatch, {"sogou_weixin": (0.0, 10), "yandex": (0.3, 14)})
        results, status = await m.metasearch("q", 6, engines=["sogou_weixin", "yandex"])

        assert status["yandex"] == "ok", f"通用引擎被垂直结果的配额挤掉了: {status}"
        assert sum(1 for r in results if r["backend"] == "yandex") == 14

    @pytest.mark.asyncio
    async def test_general_engine_filling_the_quota_still_preempts(self, monkeypatch):
        """对照组：通用引擎独自填满配额时早退行为不变（既有行为不许被这次改动带走）。"""
        from dhole_mcp import search_metasearch as m

        self._install(monkeypatch, {"bing": (0.0, 10), "yandex": (5.0, 14)})
        results, status = await m.metasearch("q", 6, engines=["bing", "yandex"])

        assert status["yandex"] == "preempted", status
        assert all(r["backend"] != "yandex" for r in results)

    @pytest.mark.asyncio
    async def test_vertical_only_pool_still_returns_early(self, monkeypatch):
        """通用引擎根本不在池子里时，垂直结果自己就能触发早退（别把这条堵死）。"""
        from dhole_mcp import search_metasearch as m

        self._install(monkeypatch, {"sogou_weixin": (0.0, 10)})
        results, status = await m.metasearch("q", 6, engines=["sogou_weixin"])

        assert results and status["sogou_weixin"] == "ok"


class TestChallengePageCooldown:
    """HTTP 200 的校验页不是一次性限流。

    实测 so.com 在完全零请求静默 20 分钟后仍回「访问异常页面」，而默认冷却只有 60 秒 ——
    那等于替上游把惩罚续期，正是 MetaBlockedException 文档说要避免的 hammering。状态码
    拒绝（403/429/503）仍然按瞬时处理，所以两类的时长必须分开，且校验页要按**连续次数**
    递增：单次误判不该把国内主力引擎停十分钟。
    """

    @pytest.fixture
    def ms(self, monkeypatch, tmp_path):
        from dhole_mcp import search_metasearch as m

        monkeypatch.setattr(m, "_circuit_state_file", lambda: str(tmp_path / "cb.json"))
        monkeypatch.setattr(m, "_BACKEND_HEALTH", {}, raising=False)
        monkeypatch.setattr(m, "_CHALLENGE_COUNTS", {}, raising=False)
        return m

    def test_the_two_rejection_kinds_are_distinguishable_on_the_exception(self, ms):
        """冷却分档的依据必须由异常自己带着，不能靠 parse 文案猜。"""
        assert ms.MetaBlockedException("HTTP 403").challenge is False
        assert ms.MetaBlockedException("so360 校验页", challenge=True).challenge is True

    def test_status_code_block_stays_at_sixty_seconds(self, ms):
        ms._record_block("brave")
        left = ms._BACKEND_HEALTH["brave"] - time.time()
        assert 50 < left <= 60.5, left

    def test_challenge_escalates_on_the_third_consecutive_strike(self, ms):
        for _ in range(2):
            ms._record_block("so360", challenge=True)
            left = ms._BACKEND_HEALTH["so360"] - time.time()
            assert left <= 60.5, f"前两次只该关 60 秒，实际 {left:.0f}s"
        ms._record_block("so360", challenge=True)
        left = ms._BACKEND_HEALTH["so360"] - time.time()
        assert left > 500, f"第三次连续校验页应升到与被墙同档，实际 {left:.0f}s"

    def test_a_good_round_resets_the_streak(self, ms):
        for _ in range(3):
            ms._record_block("so360", challenge=True)
        ms._record_success("so360")
        ms._record_block("so360", challenge=True)
        assert ms._BACKEND_HEALTH["so360"] - time.time() <= 60.5


class TestBingBlockIsNotSwallowed:
    """Bing 家族的重试循环原先把 MetaBlockedException 一起吞成"空结果"。

    后果有两层：被 403/校验页拒绝时反而**连敲三次**（与 MetaBlockedException 的
    用途正相反），且 metasearch 那一轮看到的是普通空结果 -> 状态 empty、熔断永不
    触发。实测 bing_global 直连被地域跳转挡掉时就是这样每轮白等。
    """

    @pytest.fixture
    def eng(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        calls = {"n": 0}

        def fake(self, *a, **k):
            calls["n"] += 1
            raise self._boom  # type: ignore[attr-defined]

        monkeypatch.setattr(m.BaseSearchEngine, "search", fake)
        e = m.Bing(proxy=None, timeout=5)
        # BingGlobal 继承同一份 search，这条修复对两个入口同时生效
        return e, calls

    def test_block_propagates_without_retrying(self, eng):
        from dhole_mcp import search_metasearch as m

        e, calls = eng
        e._boom = m.MetaBlockedException("HTTP 403")
        with pytest.raises(m.MetaBlockedException):
            e.search("q")
        assert calls["n"] == 1, "被拦不该重试：立刻上抛给熔断器"

    def test_transient_failure_still_retries(self, eng):
        e, calls = eng
        e._boom = OSError("[WinError 10054] 远程主机强迫关闭了一个现有的连接")
        assert e.search("q") is None
        assert calls["n"] == e._retries + 1, "网络抖动重试是这条循环存在的理由，不许一起删掉"


@pytest.mark.asyncio
async def test_empty_report_says_which_status_code_we_actually_saw(monkeypatch):
    """302 没跟随时我们根本没看到结果页，但那与"上游查了、答案是空"共用一个 empty。

    读的人只能看到 "no results"，于是把"这家不给这个 IP 看"当成"这个查询没东西"。
    状态 token 不许动（它同时是 engine_empty 的判据），原因写进 error。
    """
    from dhole_mcp import search_engines as se
    from dhole_mcp import search_metasearch as ms

    async def fake_meta(query, max_results, **kwargs):
        return [], {"bing_global": "empty"}

    monkeypatch.setattr(se, "_metasearch", fake_meta)
    monkeypatch.setattr(ms, "engine_health", lambda: {
        "bing_global": {"http": 302, "last_nodes": 0, "last": 0,
                        "verdict": "not_instrumented"}})
    _, reports = await se.multi_search("q", 5, engines=["bing_global"])  # type: ignore[misc]
    assert reports[0].status == "empty"
    assert "HTTP 302" in reports[0].error
    assert "redirect not followed" in reports[0].error


@pytest.mark.asyncio
async def test_a_real_200_with_nothing_parsable_still_says_no_results(monkeypatch):
    """反向守卫：不许把 200 的空页也包装成状态码问题。"""
    from dhole_mcp import search_engines as se
    from dhole_mcp import search_metasearch as ms

    async def fake_meta(query, max_results, **kwargs):
        return [], {"baidu": "empty"}

    monkeypatch.setattr(se, "_metasearch", fake_meta)
    monkeypatch.setattr(ms, "engine_health", lambda: {
        "baidu": {"http": 200, "last_nodes": 0, "last": 0, "verdict": "unknown_empty"}})
    _, reports = await se.multi_search("q", 5, engines=["baidu"])  # type: ignore[misc]
    assert reports[0].error == "no results"


@pytest.mark.asyncio
async def test_everything_on_cooldown_reports_blocked_instead_of_raising(monkeypatch):
    """"每家都在冷却"是一个有期限、且原因已写进 status 的状态，不是"搜索起不来"。

    实测原形态（engines=["so360"] 撞上冷却）：`error` 是一串裸 Python 字典、
    `engine_blocked` 是**空列表**、`consensus_basis` 说 `single_family`，而
    `next_action` 叫用户"改写查询或试 mode=neural"。metasearch 上面那条注释承诺的
    `circuit_open -> engine_blocked` 整个被抛掉的异常抹掉了，读起来像查询的问题。
    """
    from dhole_mcp import search_engines as se
    from dhole_mcp import search_metasearch as m

    monkeypatch.setattr(m, "_is_circuit_open", lambda b: True)
    results, status = await m.metasearch("q", 5, engines=["so360"])
    assert results == [] and status == {"so360": "circuit_open"}, \
        f"冷却应当作为状态交回上层，而不是抛异常: {status}"

    async def fake_meta(query, max_results, **kwargs):
        return [], {"so360": "circuit_open"}

    monkeypatch.setattr(se, "_metasearch", fake_meta)
    _, reports = await se.multi_search("q", 5, engines=["so360"])  # type: ignore[misc]
    assert reports[0].blocked, "circuit_open 必须落进 engine_blocked"
    assert "circuit open" in reports[0].error


@pytest.mark.asyncio
async def test_a_broken_install_still_gets_the_proxy_advice(monkeypatch):
    """反向守卫：不许把"一家都起不来"（primp 没装、引擎被禁用）也咽成空结果 ——
    那种情况没有期限可等，DHOLE_SEARCH_PROXY 那句建议要留在 error 里。"""
    from dhole_mcp import search_metasearch as m
    from dhole_mcp.search_metasearch import MetaSearchException

    monkeypatch.setattr(m, "_TEXT_ENGINES", {})
    monkeypatch.setattr(m, "_resolve_backends", lambda engines: ["nonexistent"])
    with pytest.raises(MetaSearchException, match="No search engines could start"):
        await m.metasearch("q", 5, engines=["nonexistent"])
