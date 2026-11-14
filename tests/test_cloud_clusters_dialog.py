"""The Add clusters from the cloud dialog and the kubeconfig list in the main window, with the provider CLI replaced by a fake
(no cloud, no network). Credentials always go to the chosen file; the main kubeconfig is only written when asked for."""
import json
from pathlib import Path

import pytest
import yaml
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from klotse.k8s import cloud_clusters as cloud
from klotse.ui import cloud_clusters_dialog as dialog_module
from klotse.ui.cloud_clusters_dialog import CloudClustersDialog
from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from test_cloud_clusters import kubeconfig


def store(tmp_path):
    return QSettings(str(tmp_path / "nested" / "Klotse.ini"), QSettings.IniFormat)


def fake_cli(monkeypatch, clusters=("demo-prod", "demo-test")):
    """Replace the provider CLI: `list-clusters` answers the names; `update-kubeconfig` writes that cluster's entry where asked."""
    calls = []

    def run(argv, env=None, timeout=None):
        calls.append(list(argv))
        if argv[1:3] == ["eks", "list-clusters"]:
            return json.dumps({"clusters": list(clusters)})
        target = argv[argv.index("--kubeconfig") + 1]
        kubeconfig(target, [argv[argv.index("--name") + 1]])
        return ""

    monkeypatch.setattr(cloud, "run", run)
    return calls


def wait_for(qtbot, condition):
    qtbot.waitUntil(condition, timeout=5000)


def test_listing_fills_the_table_with_the_clusters_found(qtbot, tmp_path, monkeypatch):
    fake_cli(monkeypatch)
    target = tmp_path / ".kube" / "klotse-cloud.klotse"
    dlg = CloudClustersDialog(lambda: (str(target), False))
    qtbot.addWidget(dlg)
    dlg.edits["region"].setText("eu-west-1")
    dlg._list()
    wait_for(qtbot, lambda: dlg.table.rowCount() == 2)
    assert [dlg.table.item(row, 1).text() for row in range(2)] == ["demo-prod", "demo-test"]
    assert dlg.add_button.isEnabled()


def test_adding_writes_only_the_ticked_cluster_into_the_generated_file(qtbot, tmp_path, monkeypatch):
    fake_cli(monkeypatch)
    main = kubeconfig(tmp_path / "main-config", ["demo-main"])
    before = main.read_bytes()
    monkeypatch.setenv("KUBECONFIG", str(main))  # the main file must not be touched
    target = tmp_path / ".kube" / "klotse-cloud.klotse"
    dlg = CloudClustersDialog(lambda: (str(target), False))
    qtbot.addWidget(dlg)
    dlg.edits["region"].setText("eu-west-1")
    dlg._list()
    wait_for(qtbot, lambda: dlg.table.rowCount() == 2)
    dlg.table.item(0, 0).setCheckState(dialog_module.Qt.Checked)
    with qtbot.waitSignal(dlg.clusters_added, timeout=5000) as signal:
        dlg._add()
    assert signal.args == [str(target), False, ["demo-prod"]]
    names = [c["name"] for c in yaml.safe_load(target.read_text(encoding="utf-8"))["contexts"]]
    assert names == ["demo-prod"]
    assert main.read_bytes() == before


def test_writing_into_the_main_kubeconfig_asks_first_and_the_default_is_no(qtbot, tmp_path, monkeypatch):
    calls = fake_cli(monkeypatch)
    main = kubeconfig(tmp_path / "main-config", ["demo-main"])
    before = main.read_bytes()
    dlg = CloudClustersDialog(lambda: (str(main), True))
    qtbot.addWidget(dlg)
    dlg.edits["region"].setText("eu-west-1")
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a[-1]) or QMessageBox.No))
    dlg._list()
    wait_for(qtbot, lambda: dlg.table.rowCount() == 2)
    dlg.table.item(0, 0).setCheckState(dialog_module.Qt.Checked)
    count = len(calls)
    dlg._add()
    assert asked == [QMessageBox.No]  # the question's default answer
    assert len(calls) == count and main.read_bytes() == before  # answered No: nothing ran, nothing changed


def test_a_provider_scope_with_a_missing_field_is_reported_not_run(qtbot, tmp_path, monkeypatch):
    calls = fake_cli(monkeypatch)
    dlg = CloudClustersDialog(lambda: (str(tmp_path / "x"), False))
    qtbot.addWidget(dlg)
    dlg._list()  # the region is empty
    assert "Region is required" in dlg.status.text()
    assert calls == []


def test_the_main_window_reads_each_context_from_the_file_it_came_from(qtbot, tmp_path):
    seen = []

    class ListingService:
        def __init__(self):
            self.kubeconfig = None
            self.proxy, self.no_proxy = "", []

        def load_contexts(self):
            seen.append(self.kubeconfig)
            if self.kubeconfig and "klotse-cloud" in str(self.kubeconfig):
                return ["demo-cloud"], None
            return ["demo-default"], None

    win = MainWindow(ListingService, LabelSettings(store(tmp_path)))
    qtbot.addWidget(win)
    generated = tmp_path / "klotse-cloud.klotse"
    kubeconfig(generated, ["demo-cloud"])
    win.kubeconfig_settings.add_extra(str(generated))
    contexts = win._load_contexts()
    assert contexts == ["demo-default", "demo-cloud"]
    assert win.new_service("demo-cloud").kubeconfig == str(generated)
    assert win.new_service("demo-default").kubeconfig is None
    assert Path(win.context_files["demo-cloud"]) == generated
