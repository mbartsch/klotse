import json

import pytest
from PySide6.QtCore import QSettings

from kubeman.ui.customizations import DEFAULT_HIDDEN, LabelSettings


def fresh(path):
    return LabelSettings(QSettings(str(path), QSettings.IniFormat))


def test_file_is_plain_readable_sections(tmp_path):
    path = tmp_path / "k.ini"
    s = fresh(path)
    s.set_labels("Pod", ["app.kubernetes.io/name", "example.com/team"], {"app.kubernetes.io/name": "App"})
    s.set_layout("Pod", ["name", "status"], ["cpu_req", "cpu_lim"])
    text = path.read_text()
    assert "[columns.Pod]" in text
    assert "labels=app.kubernetes.io/name -> App | example.com/team" in text
    assert "hidden=cpu_req | cpu_lim" in text and "order=name | status" in text
    assert "{" not in text and '"' not in text  # no JSON, no quoting
    again = fresh(path)
    assert again.labels("Pod") == ["app.kubernetes.io/name", "example.com/team"]
    assert again.headers("Pod") == {"app.kubernetes.io/name": "App"}
    assert again.layout("Pod") == {"order": ["name", "status"], "hidden": ["cpu_req", "cpu_lim"]}


def test_empty_means_none_and_absent_means_default(tmp_path):
    path = tmp_path / "k.ini"
    s = fresh(path)
    assert s.labels("Node") == ["karpenter.sh/nodepool"] and s.layout("Pod")["hidden"] == DEFAULT_HIDDEN["Pod"]
    s.set_labels("Node", [])  # user removed the default label column
    s.set_layout("Pod", [], [])  # user shows every built-in column
    again = fresh(path)
    assert again.labels("Node") == [] and again.layout("Pod") == {"order": [], "hidden": []}
    assert again.layout("Node") == {"order": [], "hidden": []}


def test_defaults_are_not_written_for_untouched_kinds(tmp_path):
    path = tmp_path / "k.ini"
    fresh(path).set_layout("Deployment", ["name", "age"], [])
    text = path.read_text()
    assert "[columns.Deployment]" in text and "Node" not in text and "[columns.Pod]" not in text


def test_old_json_files_are_read_then_rewritten_readably(tmp_path):
    path = tmp_path / "k.ini"
    old = QSettings(str(path), QSettings.IniFormat)
    old.setValue("columns/labels", json.dumps({"Node": ["a/b"], "Pod": [], "headers": {"Node": {"a/b": "Bee, bee"}}}))
    old.setValue("columns/layout", json.dumps({"Pod": {"order": ["status"], "hidden": ["age"]}}))
    old.setValue("terminal/macos", "iTerm2")
    old.sync()
    s = fresh(path)
    assert s.labels("Node") == ["a/b"] and s.layout("Pod") == {"order": ["status"], "hidden": ["age"]}
    assert s.headers("Node") == {"a/b": "Bee, bee"}
    s.set_layout("Deployment", [], ["age"])  # any save migrates everything
    text = path.read_text()
    assert "columns/labels" not in text and "columns\\labels" not in text and "{" not in text
    assert "terminal" in text and "iTerm2" in text  # unrelated settings survive
    again = fresh(path)
    assert again.labels("Node") == ["a/b"] and again.headers("Node") == {"a/b": "Bee, bee"}
    assert again.layout("Pod") == {"order": ["status"], "hidden": ["age"]}


def test_headings_with_separator_are_rejected_and_nothing_is_saved(tmp_path):
    path = tmp_path / "k.ini"
    s = fresh(path)
    with pytest.raises(ValueError):
        s.set_labels("Pod", ["app"], {"app": "A | B"})
    assert s.labels("Pod") == [] and not path.exists()


def test_garbage_falls_back_to_defaults(tmp_path):
    path = tmp_path / "k.ini"
    bad = QSettings(str(path), QSettings.IniFormat)
    bad.setValue("columns/labels", "not json")
    bad.setValue("columns.Pod/labels", "has space/key = ok; bad key with spaces")
    bad.sync()
    s = fresh(path)
    assert s.labels("Node") == ["karpenter.sh/nodepool"]
    assert s.labels("Pod") == [] or "bad key with spaces" not in s.labels("Pod")
