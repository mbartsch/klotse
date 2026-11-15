"""UX review batch 2 (#496): typed work must not be lost without a question, and an action must not start by accident."""
from PySide6.QtWidgets import QDialogButtonBox, QMessageBox

from klotse.ui.apply_dialog import ApplyDialog
from klotse.ui.edit_dialog import EditDialog
from klotse.ui.node_dialogs import DrainDialog
from klotse.ui.review_dialog import EDIT, ReviewDialog


def answer(monkeypatch, value):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *args, **kwargs: asked.append(args[1]) or value)
    return asked


def test_escape_in_the_yaml_editor_asks_and_default_keeps_the_edit(qtbot, monkeypatch):
    dialog = EditDialog("Edit ConfigMap c", {"kind": "ConfigMap", "metadata": {"name": "c"}, "data": {"a": "1"}})
    qtbot.addWidget(dialog)
    asked = answer(monkeypatch, QMessageBox.No)
    dialog.editor.setPlainText(dialog.editor.toPlainText() + "\n# changed\n")
    with qtbot.assertNotEmitted(dialog.rejected):
        dialog.reject()  # No: the edit stays on screen
    assert asked
    answer(monkeypatch, QMessageBox.Yes)
    with qtbot.waitSignal(dialog.rejected):
        dialog.reject()


def test_escape_without_changes_closes_without_asking(qtbot, monkeypatch):
    dialog = EditDialog("Edit ConfigMap c", {"kind": "ConfigMap", "metadata": {"name": "c"}})
    qtbot.addWidget(dialog)
    asked = answer(monkeypatch, QMessageBox.No)
    with qtbot.waitSignal(dialog.rejected):
        dialog.reject()
    assert asked == []


def test_edit_in_form_leaves_without_a_question(qtbot, monkeypatch):
    dialog = EditDialog("Edit ConfigMap c", {"kind": "ConfigMap", "metadata": {"name": "c"}}, can_form=True)
    qtbot.addWidget(dialog)
    asked = answer(monkeypatch, QMessageBox.No)
    dialog.editor.setPlainText(dialog.editor.toPlainText() + "\n# changed\n")
    dialog._to_form()
    assert asked == [] and dialog.wants_form


def test_escape_in_review_changes_goes_back_to_editing(qtbot):
    dialog = ReviewDialog("Review ConfigMap c")
    qtbot.addWidget(dialog)
    dialog.reject()
    assert dialog.verdict == EDIT


def test_apply_yaml_refuses_to_close_while_the_server_answers(qtbot, monkeypatch):
    dialog = ApplyDialog("ctx", "shop")
    qtbot.addWidget(dialog)
    dialog.editor.setPlainText("kind: ConfigMap\nmetadata: {name: a}\n")
    dialog.set_busy(True, "Applying…")
    with qtbot.assertNotEmitted(dialog.rejected):
        dialog.reject()
    assert "Wait for the server" in dialog.result.toPlainText()


def test_apply_yaml_asks_before_dropping_unapplied_yaml(qtbot, monkeypatch):
    dialog = ApplyDialog("ctx", "shop")
    qtbot.addWidget(dialog)
    dialog.editor.setPlainText("kind: ConfigMap\nmetadata: {name: a}\n")
    asked = answer(monkeypatch, QMessageBox.No)
    with qtbot.assertNotEmitted(dialog.rejected):
        dialog.reject()
    assert asked


def test_enter_in_the_drain_timeout_box_does_not_start_the_drain(qtbot):
    dialog = DrainDialog("ctx", "n1")
    qtbot.addWidget(dialog)
    button = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok)
    assert not button.autoDefault() and not button.isDefault()


def test_renaming_a_group_onto_an_existing_one_asks_and_no_keeps_both(qtbot, monkeypatch):
    from klotse.ui.cluster_groups import ClusterGroups
    from klotse.ui.navigator import Navigator
    groups = ClusterGroups()
    groups.assign("a", "prod/eu")
    groups.assign("b", "prod/us")
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["a", "b"], groups=groups)
    asked = answer(monkeypatch, QMessageBox.No)
    assert nav.rename_group("prod/eu", "us") == "prod/eu"
    assert asked and groups.group_of("a") == "prod/eu" and groups.group_of("b") == "prod/us"
    answer(monkeypatch, QMessageBox.Yes)
    assert nav.rename_group("prod/eu", "us") == "prod/us"
    assert groups.group_of("a") == "prod/us"


def test_a_saved_filter_name_that_exists_is_reported_before_it_is_replaced(tmp_path):
    from PySide6.QtCore import QSettings
    from klotse.ui.saved_filters import SavedFilters
    saved = SavedFilters(QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat))
    saved.add("a", "Pod", "crashing", "CrashLoop")
    assert saved.exists("a", "Pod", "crashing") and not saved.exists("a", "Pod", "other")
    assert not saved.exists("b", "Pod", "crashing")  # per cluster and per list
