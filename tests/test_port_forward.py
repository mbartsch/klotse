"""Port-forward: kubectl port-forward processes started from a pod or service row, listed in a panel, stopped on request or exit.
The tests run a small Python script in place of kubectl that prints what kubectl prints."""
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog

from klotse.ui.port_forward import (Forward, PortForwardDialog, PortForwardPanel, PortForwards, forward_ports, valid_port)
from klotse.ui.workspace import ClusterWorkspace

ACTIVE = ("import time\nprint('Forwarding from 127.0.0.1:54321 -> 80', flush=True)\n"
          "print('Forwarding from [::1]:54321 -> 80', flush=True)\n"
          "for _ in range(600):\n    time.sleep(0.05)\n")
CONNECTIONS = ("import time\nprint('Forwarding from 127.0.0.1:54321 -> 80', flush=True)\n"
               "print('Handling connection for 54321', flush=True)\nprint('Handling connection for 54321', flush=True)\n"
               "for _ in range(600):\n    time.sleep(0.05)\n")
FAILS = "import sys\nprint('error: unable to listen on any of the requested ports: [{8080 80}]', file=sys.stderr, flush=True)\nsys.exit(1)\n"
EXITS_CLEANLY = "print('Forwarding from 127.0.0.1:54321 -> 80', flush=True)\n"


def fake(code):
    return lambda context, kubeconfig: [sys.executable, "-u", "-c", code]


@pytest.fixture
def forwards(qtbot):
    made = []

    def make(code=ACTIVE):
        manager = PortForwards(base=fake(code))
        made.append(manager)
        return manager
    yield make
    for manager in made:  # no fake kubectl may outlive its test
        manager.stop_all()
        for fw in manager.items:
            if fw.process is not None:
                fw.process.waitForFinished(3000)


def wait_for(qtbot, fw, state):
    qtbot.waitUntil(lambda: fw.state == state, timeout=8000)


# ---- small helpers -----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [("80", 80), (" 8080 ", 8080), ("65535", 65535), (443, 443)])
def test_valid_ports(text, expected):
    assert valid_port(text) == expected


@pytest.mark.parametrize("text", ["", "0", "65536", "-1", "http", "80.5", "8 0"])
def test_invalid_ports_are_explained(text):
    with pytest.raises(ValueError, match="not a port number"):
        valid_port(text)


def test_an_empty_local_port_means_a_free_one_when_allowed():
    assert valid_port("", allow_empty=True) == 0 and valid_port("  ", allow_empty=True) == 0


def test_a_pods_and_a_services_ports_are_offered_once_each():
    pod = {"spec": {"containers": [{"name": "app", "ports": [{"name": "http", "containerPort": 80, "protocol": "TCP"},
                                                              {"containerPort": 9090}]},
                                   {"name": "side", "ports": [{"containerPort": 80}]}]}}
    assert forward_ports("Pod", pod) == [("http (80/TCP)", 80), ("app (9090/TCP)", 9090)]
    service = {"spec": {"ports": [{"name": "web", "port": 80, "targetPort": "http"}, {"port": 443, "targetPort": 8443}, {"port": 80}]}}
    assert forward_ports("Service", service) == [("web (80 → http)", 80), ("443 (443 → 8443)", 443)]
    assert forward_ports("Pod", {}) == [] and forward_ports("Node", pod) == [] and forward_ports("Pod", None) == []


def test_the_forward_describes_itself():
    assert Forward(1, "c", "shop", "Pod", "web", 80, 0).target == "pod/web"
    fw = Forward(2, "c", "shop", "Service", "api", 80, 0, local=8080)
    assert (fw.target, fw.address) == ("svc/api", "127.0.0.1:8080") and Forward(3, "c", "n", "Pod", "p", 1, 0).address == ""


# ---- the processes -----------------------------------------------------------------------------------------------
def test_kubectl_is_asked_for_the_right_forward_on_loopback_only(qtbot, forwards):
    manager = forwards()
    pod = manager.start("ctx", "shop", "Pod", "web-1", 80, 8080)
    svc = manager.start("ctx", "shop", "Service", "api", 443)
    assert pod.process.arguments()[-7:] == ["-n", "shop", "port-forward", "pod/web-1", "8080:80", "--address", "127.0.0.1"]
    assert svc.process.arguments()[-7:] == ["-n", "shop", "port-forward", "svc/api", ":443", "--address", "127.0.0.1"]  # kubectl picks the local port


def test_the_real_command_carries_the_context_and_kubeconfig(qtbot):
    seen = []
    manager = PortForwards(base=lambda ctx, kc: seen.append((ctx, kc)) or ["definitely-not-a-program-klotse"])
    manager.start("prod", "shop", "Pod", "web", 80, kubeconfig="/tmp/kc")
    assert seen == [("prod", "/tmp/kc")]


def test_a_forward_becomes_active_when_kubectl_reports_the_local_port(qtbot, forwards):
    manager = forwards()
    fw = manager.start("ctx", "shop", "Pod", "web-1", 80)
    assert fw.state == "starting" and fw.address == ""
    wait_for(qtbot, fw, "active")
    assert fw.local == 54321 and fw.address == "127.0.0.1:54321" and manager.active() == [fw]  # the IPv6 line is ignored


def test_connections_are_counted(qtbot, forwards):
    manager = forwards(CONNECTIONS)
    fw = manager.start("ctx", "shop", "Pod", "web-1", 80)
    qtbot.waitUntil(lambda: fw.connections == 2, timeout=8000)
    assert fw.state == "active"


def test_a_failure_shows_kubectls_own_words(qtbot, forwards):
    manager = forwards(FAILS)
    fw = manager.start("ctx", "shop", "Pod", "web-1", 80, 8080)
    wait_for(qtbot, fw, "failed")
    assert "unable to listen" in fw.message and not fw.running and manager.active() == []


def test_a_missing_kubectl_says_so(qtbot):
    manager = PortForwards(base=lambda ctx, kc: ["definitely-not-a-program-klotse"])
    fw = manager.start("ctx", "shop", "Pod", "web-1", 80)
    qtbot.waitUntil(lambda: fw.state == "failed", timeout=8000)
    assert "kubectl" in fw.message and "PATH" in fw.message


def test_a_forward_that_ends_by_itself_is_stopped_not_failed(qtbot, forwards):
    manager = forwards(EXITS_CLEANLY)
    fw = manager.start("ctx", "shop", "Pod", "web-1", 80)
    wait_for(qtbot, fw, "stopped")
    assert fw.message == ""


def test_stopping_kills_the_process_and_stop_all_stops_every_one(qtbot, forwards):
    manager = forwards()
    first, second = manager.start("ctx", "a", "Pod", "p1", 80), manager.start("ctx", "a", "Pod", "p2", 80)
    wait_for(qtbot, first, "active")
    wait_for(qtbot, second, "active")
    manager.stop(first)
    assert first.state == "stopping"
    wait_for(qtbot, first, "stopped")
    assert second.state == "active"
    manager.stop_all()
    wait_for(qtbot, second, "stopped")
    assert manager.active() == []


def test_finished_forwards_can_be_cleared_and_running_ones_stay(qtbot, forwards):
    manager = forwards()
    keep = manager.start("ctx", "a", "Pod", "p1", 80)
    gone = manager.start("ctx", "a", "Pod", "p2", 80)
    wait_for(qtbot, keep, "active")
    wait_for(qtbot, gone, "active")
    manager.stop(gone)
    wait_for(qtbot, gone, "stopped")
    manager.remove_finished()
    assert manager.items == [keep]


def test_bad_ports_are_refused_before_anything_starts(qtbot, forwards):
    manager = forwards()
    with pytest.raises(ValueError):
        manager.start("ctx", "a", "Pod", "p", 0)
    with pytest.raises(ValueError):
        manager.start("ctx", "a", "Pod", "p", 80, 70000)
    assert manager.items == []


# ---- the panel ---------------------------------------------------------------------------------------------------
def test_the_panel_lists_forwards_and_follows_them(qtbot, forwards):
    manager = forwards()
    panel = PortForwardPanel(manager)
    qtbot.addWidget(panel)
    assert panel.table.rowCount() == 0 and not panel.stop_all_button.isEnabled()
    fw = manager.start("ctx", "shop", "Pod", "web-1", 80)
    assert panel.table.rowCount() == 1 and panel.table.item(0, 2).text() == "(waiting)" and "Starting" in panel.table.item(0, 4).text()
    wait_for(qtbot, fw, "active")
    assert panel.table.item(0, 0).text() == "pod/web-1" and panel.table.item(0, 2).text() == "127.0.0.1:54321"
    assert panel.table.item(0, 4).text().startswith("● Active") and panel.stop_all_button.isEnabled()


def test_buttons_follow_the_selected_forward(qtbot, forwards):
    manager = forwards()
    panel = PortForwardPanel(manager)
    qtbot.addWidget(panel)
    fw = manager.start("ctx", "shop", "Pod", "web-1", 80)
    wait_for(qtbot, fw, "active")
    assert not panel.stop_button.isEnabled()  # nothing selected
    panel.table.selectRow(0)
    assert panel.stop_button.isEnabled() and panel.copy_button.isEnabled() and panel.open_button.isEnabled()
    panel.copy_button.click()
    assert QGuiApplication.clipboard().text() == "127.0.0.1:54321"
    panel.stop_button.click()
    wait_for(qtbot, fw, "stopped")
    assert not panel.stop_button.isEnabled() and not panel.open_button.isEnabled() and panel.clear_button.isEnabled()
    panel.clear_button.click()
    assert panel.table.rowCount() == 0


def test_a_failed_forward_shows_why_in_the_table(qtbot, forwards):
    manager = forwards(FAILS)
    panel = PortForwardPanel(manager)
    qtbot.addWidget(panel)
    fw = manager.start("ctx", "shop", "Pod", "web-1", 80)
    wait_for(qtbot, fw, "failed")
    assert panel.table.item(0, 4).text().startswith("▲ Failed: error: unable to listen")


def test_open_in_browser_uses_the_loopback_address(qtbot, forwards, monkeypatch):
    opened = []
    monkeypatch.setattr("klotse.ui.port_forward.webbrowser.open", lambda url: opened.append(url))
    manager = forwards()
    panel = PortForwardPanel(manager)
    qtbot.addWidget(panel)
    fw = manager.start("ctx", "shop", "Pod", "web-1", 80)
    wait_for(qtbot, fw, "active")
    panel.table.selectRow(0)
    panel.open_button.click()
    assert opened == ["http://127.0.0.1:54321/"]


def test_stopping_the_panel_stops_every_forward(qtbot, forwards):
    manager = forwards()
    panel = PortForwardPanel(manager)
    qtbot.addWidget(panel)
    fw = manager.start("ctx", "shop", "Pod", "web-1", 80)
    wait_for(qtbot, fw, "active")
    panel.stop()
    wait_for(qtbot, fw, "stopped")


# ---- the dialog --------------------------------------------------------------------------------------------------
PORTS = [("http (80/TCP)", 80), ("metrics (9090/TCP)", 9090)]


def test_the_dialog_offers_the_ports_and_returns_remote_and_local(qtbot):
    d = PortForwardDialog("Pod", "web-1", "shop", PORTS)
    qtbot.addWidget(d)
    assert [d.remote.itemText(i) for i in range(d.remote.count())] == ["http (80/TCP)", "metrics (9090/TCP)"]
    d.remote.setCurrentIndex(1)
    assert d.values() == (0, 9090)  # the local port is left to kubectl
    d.local.setText("8080")
    assert d.values() == (8080, 9090)
    d.remote.setEditText("1234")  # a port that is not listed can be typed
    assert d.values() == (8080, 1234)


@pytest.mark.parametrize("remote, local", [("", ""), ("http", ""), ("80", "x"), ("80", "99999")])
def test_a_bad_entry_is_explained_and_the_dialog_stays_open(qtbot, remote, local):
    d = PortForwardDialog("Pod", "web-1", "shop", [])
    qtbot.addWidget(d)
    d.show()
    d.remote.setEditText(remote)
    d.local.setText(local)
    d._accept()
    assert d.result() != QDialog.Accepted and d.note.isVisible() and "not a port number" in d.note.text()


def test_a_good_entry_closes_the_dialog_with_its_ports(qtbot):
    d = PortForwardDialog("Service", "api", "shop", PORTS)
    qtbot.addWidget(d)
    d.local.setText("8081")
    d._accept()
    assert d.result() == QDialog.Accepted and d.result_ports == (8081, 80)


# ---- the workspace -----------------------------------------------------------------------------------------------
def row(kind="Pod"):
    spec = {"containers": [{"name": "c", "ports": [{"containerPort": 80}]}]} if kind == "Pod" else {"ports": [{"port": 80}]}
    return SimpleNamespace(name="web-1", namespace="shop", raw={"spec": spec})


def make_ws(qtbot):
    ws = ClusterWorkspace(SimpleNamespace(), "ctx")
    qtbot.addWidget(ws)
    ws.port_forwards.base = fake(ACTIVE)
    return ws


def accept_with(monkeypatch, ports):
    def exec_(self):
        self.result_ports = ports
        return QDialog.Accepted
    monkeypatch.setattr(PortForwardDialog, "exec", exec_)


def test_the_row_menu_signal_starts_a_forward_and_shows_the_panel(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    accept_with(monkeypatch, (0, 80))
    ws.pod_table.port_forward_requested.emit("Pod", row())
    [fw] = ws.port_forwards.items
    wait_for(qtbot, fw, "active")
    tabs = [ws.log_tabs.tabText(i) for i in range(ws.log_tabs.count())]
    assert tabs == ["Port forwards"] and (fw.namespace, fw.name, fw.remote) == ("shop", "web-1", 80)
    ws.start_port_forward("Pod", row())  # a second forward reuses the same tab
    assert ws.log_tabs.count() == 1 and len(ws.port_forwards.items) == 2
    ws.shutdown()
    wait_for(qtbot, fw, "stopped")


def test_services_can_be_forwarded_too(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    accept_with(monkeypatch, (8080, 80))
    ws.svc_table = next(t for t in ws.resource_tables() if t.kind == "Service")
    ws.svc_table.port_forward_requested.emit("Service", row("Service"))
    [fw] = ws.port_forwards.items
    assert fw.target == "svc/web-1" and fw.process.arguments()[-3] == "8080:80"
    ws.shutdown()
    wait_for(qtbot, fw, "stopped")


def test_cancelling_the_dialog_starts_nothing(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    monkeypatch.setattr(PortForwardDialog, "exec", lambda self: QDialog.Rejected)
    assert ws.start_port_forward("Pod", row()) is None and ws.port_forwards.items == [] and ws.log_tabs.count() == 0


def test_a_read_only_cluster_still_allows_it_because_nothing_in_the_cluster_changes(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    ws.set_read_only(True)
    accept_with(monkeypatch, (0, 80))
    fw = ws.start_port_forward("Pod", row())
    wait_for(qtbot, fw, "active")
    ws.shutdown()
    wait_for(qtbot, fw, "stopped")


def test_closing_the_tab_stops_the_forwards(qtbot, monkeypatch):
    ws = make_ws(qtbot)
    accept_with(monkeypatch, (0, 80))
    fw = ws.start_port_forward("Pod", row())
    wait_for(qtbot, fw, "active")
    ws._close_log_tab(0)
    wait_for(qtbot, fw, "stopped")
    assert ws.log_tabs.count() == 0
