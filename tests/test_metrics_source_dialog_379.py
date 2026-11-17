"""#379: source-dialog checks use unsaved values and never persist a test."""
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog
from klotse.ui.metrics_preferences import AddSourceDialog, MetricsPanel
from klotse.ui.metrics_sources import MetricsSources

@pytest.mark.parametrize('answer', ['Prometheus API answered', 'Forbidden: permission denied'])
def test_dialog_test_reports_success_or_failure_without_saving(qtbot, tmp_path, answer):
    sources = MetricsSources(QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat))
    asked = []
    def tester(context, text, done, auth):
        asked.append((context, text, auth))
        done(answer)
    dialog = AddSourceDialog(['demo'], service='monitoring/prometheus:80', tester=tester)
    qtbot.addWidget(dialog)
    dialog.test_button.click()
    assert asked == [('demo', 'monitoring/prometheus:80', ('', '', ''))]
    assert dialog.test_result.text() == answer
    assert dialog.test_button.isEnabled()
    assert sources.entries() == {}
    assert dialog.result() != QDialog.Accepted


def test_metrics_server_preset_is_testable(qtbot):
    asked = []
    dialog = AddSourceDialog(['demo'], tester=lambda c, s, done, a: (asked.append(s), done('metrics-server answered')))
    qtbot.addWidget(dialog)
    dialog.metrics_server_button.click()
    dialog.test_button.click()
    assert dialog.chosen() == ('demo', 'metrics-server')
    assert asked == ['metrics-server']


def test_changed_source_discards_old_test_answer(qtbot):
    callbacks = []
    dialog = AddSourceDialog(['demo'], service='obs/prometheus:80', tester=lambda c, s, done, a: callbacks.append(done))
    qtbot.addWidget(dialog)
    dialog.test_button.click()
    assert not dialog.test_button.isEnabled()
    dialog.service.setEditText('obs/victoria:8428')
    callbacks[0]('old source answered')
    assert dialog.test_result.text() == ''
    assert dialog.test_button.isEnabled()


def test_test_requires_plain_http_auth_acknowledgement(qtbot):
    asked = []
    dialog = AddSourceDialog(['demo'], service='http://metrics.example', auth=('bearer', 'TOKEN', ''),
                             tester=lambda *args: asked.append(args))
    qtbot.addWidget(dialog)
    dialog.test_button.click()
    assert not asked
    assert 'unencrypted' in dialog.test_result.text()


def test_add_empty_override_keeps_global_default(qtbot, tmp_path, monkeypatch):
    sources = MetricsSources(QSettings(str(tmp_path / 'settings.ini'), QSettings.IniFormat))
    sources.set_default('obs/prometheus:80')
    panel = MetricsPanel(sources, ['demo'])
    qtbot.addWidget(panel)
    monkeypatch.setattr(AddSourceDialog, 'exec', lambda self: QDialog.Accepted)
    panel.add_source('demo')
    panel.save()
    assert sources.own('demo') is None
    assert sources.get('demo') == 'obs/prometheus:80'
