"""#500: the single-pod and the several-pod log tabs share one toolbar: the regular-expression filters with Ignore case, Find,
Wrap, Save, Restart and Clear, and the same tail rule (0 = all lines)."""
from PySide6.QtWidgets import QFileDialog, QPlainTextEdit

from klotse.ui.log_view import LogView
from klotse.ui.multi_log import MultiLogView


class Stream:
    def stream(self, n):
        return iter([])

    def close(self):
        pass


class Service:
    def __init__(self):
        self.calls = []

    def open_log_stream(self, namespace, pod, container=None, tail=500, **options):
        self.calls.append((container, tail, options))
        return Stream()


def single(qtbot):
    view = LogView(Service(), "ns", "pod", ["app"])
    qtbot.addWidget(view)
    return view


def several(qtbot):
    view = MultiLogView(Service(), "ns", lambda selector: [], lambda fn, on_result, on_error=None: None)
    qtbot.addWidget(view)
    return view


def test_a_single_pod_tab_has_the_filters_restart_and_save_of_the_shared_toolbar(qtbot):
    view = single(qtbot)
    assert view.drop.accessibleName() and view.ignore_case.isChecked()
    assert view.restart_button.text() == "Restart" and view.save_button.text() == "Save…"


def test_the_tail_rule_is_zero_for_all_lines_on_both_tabs(qtbot):
    view = single(qtbot)
    view.tail.setValue(0)
    assert view.tail.specialValueText() == "all" and view.tail.minimum() == 0
    view.start()
    qtbot.waitUntil(lambda: any(call[1] is None for call in view.service.calls), timeout=3000)  # 0 opens the stream with no tail limit
    several_view = several(qtbot)
    assert several_view.tail.specialValueText() == "all" and several_view.tail.minimum() == 0


def test_a_filter_shows_only_the_matching_lines_of_a_single_pod(qtbot):
    view = single(qtbot)
    view._append("alpha ok\n")
    view._append("beta error\n")
    view._append("gamma ok\n")
    view.match.setText("ok")
    view.refilter()
    assert view.text.toPlainText() == "alpha ok\ngamma ok\n"
    view.match.clear()
    view.drop.setText("beta")
    view.refilter()
    assert "beta" not in view.text.toPlainText() and "alpha ok" in view.text.toPlainText()


def test_ignore_case_applies_to_the_filters_of_a_single_pod(qtbot):
    view = single(qtbot)
    view._append("Error here\n")
    view._append("fine\n")
    view.match.setText("error")
    view.ignore_case.setChecked(True)
    view.refilter()
    assert view.text.toPlainText() == "Error here\n"
    view.ignore_case.setChecked(False)
    view.refilter()
    assert view.text.toPlainText() == ""


def test_save_writes_the_visible_lines_of_a_single_pod(qtbot, monkeypatch, tmp_path):
    view = single(qtbot)
    view._append("one\n")
    target = tmp_path / "pod.txt"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    view.save()
    assert target.read_text(encoding="utf-8") == "one\n"


def test_the_several_pod_tab_wraps_and_finds_like_a_single_pod(qtbot):
    view = several(qtbot)
    assert view.wrap.text() == "Wrap" and view.search.placeholderText().startswith("Find in logs")
    view.wrap.setChecked(True)
    assert view.text.lineWrapMode() == QPlainTextEdit.WidgetWidth
    view.text.setPlainText("first\nsecond target\n")
    view.search.setText("target")
    view.search.returnPressed.emit()
    assert view.text.textCursor().selectedText() == "target"
