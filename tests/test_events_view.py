"""The Events view of the Details panel: what the cluster says happened to the selected object."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMessageBox

from klotse.k8s.client import KubeService
from klotse.ui import events_view
from klotse.ui.detail_panel import DetailPanel
from klotse.ui.workspace import ClusterWorkspace

NOW = datetime.now(timezone.utc)


def event(reason="Pulled", kind="Normal", message="ok", count=1, minutes=1, component="kubelet"):
    return SimpleNamespace(type=kind, reason=reason, message=message, count=count, last_seen=f"{minutes}m", ts=NOW - timedelta(minutes=minutes),
                           raw={"source": {"component": component}})


def row(uid="u1", ns="shop", name="web-1", **raw):
    return SimpleNamespace(name=name, namespace=ns, secret_data={}, raw={"metadata": {"name": name, "namespace": ns, "uid": uid}, **raw})


# ---- the page ----------------------------------------------------------------------------------------------------
def test_no_events_says_why_the_list_may_be_empty():
    assert "No events for web-1" in events_view.render([], "web-1") and "about an hour" in events_view.render([], "web-1")


def test_warnings_carry_a_symbol_and_the_word_and_repeats_show_their_count():
    page = events_view.render([event("BackOff", "Warning", "Back-off restarting", count=7), event()], "web-1")
    assert "▲ Warning" in page and "(×7)" in page and "1 warning" in page and "kubelet" in page
    assert "2 events, newest first" in page


def test_everything_from_the_cluster_is_escaped():
    page = events_view.render([event("<b>x</b>", message="<script>alert(1)</script>", component="<i>c</i>")])
    assert "<script>" not in page and "&lt;script&gt;" in page and "<b>x</b>" not in page and "<i>c</i>" not in page


def test_a_full_page_says_it_is_cut():
    assert "showing the newest 200" in events_view.render([event() for _ in range(200)])


# ---- the service -------------------------------------------------------------------------------------------------
class Core:
    def __init__(self):
        self.calls = []

    def _page(self, n):
        items = [SimpleNamespace(metadata=SimpleNamespace(name=f"e{i}", namespace="shop", creation_timestamp=NOW - timedelta(minutes=i)),
                                 involved_object=SimpleNamespace(kind="Pod", name="web-1"), type="Normal", reason="R", message="m",
                                 count=1, last_timestamp=NOW - timedelta(minutes=i), event_time=None) for i in range(n)]
        return SimpleNamespace(items=items, metadata=SimpleNamespace(_continue=None))

    def list_namespaced_event(self, ns, **kw):
        self.calls.append(("ns", ns, kw["field_selector"]))
        return self._page(3)

    def list_event_for_all_namespaces(self, **kw):
        self.calls.append(("all", kw["field_selector"]))
        return self._page(250)


def service():
    svc = object.__new__(KubeService)
    svc._core = Core()
    return svc


def test_events_are_matched_by_uid_in_the_objects_namespace_newest_first():
    svc = service()
    rows = svc.list_object_events("shop", "u1")
    assert svc._core.calls == [("ns", "shop", "involvedObject.uid=u1")]
    assert [r.name for r in rows] == ["e0", "e1", "e2"]


def test_a_cluster_scoped_object_is_searched_in_every_namespace_and_the_list_is_capped():
    svc = service()
    assert len(svc.list_object_events("", "n-uid")) == KubeService.EVENTS_SHOWN
    assert svc._core.calls == [("all", "involvedObject.uid=n-uid")]


def test_an_object_without_a_uid_is_refused():
    with pytest.raises(ValueError):
        service().list_object_events("shop", "")


# ---- the panel ---------------------------------------------------------------------------------------------------
def panel(qtbot):
    p = DetailPanel()
    qtbot.addWidget(p)
    p.show()
    return p


def test_the_events_button_is_there_only_for_objects_with_a_uid_that_are_not_events(qtbot):
    p = panel(qtbot)
    p.show_row(row())
    assert p.view_bar.isVisible() and p.events_button.isVisible()
    p.show_row(SimpleNamespace(name="x", secret_data={}, raw={"metadata": {"name": "x"}, "data": {}}))
    assert not p.view_bar.isVisible()
    p.show_row(row(involvedObject={"kind": "Pod"}))
    assert not p.events_button.isVisible()


def test_choosing_events_asks_once_and_shows_the_answer(qtbot):
    p = panel(qtbot)
    asked = []
    p.events_requested.connect(asked.append)
    r = row()
    p.show_row(r)
    p.events_button.click()
    assert asked == [r] and p.events.isVisible() and "Loading events" in p.events.toPlainText()
    p.show_events(r, [event("Scheduled")])
    assert "Scheduled" in p.events.toPlainText()
    p.yaml_button.click()
    p.events_button.click()
    assert asked == [r, r]  # a fresh look each time it is opened


def test_the_events_view_follows_the_selection_and_ignores_late_answers(qtbot):
    p = panel(qtbot)
    asked = []
    p.events_requested.connect(asked.append)
    first, second = row("u1", name="a"), row("u2", name="b")
    p.show_row(first)
    p.events_button.click()
    p.show_row(second)  # still in the Events view: the new object's events are asked for
    assert asked == [first, second]
    p.show_events(first, [event("Stale")])
    assert "Stale" not in p.events.toPlainText()
    p.show_events(second, [event("Fresh")])
    assert "Fresh" in p.events.toPlainText()


def test_an_error_is_shown_in_the_view(qtbot):
    p = panel(qtbot)
    r = row()
    p.show_row(r)
    p.events_button.click()
    p.show_events(r, error="events is forbidden")
    assert "Cannot read the events: events is forbidden" in p.events.toPlainText()


def test_formatted_and_yaml_still_work_next_to_events(qtbot):
    p = panel(qtbot)
    p.show_row(row(spec={"template": {}, "strategy": {}}))
    assert p.formatted_button.isVisible() and p.formatted.isVisible()
    p.events_button.click()
    assert p.events.isVisible() and not p.formatted.isVisible() and not p.editor.isVisible()
    p.yaml_button.click()
    assert p.editor.isVisible() and not p.events.isVisible()


# ---- the workspace -----------------------------------------------------------------------------------------------
def test_the_workspace_loads_events_for_the_panel(qtbot):
    calls = []
    ws = ClusterWorkspace(SimpleNamespace(list_object_events=lambda *a: calls.append(a) or [event("Pulled")]), "ctx")
    qtbot.addWidget(ws)
    r = row()
    ws.detail.show_row(r)
    ws.detail.events_button.click()
    qtbot.waitUntil(lambda: "Pulled" in ws.detail.events.toPlainText(), timeout=5000)
    assert calls == [("shop", "u1")]


def test_a_failure_goes_into_the_view_not_a_dialog(qtbot, monkeypatch):
    def boom(*a):
        raise RuntimeError("forbidden")
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: shown.append(a))
    ws = ClusterWorkspace(SimpleNamespace(list_object_events=boom), "ctx")
    qtbot.addWidget(ws)
    ws.detail.show_row(row())
    ws.detail.events_button.click()
    qtbot.waitUntil(lambda: "Cannot read" in ws.detail.events.toPlainText(), timeout=5000)
    assert shown == []
