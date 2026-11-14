"""Plain http:// with authentication: the token or password would travel unencrypted. Klotse allows it, but the user must tick the
acknowledgement before the source is saved or tested. https:// and sources without authentication need no acknowledgement."""
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QMessageBox

import pytest

from klotse.ui.metrics_preferences import AddSourceDialog, MetricsPanel
from klotse.ui.metrics_sources import MetricsSources, plain_http_with_auth


def sources(tmp_path):
    return MetricsSources(QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat))


def test_only_plain_http_with_auth_needs_the_acknowledgement():
    assert plain_http_with_auth("http://vm.lab/select/0/prometheus", "bearer")
    assert not plain_http_with_auth("https://vm.example.com", "bearer")
    assert not plain_http_with_auth("http://vm.lab", "")
    assert not plain_http_with_auth("monitoring/vmsingle:8428", "bearer")


def test_the_dialog_will_not_accept_plain_http_with_auth_until_ticked(qapp, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warned.append(args[2]))
    dialog = AddSourceDialog(["lab"], None, cluster="lab", service="http://vm.lab:8428", auth=("bearer", "VM_TOKEN", ""))
    assert not dialog.ack_box.isHidden()
    dialog.accept()
    assert dialog.result() != QDialog.Accepted and warned
    dialog.ack_box.setChecked(True)
    dialog.accept()
    assert dialog.result() == QDialog.Accepted
    assert dialog.ack_choice() is True


def test_the_acknowledgement_is_not_asked_for_https_or_without_auth(qapp):
    secure = AddSourceDialog(["lab"], None, cluster="lab", service="https://vm.example.com", auth=("bearer", "VM_TOKEN", ""))
    assert secure.ack_box.isHidden()
    plain = AddSourceDialog(["lab"], None, cluster="lab", service="http://vm.lab:8428")
    assert plain.ack_box.isHidden()
    assert plain.ack_choice() is False


def test_a_cluster_source_keeps_the_acknowledgement_only_for_plain_http_with_auth(tmp_path):
    s = sources(tmp_path)
    s.set("lab", "http://vm.lab:8428", auth="bearer", auth_env="VM_TOKEN", ack_http=True)
    assert s.own("lab")["ack_http"] is True
    s.set("prod", "https://vm.example.com", auth="bearer", auth_env="VM_TOKEN", ack_http=True)
    assert s.own("prod")["ack_http"] is False
    s.set("plain", "http://vm.lab:8428", ack_http=True)
    assert s.own("plain")["ack_http"] is False


def test_the_default_keeps_its_acknowledgement(tmp_path):
    s = sources(tmp_path)
    s.set_default("http://vm.lab:8428", auth="bearer", auth_env="VM_TOKEN", ack_http=True)
    assert s.get_default_ack() is True
    s.set_default("https://vm.example.com", auth="bearer", auth_env="VM_TOKEN", ack_http=True)
    assert s.get_default_ack() is False


def test_the_panel_refuses_a_default_over_plain_http_until_acknowledged(qapp, tmp_path):
    s = sources(tmp_path)
    panel = MetricsPanel(s, [])
    panel.mode.setCurrentIndex(1)
    panel.default_edit.setText("http://vm.lab:8428")
    panel.default_auth_box.setCurrentIndex(1)  # bearer
    panel.default_env.setText("VM_TOKEN")
    assert not panel.default_ack.isHidden()
    with pytest.raises(ValueError, match="Tick the plain-http box"):
        panel.save()
    panel.default_ack.setChecked(True)
    panel.save()
    assert s.get_default()[0] == "http://vm.lab:8428" and s.get_default_ack() is True


def test_a_row_over_plain_http_is_not_tested_until_accepted(qapp, tmp_path):
    s = sources(tmp_path)
    s.set("lab", "http://vm.lab:8428", auth="bearer", auth_env="VM_TOKEN")  # saved without the acknowledgement
    asked = []
    panel = MetricsPanel(s, ["lab"], tester=lambda *args: asked.append(args))
    panel._test_row("lab")
    assert asked == []
    assert "Accept the plain-http warning" in panel.result.text()
