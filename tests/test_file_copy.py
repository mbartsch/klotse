"""File copy to and from a pod: `kubectl cp` processes started from a pod row, listed in a panel, cancelled on request or exit.
The tests run a small Python script in place of kubectl."""
import os
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from klotse.ui.file_copy import (Copy, FileCopies, FileCopyDialog, FileCopyPanel, build_args, human_size, local_size,
                                 valid_remote)
from klotse.ui.workspace import ClusterWorkspace

OK = "import sys\nprint('ignored output')\n"
SLOW = "import time\nfor _ in range(600):\n    time.sleep(0.05)\n"
FAILS = "import sys\nprint('tar: Removing leading `/` from member names', file=sys.stderr)\nprint('error: no such file', file=sys.stderr)\nsys.exit(1)\n"
WRITES = "import sys\nopen(sys.argv[-1], 'wb').write(b'x' * 2048)\n"


def fake(code):
    return lambda context, kubeconfig: [sys.executable, "-u", "-c", code, "--"]


@pytest.fixture
def copies(qtbot):
    made = []

    def make(code=OK):
        manager = FileCopies(base=fake(code))
        made.append(manager)
        return manager
    yield make
    for manager in made:
        manager.cancel_all()
        for c in manager.items:
            if c.process is not None:
                c.process.waitForFinished(3000)


def wait_for(qtbot, copy, state):
    qtbot.waitUntil(lambda: copy.state == state, timeout=8000)


# ---- helpers ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [("/var/log/a.log", "/var/log/a.log"), (" /tmp/dir/ ", "/tmp/dir"), ("rel/x", "rel/x")])
def test_valid_remote(text, expected):
    assert valid_remote(text) == expected


@pytest.mark.parametrize("text", ["", "   ", "/", "///", "/tmp/a\nb"])
def test_bad_remote_paths(text):
    with pytest.raises(ValueError):
        valid_remote(text)


def test_local_size_and_human_size(tmp_path):
    (tmp_path / "a").write_bytes(b"1" * 10)
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "b").write_bytes(b"2" * 5)
    assert local_size(str(tmp_path / "a")) == 10 and local_size(str(tmp_path)) == 15 and local_size(str(tmp_path / "none")) == 0
    assert human_size(512) == "512 B" and human_size(2048) == "2.0 KiB" and human_size(5 * 1024 ** 2) == "5.0 MiB"


def test_arguments_name_the_local_side_relative_and_never_as_a_flag_or_drive():
    up = Copy(1, "ctx", "shop", "web-1", "app", "upload", "C:\\data\\-rf", "/tmp/x")
    assert build_args(["kubectl", "--context", "ctx"], up) == ["kubectl", "--context", "ctx", "-n", "shop", "cp", "-c", "app",
                                                              "./-rf", "web-1:/tmp/x"]
    down = Copy(2, "ctx", "shop", "web-1", "", "download", "/home/me/out.log", "/var/log/a.log")
    assert build_args(["kubectl"], down) == ["kubectl", "-n", "shop", "cp", "web-1:/var/log/a.log", "./out.log"]


# ---- the manager -----------------------------------------------------------------------------------------------
def test_a_download_finishes_and_counts_what_arrived(qtbot, copies, tmp_path):
    manager = copies(WRITES)
    target = tmp_path / "out.bin"
    c = manager.start("ctx", "shop", "web-1", "app", "download", str(target), "/data/x")
    wait_for(qtbot, c, "done")
    assert c.bytes_local == 2048 and c.process.workingDirectory() == str(tmp_path) and not manager.active()


def test_an_upload_needs_a_local_source(copies, tmp_path):
    manager = copies()
    with pytest.raises(ValueError):
        manager.start("ctx", "shop", "web-1", "", "upload", str(tmp_path / "missing"), "/tmp/x")
    with pytest.raises(ValueError):
        manager.start("ctx", "shop", "web-1", "", "download", str(tmp_path / "nofolder" / "f"), "/tmp/x")
    with pytest.raises(ValueError):
        manager.start("ctx", "shop", "web-1", "", "download", str(tmp_path / "f"), "/")
    assert manager.items == []


def test_a_failure_shows_kubectls_own_words(qtbot, copies, tmp_path):
    manager = copies(FAILS)
    c = manager.start("ctx", "shop", "web-1", "", "download", str(tmp_path / "f"), "/nope")
    wait_for(qtbot, c, "failed")
    assert c.message == "error: no such file"  # the harmless tar notice is not mistaken for the cause


def test_cancel_kills_the_process(qtbot, copies, tmp_path):
    manager = copies(SLOW)
    c = manager.start("ctx", "shop", "web-1", "", "download", str(tmp_path / "f"), "/x")
    assert c.running
    manager.cancel(c)
    wait_for(qtbot, c, "cancelled")
    assert not manager.active() and c.message == ""


def test_missing_kubectl_is_explained(qtbot, tmp_path):
    manager = FileCopies(base=lambda context, kubeconfig: [str(tmp_path / "no-such-kubectl")])
    c = manager.start("ctx", "shop", "web-1", "", "download", str(tmp_path / "f"), "/x")
    wait_for(qtbot, c, "failed")
    assert "Cannot start kubectl" in c.message


# ---- panel and dialog ----------------------------------------------------------------------------------------
def test_the_panel_lists_copies_and_cancels(qtbot, copies, tmp_path):
    manager = copies(SLOW)
    panel = FileCopyPanel(manager)
    qtbot.addWidget(panel)
    assert panel.table.rowCount() == 0 and not panel.cancel_all_button.isEnabled()
    c = manager.start("ctx", "shop", "web-1", "", "download", str(tmp_path / "f"), "/x")
    assert panel.table.rowCount() == 1 and "Copying" in panel.table.item(0, 4).text()
    panel.table.selectRow(0)
    assert panel.cancel_button.isEnabled()
    panel.cancel_button.click()
    wait_for(qtbot, c, "cancelled")
    assert panel.clear_button.isEnabled()
    panel.clear_button.click()
    assert panel.table.rowCount() == 0


def test_the_dialog_validates_and_reports_values(qtbot):
    d = FileCopyDialog("web-1", "shop", ["app", "side"])
    qtbot.addWidget(d)
    d._accept()
    assert d.result() != QDialog.Accepted and not d.note.isHidden()
    d.remote.setText("/data/")
    d.local.setText("C:/tmp/x")
    d.container.setCurrentIndex(1)
    d.upload.setChecked(True)
    d._accept()
    assert d.result() == QDialog.Accepted and d.result_values == ("upload", "side", "C:/tmp/x", "/data")


# ---- the workspace ---------------------------------------------------------------------------------------------
def row():
    raw = {"spec": {"containers": [{"name": "app"}], "initContainers": [{"name": "init"}]}}
    return SimpleNamespace(name="web-1", namespace="shop", raw=raw)


def make_ws(qtbot, code=SLOW):
    ws = ClusterWorkspace(SimpleNamespace(), "ctx")
    qtbot.addWidget(ws)
    ws.file_copies.base = fake(code)
    return ws


def accept_with(monkeypatch, values):
    def exec_(self):
        self.result_values = values
        return QDialog.Accepted
    monkeypatch.setattr(FileCopyDialog, "exec", exec_)


def test_the_dialog_offers_only_regular_containers(qtbot):
    ws = make_ws(qtbot)
    d = ws.file_copy_dialog(row())
    qtbot.addWidget(d)
    assert [d.container.itemText(i) for i in range(d.container.count())] == ["app"]


def test_a_download_from_the_row_menu_signal_shows_the_panel(qtbot, monkeypatch, tmp_path):
    ws = make_ws(qtbot)
    accept_with(monkeypatch, ("download", "app", str(tmp_path / "out"), "/x"))
    ws.pod_table.copy_files_requested.emit(row())
    [c] = ws.file_copies.items
    assert [ws.log_tabs.tabText(i) for i in range(ws.log_tabs.count())] == ["File copies"]
    ws.copy_files(row())  # a second copy reuses the tab
    assert ws.log_tabs.count() == 1 and len(ws.file_copies.items) == 2
    ws.shutdown()
    wait_for(qtbot, c, "cancelled")


def test_a_read_only_cluster_allows_download_but_not_upload(qtbot, monkeypatch, tmp_path):
    ws = make_ws(qtbot)
    ws.set_read_only(True)
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a))
    src = tmp_path / "up.txt"
    src.write_text("hi")
    accept_with(monkeypatch, ("upload", "app", str(src), "/x"))
    assert ws.copy_files(row()) is None and shown and ws.file_copies.items == []
    accept_with(monkeypatch, ("download", "app", str(tmp_path / "down"), "/x"))
    c = ws.copy_files(row())
    assert c is not None
    ws.shutdown()
    wait_for(qtbot, c, "cancelled")


def test_upload_and_overwrite_ask_first_and_default_to_no(qtbot, monkeypatch, tmp_path):
    ws = make_ws(qtbot)
    src = tmp_path / "up.txt"
    src.write_text("hi")
    asked = []

    def no(parent, title, text, buttons, default):
        asked.append((title, default))
        return QMessageBox.No
    monkeypatch.setattr(QMessageBox, "question", no)
    accept_with(monkeypatch, ("upload", "app", str(src), "/x"))
    assert ws.copy_files(row()) is None
    accept_with(monkeypatch, ("download", "app", str(src), "/x"))  # the local file exists already
    assert ws.copy_files(row()) is None
    assert asked == [("Upload to container", QMessageBox.No), ("Replace local file", QMessageBox.No)] and ws.file_copies.items == []


def test_the_pod_tab_menu_has_copy_files(qtbot):
    from klotse.ui.pod_view import PodActionBar
    bar = PodActionBar()
    qtbot.addWidget(bar)
    got = []
    bar.action_requested.connect(got.append)
    next(a for a in bar.items if a.text().startswith("Copy files")).trigger()
    assert got == ["copy-files"]
