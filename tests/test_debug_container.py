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
