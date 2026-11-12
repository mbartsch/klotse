"""Port forwards tab > Save forward: a forward that is already running (or stopped) is kept in Saved forwards with the local port it asked for."""
import sys

import pytest
from PySide6.QtCore import QSettings

from klotse.ui.port_forward import PortForwardPanel, PortForwards
from klotse.ui.saved_forwards import SavedForwards

ACTIVE = ("import time\nprint('Forwarding from 127.0.0.1:54321 -> 9090', flush=True)\n"
          "for _ in range(600):\n    time.sleep(0.05)\n")


@pytest.fixture
def manager(qtbot):
    made = PortForwards(base=lambda context, kubeconfig: [sys.executable, "-u", "-c", ACTIVE])
    yield made
    made.stop_all()
    for fw in made.items:
        if fw.process is not None:
            fw.process.waitForFinished(3000)


def test_a_running_forward_is_saved_with_the_local_port_it_asked_for(qtbot, manager, tmp_path):
    saved = SavedForwards(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))
    panel = PortForwardPanel(manager, saved=saved, context="prod-eu")
    qtbot.addWidget(panel)
    panel.show()
    fw = manager.start("prod-eu", "monitoring", "Service", "my-prom", 9090, local=0)
    qtbot.waitUntil(lambda: fw.state == "active", timeout=8000)
    panel.table.selectRow(0)
    assert panel.save_button.isEnabled()
    panel.save_button.click()
    assert saved.for_context("prod-eu") == [{"ctx": "prod-eu", "kind": "Service", "ns": "monitoring", "name": "my-prom", "remote": 9090, "local": 0}]
    panel.save_button.click()  # the same target and remote port replace the entry, they do not duplicate it
    assert len(saved.for_context("prod-eu")) == 1


def test_save_is_disabled_without_a_selection_and_without_a_store(qtbot, manager):
    panel = PortForwardPanel(manager, saved=None, context="ctx")
    qtbot.addWidget(panel)
    panel.show()
    manager.start("ctx", "shop", "Pod", "web", 80, local=8080)
    panel.table.clearSelection()
    panel._update_buttons()
    assert not panel.save_button.isEnabled()
    panel.table.selectRow(0)
    panel._update_buttons()
    assert not panel.save_button.isEnabled()  # no saved list to keep it in
