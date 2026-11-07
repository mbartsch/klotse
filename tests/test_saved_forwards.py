import sys
from types import SimpleNamespace

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog

from klotse.ui.customizations import LabelSettings
from klotse.ui.port_forward import PortForwardDialog, PortForwardPanel, PortForwards
from klotse.ui.saved_forwards import SavedForwards
from klotse.ui.workspace import ClusterWorkspace

ACTIVE = ("import time\nprint('Forwarding from 127.0.0.1:54321 -> 80', flush=True)\n"
          "for _ in range(600):\n    time.sleep(0.05)\n")


def store(tmp_path):
    return QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat)


def test_entries_are_kept_per_cluster_and_replace_the_same_target(tmp_path):
    saved = SavedForwards(store(tmp_path))
    saved.add(SavedForwards.make("a", "Service", "shop", "db", 5432, 15432))
    saved.add(SavedForwards.make("a", "Service", "shop", "db", 5432, 0))  # same target and port: replaced
    saved.add(SavedForwards.make("b", "Pod", "shop", "web-1", 80))
    again = SavedForwards(store(tmp_path))
    assert again.for_context("a") == [{"ctx": "a", "kind": "Service", "ns": "shop", "name": "db", "remote": 5432, "local": 0}]
    assert len(again.all()) == 2
    again.remove(again.for_context("b")[0])
    assert again.for_context("b") == []


def test_damaged_entries_are_skipped(tmp_path):
    s = store(tmp_path)
    s.setValue("saved_forwards/ctx.0", "a")
    s.setValue("saved_forwards/kind.0", "Secret")  # not forwardable
    s.setValue("saved_forwards/ns.0", "x")
    s.setValue("saved_forwards/name.0", "y")
    s.setValue("saved_forwards/remote.0", "80")
    s.setValue("saved_forwards/local.0", "0")
    s.setValue("saved_forwards/ctx.1", "a")
    s.setValue("saved_forwards/kind.1", "Pod")
    s.setValue("saved_forwards/ns.1", "x")
    s.setValue("saved_forwards/name.1", "y")
    s.setValue("saved_forwards/remote.1", "notaport")
    s.setValue("saved_forwards/local.1", "0")
    assert SavedForwards(s).all() == []


def make_ws(qtbot, tmp_path):
    ws = ClusterWorkspace(SimpleNamespace(), "ctx", label_settings=LabelSettings(store(tmp_path)))
    qtbot.addWidget(ws)
    ws.port_forwards.base = lambda context, kubeconfig: [sys.executable, "-u", "-c", ACTIVE]
    return ws


def test_remember_saves_the_forward_and_the_saved_menu_starts_it_again(qtbot, tmp_path, monkeypatch):
    ws = make_ws(qtbot, tmp_path)

    def exec_(self):
        self.result_ports = (0, 80)
        self.remember.setChecked(True)
        return QDialog.Accepted
    monkeypatch.setattr(PortForwardDialog, "exec", exec_)
    row = SimpleNamespace(name="web-1", namespace="shop", raw={})
    ws.start_port_forward("Pod", row)
    assert SavedForwards(ws.label_settings.store).for_context("ctx") == [
        {"ctx": "ctx", "kind": "Pod", "ns": "shop", "name": "web-1", "remote": 80, "local": 0}]
    panel = next(ws.log_tabs.widget(i) for i in range(ws.log_tabs.count()) if isinstance(ws.log_tabs.widget(i), PortForwardPanel))
    panel._fill_saved()
    texts = [a.text() for a in panel.saved_menu.actions()]
    assert texts[0] == "Start pod shop/web-1 port 80" and "Forget" in texts
    panel.saved_menu.actions()[0].trigger()
    assert len(ws.port_forwards.items) == 2
    panel.saved_menu.actions()[-1].menu().actions()[0].trigger()  # Forget
    assert SavedForwards(ws.label_settings.store).for_context("ctx") == []
    ws.shutdown()


def test_without_the_box_ticked_nothing_is_saved(qtbot, tmp_path, monkeypatch):
    ws = make_ws(qtbot, tmp_path)

    def exec_(self):
        self.result_ports = (0, 80)
        return QDialog.Accepted
    monkeypatch.setattr(PortForwardDialog, "exec", exec_)
    ws.start_port_forward("Pod", SimpleNamespace(name="web-1", namespace="shop", raw={}))
    assert SavedForwards(ws.label_settings.store).all() == []
    ws.shutdown()
