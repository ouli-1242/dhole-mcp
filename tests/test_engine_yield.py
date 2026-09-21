"""引擎产出统计（S3）：把"引擎答了、解析出 0 条"这一格变可见。

免密搜索最贵的失败模式是解析器漂移 —— 上游改版后引擎仍然 200 好好应答，我们的
xpath 却什么都取不到。它表现为"结果变少"而不是报错，且在响应里完全隐形（empty
既不进 engines_used 也不进 engine_blocked）。这里测的是观测层：计数对不对、
判据能不能把"坏了"和"真的没结果"分开、以及它会不会反过来把搜索弄坏。
"""

import json
import time

import pytest

from dhole_mcp import paths
from dhole_mcp import search_metasearch as ms


@pytest.fixture
def stats_dir(tmp_path, monkeypatch):
    """把状态文件指到临时目录，并清空进程内统计。"""
    monkeypatch.setattr(paths, "home", lambda: tmp_path)
    ms._ENGINE_YIELD.clear()
    ms._engine_stats_last_save = 0.0
    yield tmp_path
    ms._ENGINE_YIELD.clear()


def _engine(name: str):
    cls = ms._TEXT_ENGINES[name]
    return cls(proxy=None, timeout=5, verify=True)


class TestParseCounters:
    """计数器本身：三个整数的语义必须精确，否则后面的判据全建立在错数据上。"""

    def test_negative_control_invents_nothing(self):
        # 垃圾 HTML 必须解析成 0 容器 0 可用 —— 解析器不能凭空造结果
        eng = _engine("bing")
        assert eng.extract_results("<html><body><p>hello</p></body></html>") == []
        assert eng.last_extract == (0, 0)

    def test_drift_shape_is_counted_not_silently_empty(self):
        # 容器还在、子元素 xpath 已经错位：这就是漂移，且不需要历史基线就能判
        eng = _engine("bing")
        html = ("<ul><li class='b_algo'><div>标题没了 链接也没了</div></li>"
                "<li class='b_algo'><span>x</span></li></ul>")
        rows = eng.extract_results(html)
        assert len(rows) == 2, "容器仍匹配 -> 解析出 2 行（但都不可用）"
        assert eng.last_extract == (2, 0)

    def test_usable_rows_count_both_href_and_title(self):
        eng = _engine("bing")
        html = ("<ul>"
                "<li class='b_algo'><h2><a href='https://a.test'>A</a></h2><p>s</p></li>"
                "<li class='b_algo'><h2><a href='https://b.test'>B</a></h2><p>s</p></li>"
                "</ul>")
        eng.extract_results(html)
        assert eng.last_extract == (2, 2)

    def test_partial_parse_degradation_is_visible(self):
        # 一条有 href 没 title：容器匹配了但 title 的 xpath 断了 —— 必须记成 1/2
        eng = _engine("bing")
        html = ("<ul>"
                "<li class='b_algo'><h2><a href='https://a.test'>A</a></h2></li>"
                "<li class='b_algo'><h2><a href='https://b.test'></a></h2></li>"
                "</ul>")
        eng.extract_results(html)
        assert eng.last_extract == (2, 1)

    def test_counters_survive_init_override(self):
        """计数器的默认值挂在类上，不是 __init__ 里赋的。

        Bing 和 Duckduckgo 都覆盖了 __init__ 且不调 super()，写在 __init__ 里的
        默认值会让默认池里最常用的两个引擎根本没有这些字段。
        """
        for name in ("bing", "duckduckgo"):
            eng = _engine(name)
            assert eng.last_extract == (-1, -1)
            assert eng.kept_after_filter == -1
            assert eng.http_status is None


class TestYieldVerdicts:
    """判据：只有 item_nodes==0 且 HTTP 200 那一格是真的含糊，其余都能当场判定。"""

    def test_confirmed_drift_after_two_rounds(self, stats_dir):
        status = {"bing": "empty"}
        inst = {"bing": _engine("bing")}
        inst["bing"].last_extract = (8, 0)
        ms._record_engine_outcomes(status, inst)
        assert ms.engine_health()["bing"]["verdict"] == "parser_suspect"
        ms._record_engine_outcomes(status, inst)
        v = ms.engine_health()["bing"]["verdict"]
        assert v == "parser_drift", f"连续两轮 8 容器 0 可用应确认漂移，实为 {v}"

    def test_zero_nodes_without_baseline_is_not_called_drift(self, stats_dir):
        inst = {"bing": _engine("bing")}
        inst["bing"].last_extract = (0, 0)
        ms._record_engine_outcomes({"bing": "empty"}, inst)
        # 样本不够 -> 如实 unknown，不猜"上游没结果"也不猜"坏了"
        assert ms.engine_health()["bing"]["verdict"] == "unknown_empty"

    def test_healthy_baseline_makes_zero_nodes_upstream_empty(self, stats_dir):
        inst = {"bing": _engine("bing")}
        for _ in range(10):
            inst["bing"].last_extract = (9, 9)
            ms._record_engine_outcomes({"bing": "ok"}, inst)
        inst["bing"].last_extract = (0, 0)
        ms._record_engine_outcomes({"bing": "empty"}, inst)
        assert ms.engine_health()["bing"]["verdict"] == "upstream_empty"

    def test_healthy_run_clears_a_prior_drift_streak(self, stats_dir):
        inst = {"bing": _engine("bing")}
        inst["bing"].last_extract = (8, 0)
        ms._record_engine_outcomes({"bing": "empty"}, inst)
        ms._record_engine_outcomes({"bing": "empty"}, inst)
        assert ms.engine_health()["bing"]["verdict"] == "parser_drift"
        inst["bing"].last_extract = (8, 7)
        ms._record_engine_outcomes({"bing": "ok"}, inst)
        assert ms.engine_health()["bing"]["verdict"] == "healthy"
        assert ms._ENGINE_YIELD["bing"]["drift"] == 0

    def test_blocked_and_preempted_are_not_mixed_with_parse_failure(self, stats_dir):
        blocked = _engine("bing")
        ms._record_engine_outcomes({"bing": "circuit_open"}, {"bing": blocked})
        assert ms.engine_health()["bing"]["verdict"] == "blocked"
        asked = _engine("brave")
        ms._record_engine_outcomes({"brave": "preempted"}, {"brave": asked})
        assert ms.engine_health()["brave"]["verdict"] == "not_asked"

    def test_stale_row_is_not_reported_as_current_state(self, stats_dir):
        inst = {"bing": _engine("bing")}
        inst["bing"].last_extract = (8, 0)
        ms._record_engine_outcomes({"bing": "empty"}, inst)
        ms._record_engine_outcomes({"bing": "empty"}, inst)
        assert ms.engine_health()["bing"]["verdict"] == "parser_drift"
        ms._ENGINE_YIELD["bing"]["ts"] = time.time() - 7200
        assert ms.engine_health()["bing"]["verdict"] == "stale", \
            "上一次运行的结论不能被当成现状"


class TestObservabilityIsHarmless:
    """观测面不能反过来把搜索弄坏，也不能变成第三个隐性失败点。"""

    def test_never_raises_on_fake_engine_classes(self, stats_dir):
        class _NoAttrs:
            pass
        # test_engine_registry 往 _TEXT_ENGINES 里塞的就是这种假引擎类
        ms._record_engine_outcomes({"bing": "ok"}, {"bing": _NoAttrs()})
        ms._record_engine_outcomes({"x": "empty"}, {})
        ms._record_engine_outcomes({"y": "error:OSError"}, {"y": None})

    def test_writes_are_debounced(self, stats_dir):
        inst = {"bing": _engine("bing")}
        inst["bing"].last_extract = (5, 5)
        ms._record_engine_outcomes({"bing": "ok"}, inst)
        path = paths.file("engine_stats.json")
        assert path.exists()
        path.unlink()
        ms._engine_stats_last_save = time.time()  # 假装刚写过
        ms._record_engine_outcomes({"bing": "ok"}, inst)
        assert not path.exists(), "60s 内不该重复落盘"

    def test_persisted_snapshot_round_trips(self, stats_dir):
        inst = {"bing": _engine("bing")}
        inst["bing"].last_extract = (8, 0)
        ms._record_engine_outcomes({"bing": "empty"}, inst)
        ms._ENGINE_YIELD.clear()
        ms._load_engine_stats()
        assert ms._ENGINE_YIELD["bing"]["last_nodes"] == 8
        assert ms._ENGINE_YIELD["bing"]["last"] == 0

    def test_corrupt_stats_file_does_not_break_import_or_health(self, stats_dir):
        paths.file("engine_stats.json").write_text("{not json", encoding="utf-8")
        ms._load_engine_stats()
        assert ms._ENGINE_YIELD == {}


class TestDiagnosticsRow:
    """dhole -v 必须只做文件系统读：它要在半坏的精简安装上也能跑。"""

    def _row(self, stats):
        from dhole_mcp import updater
        if stats is None:
            return updater._engine_yield_row()
        paths.file("engine_stats.json").write_text(json.dumps(stats), encoding="utf-8")
        return updater._engine_yield_row()

    def test_drift_is_named_as_drift(self, stats_dir):
        label, state, ok = self._row({
            "bing": {"status": "empty", "last_nodes": 8, "last": 0, "mean": 0,
                     "drift": 2, "zero": 0, "ts": time.time()},
        })
        assert label == "engine yield"
        assert "PARSER BROKEN" in state and "bing" in state
        assert ok is False, "确认漂移不能让诊断行显示为正常"

    def test_healthy_engine_reads_as_normal(self, stats_dir):
        _, state, ok = self._row({
            "bing": {"status": "ok", "last_nodes": 9, "last": 9, "mean": 8.5,
                     "drift": 0, "zero": 0, "ts": time.time()},
        })
        assert ok is True and "PARSER BROKEN" not in state
        assert "8.5" in state

    def test_blocked_engine_is_not_reported_as_broken_parser(self, stats_dir):
        _, state, ok = self._row({
            "brave": {"status": "circuit_open", "last_nodes": -1, "last": -1,
                      "mean": 0, "drift": 0, "zero": 0, "ts": time.time()},
        })
        assert "blocked" in state and "PARSER BROKEN" not in state
        assert ok is True, "被墙不是解析器坏了，不该抢走同一个告警位"

    def test_zero_is_not_confused_with_missing(self, stats_dir):
        """0 条可用产出是这里最有意义的值，不能被当成"没有观测"。

        `st.get(k) or default` 会把 0 变成 default —— 这个读取器一开始就这么写，
        正好把漂移判据吞掉。
        """
        _, state, ok = self._row({
            "brave": {"status": "empty", "last_nodes": 0, "last": 0, "mean": 0,
                      "drift": 0, "zero": 3, "ts": time.time()},
        })
        assert ok is True  # 0 容器 + 无基线 = 未知，不是坏了
        assert "brave: 0 this query" in state, state

    def test_missing_file_says_no_data_rather_than_looking_fine(self, stats_dir):
        _, state, _ = self._row(None)
        assert "no data yet" in state

    def test_junk_in_stats_file_degrades_to_nothing_not_a_crash(self, stats_dir):
        paths.file("engine_stats.json").write_text("[1, 2]", encoding="utf-8")
        from dhole_mcp import updater
        assert updater._engine_yield_row()[1] == "no data yet (run a search first)"

    def test_capabilities_shows_yield_on_every_return_path(self, stats_dir, monkeypatch):
        """capabilities() 有多条提前 return 的分支，产出行不能只在最好的那条里。"""
        from dhole_mcp import updater
        paths.file("engine_stats.json").write_text(json.dumps({
            "bing": {"status": "empty", "last_nodes": 7, "last": 0, "mean": 0,
                     "drift": 2, "zero": 0, "ts": time.time()},
        }), encoding="utf-8")
        monkeypatch.setattr(updater, "_has_module", lambda _n: False)
        labels = [c[0] for c in updater.capabilities()]
        assert "engine yield" in labels, "缺重依赖时更要看得到引擎产出"
