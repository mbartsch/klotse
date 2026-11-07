from types import SimpleNamespace

from PySide6.QtWidgets import QDialog

from klotse.k8s.permissions import ACTIONS
from klotse.ui import workspace as module
from klotse.ui.node_dialogs import DebugDialog
from klotse.ui.terminal import debug_args
from klotse.ui.workspace import ClusterWorkspace


def test_debug_args_name_context_pod_image_and_target():
    argv = debug_args("ctx", "shop", "web-1", "busybox", target="app", kubeconfig="/k")
    assert argv == ["kubectl", "--kubeconfig", "/k", "--context", "ctx", "-n", "shop", "debug", "-it", "web-1",
                    "--image=busybox", "--target=app", "--", "sh"]
    plain = debug_args("ctx", "shop", "web-1", "busybox")
    assert "--target=app" not in plain and plain[-2:] == ["--", "sh"] and "--context" in plain
    assert "--container=dbg" in debug_args("ctx", "shop", "web-1", "busybox", name="dbg")


def test_dialog_offers_the_containers_and_returns_the_choice(qtbot):
    dialog = DebugDialog("ctx", "shop/web-1", ["app", "sidecar"])
    qtbot.addWidget(dialog)
    assert dialog.values() == ("docker.io/library/busybox", None)
    dialog.target.setCurrentText("sidecar")
    dialog.image.setText(" nicolaka/netshoot ")
    assert dialog.values() == ("nicolaka/netshoot", "sidecar")
    dialog.image.setText("")
    assert not dialog.open_button.isEnabled()


def test_debug_action_opens_a_terminal_with_kubectl_debug(qtbot, monkeypatch):
    ws = ClusterWorkspace(SimpleNamespace(), "ctx")
    qtbot.addWidget(ws)
    opened = []
    monkeypatch.setattr(ws, "_launch_terminal", lambda argv, message, title=None: opened.append((argv, message)))
    monkeypatch.setattr(DebugDialog, "exec", lambda self: QDialog.Accepted)
    row = SimpleNamespace(name="web-1", namespace="shop", raw={"spec": {"containers": [{"name": "app"}]}, "metadata": {}})
    ws.pod_action("debug", row)
    argv, message = opened[0]
    assert argv[argv.index("-n"):argv.index("-n") + 5] == ["-n", "shop", "debug", "-it", "web-1"]
    assert "--image=docker.io/library/busybox" in argv and argv[-2:] == ["--", "sh"] and message == "Opened debug: web-1"


def test_cancelling_the_dialog_starts_nothing(qtbot, monkeypatch):
    ws = ClusterWorkspace(SimpleNamespace(), "ctx")
    qtbot.addWidget(ws)
    opened = []
    monkeypatch.setattr(ws, "_launch_terminal", lambda *a, **k: opened.append(a))
    monkeypatch.setattr(DebugDialog, "exec", lambda self: QDialog.Rejected)
    ws.pod_action("debug", SimpleNamespace(name="w", namespace="n", raw={"spec": {"containers": [{"name": "a"}]}, "metadata": {}}))
    assert opened == []


def test_permission_hint_maps_debug_to_the_ephemeralcontainers_subresource():
    assert ACTIONS["debug"] == ("patch", "ephemeralcontainers")


def _pod(owned=True, debug=True):
    meta = {"name": "web-1", "namespace": "shop"}
    if owned:
        meta["ownerReferences"] = [{"kind": "ReplicaSet", "name": "web", "controller": True}]
    spec = {"containers": [{"name": "app"}]}
    if debug:
        spec["ephemeralContainers"] = [{"name": "debugger-x"}]
    return SimpleNamespace(name="web-1", namespace="shop", raw={"metadata": meta, "spec": spec})


def test_removing_debug_containers_replaces_a_controlled_pod_after_asking(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    deleted = []
    ws = ClusterWorkspace(SimpleNamespace(delete_pod=lambda *a: deleted.append(a)), "ctx")
    qtbot.addWidget(ws)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.No)
    ws.pod_action("remove-debug", _pod())
    assert deleted == []
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    ws.pod_action("remove-debug", _pod())
    qtbot.waitUntil(lambda: bool(deleted))
    assert deleted == [("web-1", "shop")]
    qtbot.waitUntil(lambda: not ws._workers)


def test_a_pod_without_a_controller_is_never_deleted_for_this(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    deleted, told = [], []
    ws = ClusterWorkspace(SimpleNamespace(delete_pod=lambda *a: deleted.append(a)), "ctx")
    qtbot.addWidget(ws)
    monkeypatch.setattr(QMessageBox, "information", lambda *a: told.append(a[2]))
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)
    ws.pod_action("remove-debug", _pod(owned=False))
    assert deleted == [] and "no controller" in told[0]


def test_menu_entry_only_for_pods_with_debug_containers(qtbot):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from klotse.ui.resource_table import ResourceTable
    for debug, expected in ((True, True), (False, False)):
        table = ResourceTable([("Name", "name")], "Pod")
        qtbot.addWidget(table)
        row = _pod(debug=debug)
        table.set_rows([row])
        seen = []

        def read():
            menu = QApplication.activePopupWidget()
            seen.extend(a.text() for a in menu.actions())
            menu.close()
        QTimer.singleShot(200, read)
        table.view.selectRow(0)
        table._context_menu(table.view.visualRect(table.proxy.index(0, 0)).center())
        assert any(t.startswith("Remove debug containers") for t in seen) is expected


def test_dialog_explains_what_sharing_gives(qtbot):
    dialog = DebugDialog("ctx", "shop/web-1", ["app"])
    qtbot.addWidget(dialog)
    text = dialog.sharing.text()
    assert "/proc/<pid>/root/" in text and "Other pods are separate" in text
