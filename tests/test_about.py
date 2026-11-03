import tomllib
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog

import klotse
from klotse.ui import about, main_window as main_module
from klotse.ui.main_window import MainWindow

from test_app import FakeService


def test_dialog_shows_project_version_url_license_and_runtime(qtbot):
    dialog = about.AboutDialog()
    qtbot.addWidget(dialog)
    text = dialog.info.text()
    assert dialog.windowTitle() == "About Klotse"
    assert about.REPO_URL in text and f"href='{about.REPO_URL}'" in text
    assert about.AUTHOR in text and "Python" in text and "Qt" in text and "kubernetes client" in text
    assert about.license_text() in text and dialog.info.openExternalLinks()


def test_license_is_apache_2_and_consistent_everywhere():
    root = Path(__file__).resolve().parent.parent
    text = (root / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in text and "Version 2.0, January 2004" in text
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["license"] == "Apache-2.0" and pyproject["project"]["license-files"] == ["LICENSE"]
    assert about.LICENSE == "Apache License 2.0" and "License: Apache License 2.0" in about.details()
    assert "Apache License 2.0" in (root / "README.md").read_text(encoding="utf-8")


def test_dialog_links_the_license_and_carries_the_trademark_note(qtbot):
    from PySide6.QtWidgets import QLabel

    dialog = about.AboutDialog()
    qtbot.addWidget(dialog)
    assert f"href='{about.LICENSE_URL}'" in dialog.info.text()
    notes = [label.text() for label in dialog.findChildren(QLabel) if "registered trademark" in label.text()]
    assert notes == [about.TRADEMARK_NOTE] and "not affiliated with or endorsed" in notes[0]


def test_missing_license_name_is_stated_not_invented(monkeypatch):
    monkeypatch.setattr(about, "LICENSE", None)
    assert about.license_text() == "Not specified yet" and "Not specified yet" in about.license_html()
    monkeypatch.setattr(about, "LICENSE", "MIT")
    assert about.license_text() == "MIT" and "License: MIT" in about.details()


def test_version_has_a_single_source_shared_with_pyproject():
    pyproject = tomllib.loads((Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" in pyproject["project"]["dynamic"] and "version" not in pyproject["project"]
    assert pyproject["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "klotse.__version__"}
    assert klotse.__version__ and f"Klotse {klotse.__version__}" in about.details()


def test_dialog_shows_the_logo_from_assets_when_present(qtbot, tmp_path, monkeypatch):
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QLabel

    from klotse.ui import branding

    def logo_labels(dialog):
        return [label for label in dialog.findChildren(QLabel) if not label.pixmap().isNull()]

    monkeypatch.setattr(branding, "ASSETS", tmp_path)
    plain = about.AboutDialog()
    qtbot.addWidget(plain)
    assert logo_labels(plain) == []  # no logo file: the dialog still works
    image = QImage(1408, 768, QImage.Format_RGB32)
    image.fill(QColor("#2255bb"))
    assert image.save(str(tmp_path / "klotse_logo.jpg"), "JPG")
    dialog = about.AboutDialog()
    qtbot.addWidget(dialog)
    (label,) = logo_labels(dialog)
    assert (label.pixmap().width(), label.pixmap().height()) == (about.LOGO_SIZE, about.LOGO_SIZE)
    dialog.show()
    qtbot.waitExposed(dialog)
    assert label.height() >= about.LOGO_SIZE and label.width() >= about.LOGO_SIZE  # the logo is never clipped


def test_copy_button_puts_the_details_on_the_clipboard(qtbot):
    dialog = about.AboutDialog()
    qtbot.addWidget(dialog)
    dialog.copy_button.click()
    copied = QGuiApplication.clipboard().text()
    assert copied == about.details() and about.REPO_URL in copied and "Platform:" in copied


def test_help_menu_opens_the_about_dialog(qtbot, monkeypatch):
    shown = []

    class FakeDialog:
        def __init__(self, parent):
            shown.append(parent)

        def exec(self):
            return QDialog.Accepted

        def deleteLater(self):
            pass

    monkeypatch.setattr(main_module, "AboutDialog", FakeDialog)
    win = MainWindow(FakeService)
    qtbot.addWidget(win)
    help_menu = next(a for a in win.menuBar().actions() if a.text() == "Help").menu()
    assert win.about_action in help_menu.actions() and win.about_action.text() == "About Klotse"
    win.about_action.trigger()
    assert shown == [win]
