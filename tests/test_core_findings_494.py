"""Regressions for the November core review (#494): usage history bounds (PERF-01) and stale metrics answers (C2)."""
from klotse.k8s import metrics_history as mh
from klotse.ui.metrics_dialog import MetricsDialog, _Request


def test_history_bounds_the_number_of_series_not_only_points_per_series():
    history = mh.History(max_points=2, max_series=4, max_age_s=10_000)
    for index in range(10_000):
        history.add({"total": (float(index), 0.1, 1.0), f"pod-{index}": (float(index), 0.1, 1.0)}, float(index))
    assert len(history.lines) <= 4
    assert "total" in history.lines  # the total is kept in preference to historical pods
    assert all(len(points) <= 2 for points in history.lines.values())


def test_history_expires_points_of_inactive_lines_by_receipt_age():
    history = mh.History(max_points=10, max_series=50, max_age_s=100)
    history.add({"pod-old": (0.0, 0.1, 1.0)}, 0.0)
    history.add({"total": (500.0, 0.1, 1.0)}, 500.0)  # pod-old receives no sample; its only point is now too old
    assert "pod-old" not in history.lines
    assert "total" in history.lines


def test_a_metrics_answer_from_a_superseded_source_or_range_is_discarded(qtbot):
    dialog = MetricsDialog("Usage history: web", lambda: {}, None, True, interval_s=3600)
    qtbot.addWidget(dialog)
    applied = []
    dialog._got = lambda sample: applied.append(("sampled", sample))
    dialog._got_remote = lambda answer, request: applied.append(("remote", answer))
    dialog._request_current = lambda: None
    old = _Request(dialog._generation, "remote", None, "Prometheus A", None, "1h")
    dialog._request, dialog._busy = old, True
    dialog._generation += 1  # the user changed the source or the range while the request was in flight
    dialog._finished(old, answer={"stale": 1})
    assert applied == []
    assert dialog._busy is False and dialog._request is None
