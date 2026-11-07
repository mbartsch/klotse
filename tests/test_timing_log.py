import functools
import json
import os
import threading

import pytest

from klotse.k8s import timing
from klotse.k8s.timing import TimingLog, name_of, size_of
from klotse.ui.preferences import DebugSettings, PreferencesDialog, KubeconfigSettings
from klotse.workers import Worker


def lines(path):
    return [json.loads(line) for line in open(path, encoding="utf-8")]


def test_off_by_default_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv(timing.ENV, raising=False)
    log = TimingLog()
    log.configure(tmp_path / "t.log", False)
    log.record("phase", "x", 1.0)
    with log.timed("phase", "y"):
        pass
    assert not (tmp_path / "t.log").exists()


def test_on_writes_a_session_line_and_records(tmp_path, monkeypatch):
    monkeypatch.delenv(timing.ENV, raising=False)
    log = TimingLog()
    assert log.configure(tmp_path / "t.log", True, "9.9") is True
    with log.timed("phase", "overview.pods", cluster="c1") as extra:
        extra["items"] = 5
    rows = lines(tmp_path / "t.log")
    assert rows[0]["kind"] == "session" and rows[0]["klotse"] == "9.9"
    assert rows[1]["kind"] == "phase" and rows[1]["label"] == "overview.pods" and rows[1]["items"] == 5 and rows[1]["seconds"] >= 0


def test_environment_variable_switches_it_on(tmp_path, monkeypatch):
    monkeypatch.setenv(timing.ENV, "1")
    log = TimingLog()
    assert log.configure(tmp_path / "t.log", False) is True


def test_rotation_and_unwritable_path_never_raise(tmp_path, monkeypatch):
    monkeypatch.delenv(timing.ENV, raising=False)
    log = TimingLog()
    log.configure(tmp_path / "t.log", True)
    monkeypatch.setattr(timing, "MAX_BYTES", 10)
    log.record("phase", "a", 0.1)
    log.record("phase", "b", 0.1)
    assert os.path.exists(str(tmp_path / "t.log") + ".1")
    (tmp_path / "file").write_text("not a folder")
    bad = TimingLog()
    bad.configure(str(tmp_path / "file" / "x.log"), True)  # the session line cannot be written either: no exception
    bad.record("phase", "c", 0.1)  # no exception


def test_names_and_sizes():
    def fetch(a):
        return a

    assert name_of(functools.partial(fetch, 1)).endswith("fetch")
    assert size_of([1, 2, 3]) == 3 and size_of(([1, 2], [3])) == 3 and size_of("text") is None and size_of(None) is None


def test_a_worker_logs_name_count_and_queue_time_but_not_arguments(tmp_path, qtbot, monkeypatch):
    monkeypatch.delenv(timing.ENV, raising=False)
    timing.timings.configure(tmp_path / "t.log", True)
    try:
        def list_things(secret_argument):
            return [1, 2, 3]

        worker = Worker(list_things, "super-secret-name")
        worker.submitted = timing.time.perf_counter()
        worker.context = "ctx"
        got = []
        worker.signals.result.connect(got.append)
        worker.run()
        entry = [e for e in lines(tmp_path / "t.log") if e["kind"] == "worker"][0]
        assert entry["label"].endswith("list_things") and entry["items"] == 3 and entry["cluster"] == "ctx" and "queued" in entry
        assert "super-secret-name" not in open(tmp_path / "t.log", encoding="utf-8").read()
    finally:
        timing.timings.configure(None, False)


def test_a_failing_worker_is_logged_with_the_error_type(tmp_path, monkeypatch):
    monkeypatch.delenv(timing.ENV, raising=False)
    timing.timings.configure(tmp_path / "t.log", True)
    try:
        def boom():
            raise ValueError("secret detail")

        Worker(boom).run()
        entry = [e for e in lines(tmp_path / "t.log") if e["kind"] == "worker"][0]
        assert entry["error"] == "ValueError" and "secret detail" not in open(tmp_path / "t.log", encoding="utf-8").read()
    finally:
        timing.timings.configure(None, False)


def test_api_list_pages_are_counted(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from klotse.k8s.client import KubeService
    monkeypatch.delenv(timing.ENV, raising=False)
    timing.timings.configure(tmp_path / "t.log", True)
    try:
        pages = [SimpleNamespace(items=[1, 2], metadata=SimpleNamespace(_continue="n")), SimpleNamespace(items=[3], metadata=SimpleNamespace(_continue=None))]

        def list_pod_for_all_namespaces(limit, _continue, _request_timeout):
            return pages.pop(0)

        assert list(KubeService._iter_list(list_pod_for_all_namespaces)) == [1, 2, 3]
        entry = [e for e in lines(tmp_path / "t.log") if e["kind"] == "api-list"][0]
        assert entry["label"] == "list_pod_for_all_namespaces" and entry["pages"] == 2 and entry["items"] == 3
    finally:
        timing.timings.configure(None, False)


def test_debug_setting_is_saved_and_preferences_has_the_box(tmp_path, qtbot):
    from PySide6.QtCore import QSettings
    store = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    debug = DebugSettings(store)
    assert debug.timings is False
    dialog = PreferencesDialog(KubeconfigSettings(store), tmp_path, debug=debug)
    qtbot.addWidget(dialog)
    assert dialog.timing_box is not None and not dialog.timing_box.isChecked()
    dialog.timing_box.setChecked(True)
    dialog._save()
    assert DebugSettings(store).timings is True
    assert PreferencesDialog(KubeconfigSettings(store), tmp_path).timing_box is None
