"""#498: state and read-only are words as well as colour; the log filter says what is wrong; the kubeconfig path can be typed."""
from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from klotse.ui.navigator import Navigator


def text_of(nav, name):
    return nav._clusters[name].text(0)


def test_each_connection_state_has_its_own_mark_not_only_its_colour(qtbot):
    from klotse.ui.navigator import STATE_SHAPES, dot_icon, STATE_COLORS
    shapes = set(STATE_SHAPES.values())
    assert len(shapes) == 4  # four states, four shapes
    images = {dot_icon(STATE_COLORS["idle"], shape).pixmap(12, 12).toImage().bits().tobytes() for shape in shapes}
    assert len(images) == 4  # and each shape draws differently


def test_a_read_only_cluster_has_a_lock_and_the_label_stays_compact(qtbot):
    nav = Navigator()
    qtbot.addWidget(nav)
    nav.set_contexts(["prod"])
    nav.set_read_only("prod", True)
    assert text_of(nav, "prod").endswith("🔒")
    nav.set_read_only("prod", False)
    assert "🔒" not in text_of(nav, "prod")


def test_the_rbac_highlight_has_a_sign_as_well_as_colour():
    from klotse.ui import rbac_view
    from klotse.ui import theme
    html = rbac_view._list(["delete", "get"], warn=lambda v: v == "delete")
    assert "⚠ delete" in html and theme.WARN in html


def test_an_invalid_log_filter_says_so_in_words(qtbot, monkeypatch):
    from klotse.ui import multi_log
    view = multi_log.MultiLogView.__new__(multi_log.MultiLogView)
    # the regex box only needs its text and style; build the minimum the check uses
    from PySide6.QtWidgets import QLabel, QLineEdit, QCheckBox
    view.match = QLineEdit("(unclosed")
    view.drop = QLineEdit("")
    view.ignore_case = QCheckBox()
    view.status = QLabel("")
    qtbot.addWidget(view.match)
    view._compile()
    assert view.status.text().startswith("Filter not valid")


def test_the_kubeconfig_path_can_be_typed_and_a_missing_file_is_refused(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QDialog, QLineEdit
    from klotse.ui.preferences import PreferencesDialog
    real = tmp_path / "config"
    real.write_text("apiVersion: v1\n")
    stub = PreferencesDialog.__new__(PreferencesDialog)  # accept() reads only the path field
    stub.path = QLineEdit(str(tmp_path / "missing"))
    warned, accepted = [], []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    monkeypatch.setattr(QDialog, "accept", lambda self: accepted.append(True))
    PreferencesDialog.accept(stub)
    assert warned and not accepted  # a missing file is refused, and the dialog stays open
    stub.path.setText(str(real))
    PreferencesDialog.accept(stub)
    assert accepted == [True]


def test_the_command_palette_scan_hides_each_menu_again(qtbot, tmp_path):
    # The Edit menu turns its shortcuts back on when it is hidden; the palette opens menus to read them, so it must hide them too.
    from test_palette import window
    win = window(qtbot, tmp_path)
    hidden = []
    win.edit_menu.aboutToHide.connect(lambda: hidden.append(True))
    win.palette_entries()
    assert hidden
