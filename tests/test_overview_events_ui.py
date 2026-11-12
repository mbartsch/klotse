from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPlainTextEdit

from klotse.k8s.resources import Row
from klotse.ui import overview as overview_module
from klotse.ui.overview import CLUSTER_CARDS, OverviewView


def event_row(message="Deployment api failed to schedule because no nodes match its constraints."):
    return Row("event-1", "payments", {"kind": "Event"}, type="Warning", reason="FailedScheduling",
               object="Pod/api-7d9f", message=message, count=1, last_seen="5m")


def test_cluster_recent_events_show_readable_wrapped_columns_and_hide_fit_control(qtbot):
    view = OverviewView(CLUSTER_CARDS, context="test")
    qtbot.addWidget(view)
    view.show()
    message = "Deployment api failed to schedule because no nodes match its constraints."
    view.set_data({"counts": {}, "events": [event_row(message)], "lists": {}})

    assert [(heading, attr) for heading, attr, *_ in view.events_table.model.columns] == [
        ("Object", "object"), ("Namespace", "namespace"), ("Reason", "reason"),
        ("Age", "last_seen"), ("Message", "message"),
    ]
    assert view.events_table.model.index(0, 4).data() == message
    assert view.events_table.view.wordWrap()
    assert all(not view.events_table.view.isColumnHidden(i) for i in range(5))
    assert view.events_table.fit_button.isHidden()
    assert view.stack.minimumHeight() >= 270


def test_recent_event_refresh_keeps_user_column_widths(qtbot):
    view = OverviewView(CLUSTER_CARDS, context="test")
    qtbot.addWidget(view)
    view.show()
    view.set_data({"counts": {}, "events": [event_row()], "lists": {}})
    view.events_table.view.setColumnWidth(0, 181)

    view.set_data({"counts": {}, "events": [event_row("A newer event")], "lists": {}})

    assert view.events_table.view.columnWidth(0) == 181


def test_activating_a_recent_event_opens_its_full_message(qtbot):
    view = OverviewView(CLUSTER_CARDS, context="test")
    qtbot.addWidget(view)
    view.show()
    message = "First line\nSecond line with the full diagnostic explanation."
    view.set_data({"counts": {}, "events": [event_row(message)], "lists": {}})

    index = view.events_table.proxy.index(0, 4)
    view.events_table.view.setCurrentIndex(index)
    qtbot.keyClick(view.events_table.view, Qt.Key_Return)  # also on macOS, where Qt alone would not activate the row

    assert len(view._event_dialogs) == 1
    dialog = next(iter(view._event_dialogs.values()))
    assert dialog.isVisible()
    assert dialog.windowTitle() == "Event details"
    assert dialog.findChild(QPlainTextEdit, "eventFullMessage").toPlainText() == message
    dialog.close()


def test_return_opens_the_event_on_macos_even_when_qt_would_not_activate_the_row(qtbot, monkeypatch):
    monkeypatch.setattr(overview_module.sys, "platform", "darwin")  # the filter is installed only there
    view = OverviewView(CLUSTER_CARDS, context="test")
    qtbot.addWidget(view)
    view.show()
    view.set_data({"counts": {}, "events": [event_row("Mac message")], "lists": {}})
    table = view.events_table.view
    table.activated.disconnect()  # what Qt does on macOS: Return never reaches `activated`
    table.setCurrentIndex(view.events_table.proxy.index(0, 4))

    qtbot.keyClick(table, Qt.Key_Return)

    assert len(view._event_dialogs) == 1
    next(iter(view._event_dialogs.values())).close()


def test_double_clicking_a_recent_event_opens_its_full_message(qtbot):
    view = OverviewView(CLUSTER_CARDS, context="test")
    qtbot.addWidget(view)
    view.resize(1200, 800)
    view.show()
    message = "Full event details are available after opening this row."
    view.set_data({"counts": {}, "events": [event_row(message)], "lists": {}})
    qtbot.waitUntil(lambda: view.events_table.view.visualRect(view.events_table.proxy.index(0, 4)).isValid())
    index = view.events_table.proxy.index(0, 4)
    view.events_table.view.doubleClicked.emit(index)

    assert len(view._event_dialogs) == 1
    dialog = next(iter(view._event_dialogs.values()))
    assert dialog.findChild(QPlainTextEdit, "eventFullMessage").toPlainText() == message
    dialog.close()
