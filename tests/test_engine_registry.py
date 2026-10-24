"""Engine-name registry + keyed JSON engines (tavily/bocha).

策略：keyed 引擎默认不跑，只有 engines= 显式点名才执行 —— 每次调用都花钱。
"""

import time

import httpx
import pytest

from dhole_mcp.search import _validate_engines


class _OkEmpty:
    status_code = 200
    text = "{}"

    def json(self):
        return {}


class TestKeyedRegistryIsExactlyTwo:
    """17.0：keyed 后端只剩 tavily + bocha。

    ``KEYED_ENGINES`` 与 ``_DHOLE_TO_BACKEND`` 必须严格同集。多出来的名字会通过
    ``engines=`` 校验却没有任何实现（既不在 _TEXT_ENGINES 也不在 KEYED_ENGINES），
    于是「点了个不存在的引擎」退化成静默零结果 —— 正是本项目拒绝的那种失败。
    """

    def test_all_keyed_names_pass_validation(self):
        from dhole_mcp.search_metasearch import KEYED_ENGINES

        names = sorted(KEYED_ENGINES)
        assert names == ["bocha", "tavily"]
        assert _validate_engines(names) == names

    def test_retired_keyed_names_are_not_selectable(self):
        from dhole_mcp.search import SecurityError

        for name in ("brightdata", "exa"):
            with pytest.raises(SecurityError):
                _validate_engines([name])

    def test_every_selectable_name_has_an_implementation(self):
        from dhole_mcp import search_metasearch as m

        implemented = set(m._TEXT_ENGINES) | set(m.KEYED_ENGINES)
        orphans = set(m._DHOLE_TO_BACKEND.values()) - implemented
        assert not orphans, f"这些引擎名可选但没有实现: {sorted(orphans)}"


class TestBochaAuthError:
    """一个错 key 不能长得像「这家返回了空」。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [401, 403])
    async def test_auth_status_is_distinguishable_from_empty(self, monkeypatch, code):
        from dhole_mcp import search_metasearch as m

        class _Denied:
            status_code = code
            text = "invalid api key"

        monkeypatch.setenv("DHOLE_BOCHA_API_KEY", "wrong-key")
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _Denied())

        results, status = await m.metasearch("test query", 3, engines=["bocha"])

        assert results == []
        assert status["bocha"] == "error:BochaAuthError"

    @pytest.mark.asyncio
    async def test_auth_error_does_not_trip_the_circuit_breaker(self, monkeypatch):
        """坏 key 是永久状态 —— 走 blocked/冷却那条路只会把真正的诊断（key 被拒）
        盖成 circuit_open。这里把两份模块级计数都清空，否则同会话里前面几次
        鉴权失败会累进 `_CONN_FAIL_COUNTS`，第 3 次就落到 600s 连接冷却上。"""
        from dhole_mcp import search_metasearch as m

        class _Denied:
            status_code = 403
            text = "key not authorized"

        monkeypatch.setenv("DHOLE_BOCHA_API_KEY", "wrong-key")
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(m, "_BACKEND_HEALTH", {})
        monkeypatch.setattr(m, "_CONN_FAIL_COUNTS", {})
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _Denied())

        await m.metasearch("test query", 3, engines=["bocha"])

        assert m._is_circuit_open("bocha") is False


class TestBochaTimeout:
    """付费调用要跟上整体死线，但有个下限：请求一发出去额度就扣了。"""

    @staticmethod
    def _capture(monkeypatch, deadline):
        from dhole_mcp import search_metasearch as m

        seen: dict = {}

        def fake_post(*args, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            return _OkEmpty()

        monkeypatch.setenv("DHOLE_BOCHA_API_KEY", "fake-key")
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(m, "_SEARCH_DEADLINE", deadline)
        monkeypatch.setattr(httpx, "post", fake_post)
        return seen

    @pytest.mark.asyncio
    async def test_raised_deadline_reaches_http_timeout(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        seen = self._capture(monkeypatch, 30.0)

        await m.metasearch("test query", 3, engines=["bocha"])
        assert seen["timeout"] == 30.0

    @pytest.mark.asyncio
    async def test_tight_deadline_keeps_the_paid_call_viable(self, monkeypatch):
        """下限：整体死线只有 5s 时也不该把已扣费的 SERP 渲染掐断。"""
        from dhole_mcp import search_metasearch as m

        seen = self._capture(monkeypatch, 5.0)

        await m.metasearch("test query", 3, engines=["bocha"])
        assert seen["timeout"] == 20.0


class TestNewKeyedEngines:
    """tavily / bocha：可选、可跑、失败可诊断、默认不花钱。"""

    def test_new_names_pass_validation(self):
        assert _validate_engines(["tavily"]) == ["tavily"]
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

        for var in ("DHOLE_TAVILY_API_KEY", "DHOLE_BOCHA_API_KEY"):
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

        # 'w' 是生产路径上真正流下来的值（search_engines 先归一成短码）。这里此前喂
        # "week"，与被测代码共享同一个错误假设，两边一起绿。
        results, status = await m.metasearch("q", 3, engines=["bocha"], timelimit="w")

        assert payloads[-1]["freshness"] == "oneWeek"
        assert payloads[-1]["summary"] is False
        assert [r["href"] for r in results] == ["https://b.cn/1"]

    @pytest.mark.asyncio
    async def test_bocha_without_key_blames_env(self, monkeypatch):
        """没配 key 时报错要怪 key —— 不能把人支去查搜索代理（两回事）。"""
        from dhole_mcp import search_metasearch as m

        monkeypatch.delenv("DHOLE_BOCHA_API_KEY", raising=False)
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)

        with pytest.raises(m.MetaSearchException) as ei:
            await m.metasearch("q", engines=["bocha"])

        msg = str(ei.value)
        assert "DHOLE_BOCHA_API_KEY" in msg
        assert "DHOLE_SEARCH_PROXY" not in msg


class TestDefaultPoolEnv:
    """DHOLE_DEFAULT_ENGINES：国内用户可把默认池收敛到直连可达的引擎。"""

    def test_env_override(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        monkeypatch.setenv("DHOLE_DEFAULT_ENGINES", "bing, so360")
        assert m._resolve_backends(None) == ["bing", "so360"]

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
        assert m._DEFAULT_BACKENDS == ["baidu", "bing", "sogou", "so360",
                                       "bing_global", "yahoo", "yandex", "brave",
                                       "duckduckgo", "marginalia"]


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


def test_default_pool_is_the_whole_registry():
    """17.0：没有 opt-in 层 —— 免密引擎注册了几家，默认池就是几家。

    这条把「池子=注册表」钉成结构而不是巧合：多注册一家却忘了进池，那个引擎对
    `engines=None` 的调用方就等于不存在；池子里留一个已删的名字，那一席每轮空转。
    别名（`360` → so360、`ddg` → duckduckgo）也走同一条校验路径。
    """
    from dhole_mcp import search_metasearch as ms
    from dhole_mcp.search_engines import DEFAULT_ENGINES

    pooled = {ms._DHOLE_TO_BACKEND.get(n, n) for n in DEFAULT_ENGINES}
    assert pooled == set(ms._TEXT_ENGINES), (
        f"默认池与注册表不一致: 池多 {sorted(pooled - set(ms._TEXT_ENGINES))} / "
        f"注册多 {sorted(set(ms._TEXT_ENGINES) - pooled)}")
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


def test_readme_engine_roster_matches_the_registry():
    """README 不仅要『提到』每个名字，还要把它们**归对池子**。

    上一条守卫只查名字出现过 —— README 因此可以在描述 16.x 形状（默认池 6 + opt-in 8）
    的同时把 16 个名字全列上，绿着通过（真发生过：那条「免密 14 个」一直留到 17.0 之后）。
    这条钉的是归属。G25 附注一/M1 的教训是**扫描面**：原来只扫「搜索引擎与重排」一节，
    于是 env 表里「默认池 6 颗」的旧账在节外绿着活了整整一版 —— 判据扫不到的位置必然
    漂移。现在扫 README **全文**（引擎名可以出现在任何一节：env 表、变体表、doctor），
    并把 DEFAULT_ENGINES 的字面钉死（默认免密池 10 颗）——池子变了必须动这条测试，
    不许静默。
    """
    import re
    from pathlib import Path

    from dhole_mcp import search_metasearch as ms
    from dhole_mcp.search_engines import DEFAULT_ENGINES

    # 字面钉死（G25 附注一）：默认免密池就是这 10 颗。改池子 = 显式改这里。
    assert DEFAULT_ENGINES == (
        "baidu", "bing", "sogou", "so360", "bing_global", "yahoo",
        "yandex", "brave", "duckduckgo", "marginalia",
    ), f"默认免密池变了（现值 {DEFAULT_ENGINES}）—— 改池子要同时改 README/手册/这里"

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    # 扫描面 = README 全文（G25 附注一：只扫一节时，节外的旧账必然漂移）。
    # 整词匹配：`_` 算词字符，所以 `bing` 不会命中 `bing_global`、`sogou` 不会命中
    # `sogou_weixin`（子串陷阱，与 test_tool_descriptions._engine_tokens 同类）。
    words = set(re.findall(r"[a-z0-9_]+", readme.lower()))

    retired = {"sogou_weixin", "baidu_baike", "wikipedia", "grokipedia", "mwmbl"}
    assert not (words & retired), (
        f"README 引擎段还点名了 17.0 已删除的引擎: {sorted(words & retired)} —— "
        "调用方照着点会收到 invalid_request，读起来像是我们坏了")
    missing = sorted(set(DEFAULT_ENGINES) - words)
    assert not missing, (
        f"README 引擎段漏了默认池引擎: {missing} —— "
        "README 是用户挑引擎的唯一出处，漏一个那个引擎就等于不存在")
    missing_keyed = sorted(set(ms.KEYED_ENGINES) - words)
    assert not missing_keyed, f"README 引擎段漏了 keyed 引擎: {missing_keyed}"


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
    # 默认池 10 引擎 / 7 家族（bing、bing_global、yahoo、duckduckgo 同属 bing 索引）：
    # 渲染出来只可能是 "x of 7"，"x of 8" 及往上不可能出现
    families = {_INDEX_FAMILY[ms._DHOLE_TO_BACKEND.get(n, n)] for n in DEFAULT_ENGINES}
    assert families == {"baidu", "bing", "brave", "so360", "sogou", "yandex",
                        "marginalia"}, families


class TestSoftDeadlinePreemption:
    """软截止一到就 cancel 还没答话的引擎 —— 快引擎填满配额是它的正当理由。

    17.0 前这里还有一条「垂直引擎不许独自填满配额」：sogou_weixin 0.2-0.9s 回 10 条
    会把还在跑的 yandex（3.1s）挤掉，而它只覆盖公众号。垂直索引随引擎一起删掉了，
    留下的这条是它的对照组 —— 早退行为本身不许被那次改动带走。

    时钟：快引擎 0s / 慢引擎 5s，软截止 0 秒 —— 判据只依赖"谁先完成"，不依赖真实秒数。

    2.4 之后的边界：查询选了语言车道时，preferred 侧一条都没答话的等待**压过**软截止
    （快的那侧填的是答非所问语种的一页）。所以「preempted」这条用中间地带的查询钉，
    车道等待这条用纯英文查询钉 —— 两个契约各有一条，谁也不许吃掉谁。
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
    async def test_a_full_engine_preempts_the_slow_one(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        # 中间地带的查询（CJK 占比 0.25，不选车道）：软截止纪律原样成立。
        self._install(monkeypatch, {"bing": (0.0, 10), "yandex": (5.0, 14)})
        results, status = await m.metasearch("transformer 注意力", 6,
                                             engines=["bing", "yandex"])

        assert status["yandex"] == "preempted", status
        assert all(r["backend"] != "yandex" for r in results)

    @pytest.mark.asyncio
    async def test_a_laned_query_holds_past_soft_deadline_for_its_side(self, monkeypatch):
        from dhole_mcp import search_metasearch as m

        # 纯英文查询选了英文车道，而 preferred 侧只有 yandex 一家、5 秒才答 ——
        # 这一刻软截止买走的是"答非所问语种"的一页，所以等它（上限是硬期限）。
        self._install(monkeypatch, {"bing": (0.0, 10), "yandex": (5.0, 14)})
        results, status = await m.metasearch("transformer", 6,
                                             engines=["bing", "yandex"])

        assert status["yandex"] == "ok", status
        assert any(r["backend"] == "yandex" for r in results)


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
