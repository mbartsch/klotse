import subprocess
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QDialog, QMessageBox

from klotse.k8s import remote_files as rf
from klotse.ui.file_browser import FileBrowserDialog
from klotse.ui.file_copy import FileCopyDialog
from klotse.ui.workspace import ClusterWorkspace


# ---- the pure side -------------------------------------------------------------------------------------------------
def test_paths_must_be_absolute_and_are_normalised():
    assert rf.clean_path("/var//log/../lib/") == "/var/lib" and rf.clean_path("/") == "/"
    for bad in ("", "var/log", "/a\nb"):
        with pytest.raises(ValueError):
            rf.clean_path(bad)
    assert rf.parent("/var/log/app.log") == "/var/log" and rf.parent("/var") == "/" and rf.parent("/") == "/"
    assert rf.join("/var", "log") == "/var/log"


def test_the_path_is_one_argument_after_double_dash_never_a_shell_line():
    base = ["kubectl", "--context", "ctx"]
    hostile = "/data; rm -rf x $(id)"
    argv = rf.list_argv(base, "shop", "web-1", "app", hostile)
    assert argv[:len(base)] == base and argv[-4:-1] == ["ls", "-1AF", "--"] and argv[-1] == hostile + "/"  # one element, as typed
    assert argv.index("--") < argv.index("ls")  # kubectl stops reading options before the remote command
    plain = rf.list_argv(base, "n", "p", None, "/")
    assert plain[-1] == "/" and "-c" not in plain


def test_parse_listing_sorts_folders_first_and_strips_the_markers():
    entries, cut = rf.parse_listing("b.txt\nzeta/\nrun.sh*\nlink@\nsock=\nfifo|\n.hidden\nAlpha/\n")
    assert cut is False
    # folders and links (a link may lead to a folder) first, then files, each by name
    assert entries == [("Alpha", rf.DIR), ("link", rf.LINK), ("zeta", rf.DIR), (".hidden", rf.FILE), ("b.txt", rf.FILE),
                       ("fifo", rf.FILE), ("run.sh", rf.FILE), ("sock", rf.FILE)]


def test_a_huge_directory_is_cut_and_says_so():
    entries, cut = rf.parse_listing("\n".join(f"f{i}" for i in range(rf.LIMIT + 5)))
    assert len(entries) == rf.LIMIT and cut is True


def test_failures_are_explained():
    assert "no ls command" in rf.explain_failure('OCI runtime exec failed: exec: "ls": executable file not found in $PATH')
    assert rf.explain_failure("ls: /x: No such file or directory") == "No such directory in the container."
    assert "file, not a directory" in rf.explain_failure("ls: /x: Not a directory")
    assert rf.explain_failure("Error from server (Forbidden): pods is forbidden").startswith("Permission denied")


def test_list_directory_runs_kubectl_and_reports_problems():
    seen = []

    kwargs_seen = []

    def run(argv, **kw):
        seen.append(argv)
        kwargs_seen.append(kw)
        return SimpleNamespace(returncode=0, stdout="a/\nb\n", stderr="")
    assert rf.list_directory(["kubectl"], "ns", "pod", "c", "/etc", run=run) == ([("a", rf.DIR), ("b", rf.FILE)], False)
    assert seen[0][-1] == "/etc/" and "--" in seen[0]
    assert kwargs_seen[0]["stdin"] is subprocess.DEVNULL  # pythonw has no console: an inherited stdin gives WinError 6
    with pytest.raises(rf.RemoteError, match="absolute"):
        rf.list_directory(["kubectl"], "ns", "pod", "c", "etc", run=run)
    failing = lambda argv, **kw: SimpleNamespace(returncode=1, stdout="", stderr="ls: /x: No such file or directory")
    with pytest.raises(rf.RemoteError, match="No such directory"):
        rf.list_directory(["kubectl"], "ns", "pod", "c", "/x", run=failing)

    def missing(argv, **kw):
        raise FileNotFoundError
    with pytest.raises(rf.RemoteError, match="kubectl was not found"):
        rf.list_directory(["kubectl"], "ns", "pod", "c", "/", run=missing)

    def broken(argv, **kw):
        raise OSError(6, "The handle is invalid")
    with pytest.raises(rf.RemoteError, match="Could not run kubectl: .*handle is invalid"):
        rf.list_directory(["kubectl"], "ns", "pod", "c", "/", run=broken)


def test_claim_users_finds_running_pods_that_mount_the_claim():
    def pod(name, phase, claim):
        return {"metadata": {"name": name}, "status": {"phase": phase},
                "spec": {"volumes": [{"name": "d", "persistentVolumeClaim": {"claimName": claim}}, {"name": "x", "emptyDir": {}}],
                         "containers": [{"name": "app", "volumeMounts": [{"name": "d", "mountPath": "/data"}, {"name": "x", "mountPath": "/tmp"}]},
                                        {"name": "side"}]}}
    pods = [pod("a", "Running", "pvc1"), pod("b", "Pending", "pvc1"), pod("c", "Running", "other")]
    assert rf.claim_users(pods, "pvc1") == [("a", "app", "/data")]
    assert rf.claim_users(pods, "nobody") == []


# ---- the dialog ----------------------------------------------------------------------------------------------------
TREE = {"/": [("etc", rf.DIR), ("hello.txt", rf.FILE)], "/etc": [("hosts", rf.FILE), ("ssl", rf.DIR)], "/etc/ssl": []}


def lister(calls=None):
    def run(container, path, ok, bad):
        if calls is not None:
            calls.append((container, path))
        if path in TREE:
            ok(TREE[path], False)
        else:
            bad("No such directory in the container.")
    return run


def test_navigation_up_and_errors(qtbot):
    calls = []
    dialog = FileBrowserDialog("t", ["app", "side"], "/", lister(calls))
    qtbot.addWidget(dialog)
    assert dialog.table.item(0, 0).text() == "etc/" and dialog.table.item(1, 0).text() == "hello.txt"
    dialog._enter(0)
    assert dialog.path == "/etc" and dialog.address.text() == "/etc"
    dialog.up.click()
    assert dialog.path == "/"
    dialog.address.setText("/nope")
    dialog.go.click()
    assert dialog.path == "/" and "No such directory" in dialog.status.text()
    dialog.address.setText("relative")
    dialog.go.click()
    assert "absolute" in dialog.status.text()
    dialog.container.setCurrentText("side")
    assert calls[-1] == ("side", "/")  # changing container lists again


def test_a_late_answer_to_an_older_request_is_ignored(qtbot):
    pending = []
    dialog = FileBrowserDialog("t", ["app"], "/", lambda c, p, ok, bad: pending.append((p, ok)))
    qtbot.addWidget(dialog)
    dialog.open_path("/etc")
    first, second = pending[0], pending[1]
    second[1]([("new", rf.FILE)], False)
    first[1]([("old", rf.FILE)], False)  # arrives late
    assert dialog.table.item(0, 0).text() == "new" and dialog.path == "/etc"


def test_choose_mode_returns_the_selected_entry_or_the_folder(qtbot):
    dialog = FileBrowserDialog("t", ["app"], "/etc", lister(), choose=True)
    qtbot.addWidget(dialog)
    dialog.table.selectRow(0)
    assert dialog.selected_path() == "/etc/hosts"
    dialog.table.clearSelection()
    assert dialog.selected_path() == "/etc"
    dialog.table.selectRow(1)
    dialog._choose()
    assert dialog.chosen == ("app", "/etc/ssl") and dialog.result() == QDialog.Accepted


def test_browse_mode_has_copy_path_and_download(qtbot):
    dialog = FileBrowserDialog("t", ["app"], "/etc", lister())
    qtbot.addWidget(dialog)
    got = []
    dialog.download_requested.connect(lambda c, p: got.append((c, p)))
    dialog.table.selectRow(0)
    dialog.download_button.click()
    assert got == [("app", "/etc/hosts")] and dialog.select_button is None


# ---- inside the workspace and Copy files ---------------------------------------------------------------------------
def row():
    raw = {"spec": {"containers": [{"name": "app"}]}, "status": {"containerStatuses": [], "phase": "Running"}, "metadata": {}}
    return SimpleNamespace(name="web-1", namespace="shop", raw=raw)


def make_ws(qtbot, **service):
    ws = ClusterWorkspace(SimpleNamespace(**service), "ctx")
    qtbot.addWidget(ws)
    return ws


def test_the_browse_button_fills_the_path_and_typing_still_works(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    monkeypatch.setattr(FileBrowserDialog, "exec", lambda self: (setattr(self, "chosen", ("app", "/etc/hosts")), QDialog.Accepted)[1])
    dialog = ws.file_copy_dialog(row())
    qtbot.addWidget(dialog)
    assert dialog.browse_remote.isVisibleTo(dialog)
    dialog.browse_remote.click()
    assert dialog.remote.text() == "/etc/hosts"
    dialog.remote.setText("/typed/by/hand")  # the field stays an ordinary text box
    dialog.local.setText("C:/tmp/x")
    assert dialog.values()[3] == "/typed/by/hand"
    plain = FileCopyDialog("p", "n", ["app"])
    qtbot.addWidget(plain)
    assert not plain.browse_remote.isVisibleTo(plain)  # without a browser the button is not offered


def test_the_browser_starts_in_the_folder_of_what_was_typed(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    starts = []
    monkeypatch.setattr(FileBrowserDialog, "exec", lambda self: (starts.append(self.address.text()), QDialog.Rejected)[1])
    assert ws._choose_remote(row(), "app", "/var/log/app.log") is None
    ws._choose_remote(row(), "app", "/var/log/")
    ws._choose_remote(row(), "app", "")
    assert starts == ["/var/log", "/var/log/", "/"]


def test_browse_files_menu_action_opens_the_browser_and_download_hands_over_the_path(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    opened = []

    def exec_(self):
        opened.append(self.windowTitle())
        self.download_requested.emit("app", "/etc/hosts")
        return QDialog.Accepted
    monkeypatch.setattr(FileBrowserDialog, "exec", exec_)
    handed = []
    monkeypatch.setattr(ws, "copy_files", lambda r, container=None, remote="": handed.append((container, remote)))
    ws.pod_action("browse-files", row())
    assert opened == ["Files: shop/web-1"] and handed == [("app", "/etc/hosts")]


def test_browsing_works_on_a_read_only_cluster(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    opened = []
    monkeypatch.setattr(FileBrowserDialog, "exec", lambda self: opened.append(1) or QDialog.Rejected)
    ws.set_read_only(True)
    ws.pod_action("browse-files", row())
    assert opened == [1]


def test_a_claim_nobody_uses_is_explained_and_nothing_is_started(qtbot, monkeypatch):
    told = []
    ws = make_ws(qtbot, list_pods=lambda ns: [])
    monkeypatch.setattr(QMessageBox, "information", lambda *a: told.append(a[2]))
    ws.browse_claim(SimpleNamespace(name="data", namespace="shop"))
    qtbot.waitUntil(lambda: bool(told), timeout=5000)
    assert "No running pod" in told[0] and "does not start a pod" in told[0]
    qtbot.waitUntil(lambda: not ws._workers)


def test_a_claim_in_use_opens_the_browser_at_its_mount(qtbot, monkeypatch):
    pod = SimpleNamespace(raw={"metadata": {"name": "db-0"}, "status": {"phase": "Running"},
                               "spec": {"volumes": [{"name": "d", "persistentVolumeClaim": {"claimName": "data"}}],
                                        "containers": [{"name": "pg", "volumeMounts": [{"name": "d", "mountPath": "/var/lib/pg"}]}]}})
    ws = make_ws(qtbot, list_pods=lambda ns: [pod])
    seen = []
    monkeypatch.setattr(FileBrowserDialog, "exec", lambda self: (seen.append((self.windowTitle(), self.address.text())), QDialog.Rejected)[1])
    ws.browse_claim(SimpleNamespace(name="data", namespace="shop"))
    qtbot.waitUntil(lambda: bool(seen), timeout=5000)
    assert seen == [("Files of claim shop/data (via db-0)", "/var/lib/pg")]
    qtbot.waitUntil(lambda: not ws._workers)


def test_pods_and_claims_have_the_menu_entry(qtbot):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from klotse.ui.resource_table import ResourceTable
    for kind in ("Pod", "PersistentVolumeClaim", "Service"):
        table = ResourceTable([("Name", "name")], kind)
        qtbot.addWidget(table)
        table.set_rows([SimpleNamespace(name="x", namespace="n", raw={"spec": {}, "metadata": {}})])
        seen = []

        def read():
            menu = QApplication.activePopupWidget()
            seen.extend(a.text() for a in menu.actions())
            menu.close()
        QTimer.singleShot(200, read)
        table.view.selectRow(0)
        table._context_menu(table.view.visualRect(table.proxy.index(0, 0)).center())
        assert ("Browse files…" in seen) is (kind in ("Pod", "PersistentVolumeClaim"))
