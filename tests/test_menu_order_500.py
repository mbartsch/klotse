"""#500: in a Details "More…" menu the dangerous entries come last; the rest keep their order."""
from types import SimpleNamespace as NS

from PySide6.QtWidgets import QToolButton

from klotse.ui.detail_panel import DetailPanel


def action(title, dangerous=False, key=None):
    return NS(title=title, callback=lambda: None, dangerous=dangerous, primary=False, key=key or title)


def test_force_terminate_comes_after_the_file_and_port_entries(qtbot):
    panel = DetailPanel()
    qtbot.addWidget(panel)
    panel.set_object_actions([action("Edit…"), action("Force terminate…", dangerous=True), action("Browse files…"),
                              action("Copy files…"), action("Port forward…"), action("Terminate…", dangerous=True)])
    menu = next(b.menu() for b in panel.action_bar.findChildren(QToolButton) if b.menu() is not None)
    titles = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert titles.index("Browse files…") < titles.index("Force terminate…")
    assert titles.index("Port forward…") < titles.index("Terminate…")
    assert titles[-2:] == ["Force terminate…", "Terminate…"]
