from types import SimpleNamespace

from PySide6.QtWidgets import QPlainTextEdit

from klotse.k8s.client import KubeService
from klotse.ui import log_view
from klotse.ui.log_view import LogView


class Stream:
    def stream(self, n):
        return iter([b"line\n"])

    def close(self):
        pass


class Service:
    def __init__(self):
        self.calls = []

    def open_log_stream(self, namespace, pod, container=None, tail=500, **options):
        self.calls.append((container, tail, options))
        return Stream()


def make(qtbot):
    service = Service()
    view = LogView(service, "ns", "pod", ["app"])
    qtbot.addWidget(view)
    qtbot.waitUntil(lambda: bool(service.calls))
    return service, view


def finish(view):
    view.stop()
    log_view.shutdown_all()


def test_plain_start_sends_no_options(qtbot):
    service, view = make(qtbot)
    assert service.calls[0][2] == {}
    finish(view)


def test_wrap_toggles_line_wrapping(qtbot):
    _, view = make(qtbot)
    assert view.text.lineWrapMode() == QPlainTextEdit.NoWrap
    view.wrap.setChecked(True)
    assert view.text.lineWrapMode() == QPlainTextEdit.WidgetWidth
    view.wrap.setChecked(False)
    assert view.text.lineWrapMode() == QPlainTextEdit.NoWrap
    finish(view)


def test_timestamps_and_previous_restart_the_stream_with_options(qtbot):
    service, view = make(qtbot)
    view.timestamps.setChecked(True)
    qtbot.waitUntil(lambda: len(service.calls) == 2)
    assert service.calls[1][2] == {"timestamps": True}
    view.previous.setChecked(True)
    qtbot.waitUntil(lambda: len(service.calls) == 3)
    assert service.calls[2][2] == {"timestamps": True, "previous": True}
    finish(view)


def test_service_passes_options_and_does_not_follow_previous():
    seen = {}

    def read(pod, namespace, **kw):
        seen.update(kw)
        return "resp"

    service = KubeService.__new__(KubeService)
    service._core = SimpleNamespace(read_namespaced_pod_log=read)
    service.open_log_stream("ns", "pod", "app", 50, timestamps=True, previous=True)
    assert seen["previous"] is True and seen["timestamps"] is True and seen["follow"] is False
    service.open_log_stream("ns", "pod", "app", 50)
    assert seen["follow"] is True and seen["previous"] is False and seen["timestamps"] is False
