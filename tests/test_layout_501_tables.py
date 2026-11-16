"""#501: the port forward and file copy tables fit their columns once, when rows first arrive; later updates keep the
widths the user has set (they used to reset them on every update, once a second during a download)."""
from klotse.ui.file_copy import FileCopyPanel
from klotse.ui.port_forward import PortForwardPanel
from test_file_copy import SLOW as COPY_SLOW
from test_file_copy import copies  # noqa: F401  (fixture)
from test_port_forward import ACTIVE, forwards  # noqa: F401  (fixture)


def test_a_port_forward_column_width_survives_later_updates(qtbot, forwards, monkeypatch):  # noqa: F811
    manager = forwards(ACTIVE)
    panel = PortForwardPanel(manager)
    qtbot.addWidget(panel)
    manager.start("ctx", "shop", "Pod", "web-1", 80)
    panel.table.setColumnWidth(0, 333)
    manager.start("ctx", "shop", "Pod", "web-2", 81)  # a second update of the table
    assert panel.table.columnWidth(0) == 333


def test_a_file_copy_column_width_survives_later_updates(qtbot, copies, tmp_path):  # noqa: F811
    manager = copies(COPY_SLOW)
    panel = FileCopyPanel(manager)
    qtbot.addWidget(panel)
    manager.start("ctx", "shop", "web-1", "", "download", str(tmp_path / "a"), "/x")
    panel.table.setColumnWidth(0, 333)
    manager.start("ctx", "shop", "web-1", "", "download", str(tmp_path / "b"), "/y")
    assert panel.table.columnWidth(0) == 333
