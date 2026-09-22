"""测试层：验证阶段时钟、缺失用量和并发上下文隔离，不把未知费用写成零。"""

from app.services.chat.metrics import RunMetrics, active_metrics, measure_run

"""统计测试函数：分阶段累计、首段等待及失败调用仍计入分母。"""

def test_metrics_track_stages_and_unknown_usage(monkeypatch):
    import app.services.chat.metrics as metrics
    clock = iter([0, 1, 3, 4, 6])
    monkeypatch.setattr(metrics, "perf_counter", lambda: next(clock))
    run = RunMetrics()
    run.observe("progress", {"stage": "extract"})
    run.observe("progress", {"stage": "plan_search"})
    run.observe("draft", {"text": "初步建议"})
    run.models.append({"provider": "kimi", "input_tokens": 12, "output_tokens": 3,
                       "total_tokens": 15, "success": True})
    run.models.append({"provider": "deepseek", "input_tokens": None, "output_tokens": None,
                       "total_tokens": None, "success": False})
    report = run.finish()
    assert report["stages_seconds"] == {"received": 1, "extract": 2, "plan_search": 3}
    assert report["first_draft_seconds"] == 4 and report["total_seconds"] == 6
    assert report["known_total_tokens"] == 15 and report["unknown_usage_calls"] == 1
    assert report["cost_cny"] is None


"""上下文测试函数：异常也记录一次结果，退出恢复原上下文，不泄露对话内容。"""

def test_metrics_scope_cleans_up_on_failure():
    reports = []
    try:
        with measure_run("test-id", lambda event, data: reports.append((event, data))):
            assert active_metrics.get() is not None
            raise RuntimeError("private-message")
    except RuntimeError:
        pass
    assert active_metrics.get() is None
    assert len(reports) == 1 and reports[0][0] == "metrics"
    assert reports[0][1]["outcome"] == "failed"
    assert "private-message" not in str(reports)
