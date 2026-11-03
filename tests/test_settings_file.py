import json

import pytest
from PySide6.QtCore import QSettings

from klotse.ui.customizations import DEFAULT_HIDDEN, LabelSettings


def fresh(path):
    return LabelSettings(QSettings(str(path), QSettings.IniFormat))


def test_file_is_plain_readable_sections_one_entry_per_line(tmp_path):
    path = tmp_path / "k.ini"
    s = fresh(path)
    s.set_labels("Pod", ["app.kubernetes.io/name", "example.com/team"], {"app.kubernetes.io/name": "App"})
    s.set_layout("Pod", ["name", "status"], ["cpu_req", "cpu_lim"])
    lines = path.read_text().splitlines()
    assert "[columns.Pod]" in lines
    assert "labels.0=app.kubernetes.io/name" in lines and "labels_name.0=App" in lines
    assert "labels.1=example.com/team" in lines and not any(line.startswith("labels_name.1") for line in lines)
    assert "hidden.0=cpu_req" in lines and "hidden.1=cpu_lim" in lines
    assert "order.0=name" in lines and "order.1=status" in lines
    text = path.read_text()
    assert "|" not in text and "{" not in text and "->" not in text and '"' not in text  # one value per line
    again = fresh(path)
    assert again.labels("Pod") == ["app.kubernetes.io/name", "example.com/team"]
    assert again.headers("Pod") == {"app.kubernetes.io/name": "App"}
    assert again.layout("Pod") == {"order": ["name", "status"], "hidden": ["cpu_req", "cpu_lim"]}


def test_numbered_keys_are_read_in_numeric_order_past_nine(tmp_path):
    path = tmp_path / "k.ini"
    keys = [f"example.com/k{i}" for i in range(12)]
    fresh(path).set_labels("Pod", keys)
    text = path.read_text()
    assert "labels.10=example.com/k10" in text and "labels.11=example.com/k11" in text
    assert fresh(path).labels("Pod") == keys  # not "k0, k1, k10, k11, k2, ..."
    hand_edited = QSettings(str(path), QSettings.IniFormat)
    hand_edited.beginGroup("columns.Pod")
    for key in hand_edited.childKeys():
        hand_edited.remove(key)
    for index, value in ((2, "c"), (10, "k"), (0, "a"), (1, "b")):  # written out of order, with a gap-free sort needed
        hand_edited.setValue(f"labels.{index}", f"example.com/{value}")
    hand_edited.endGroup()
    hand_edited.sync()
    assert fresh(path).labels("Pod") == ["example.com/a", "example.com/b", "example.com/c", "example.com/k"]


def test_previous_single_line_format_is_read_and_rewritten(tmp_path):
    path = tmp_path / "k.ini"
    old = QSettings(str(path), QSettings.IniFormat)
    old.beginGroup("columns.Pod")
    old.setValue("order", "name | status | age")
    old.setValue("hidden", "cpu_req | cpu_lim")
    old.setValue("labels", "app.kubernetes.io/name -> App | example.com/team")
    old.endGroup()
    old.sync()
    assert "name | status" in path.read_text()
    config = fresh(path)
    assert config.layout("Pod") == {"order": ["name", "status", "age"], "hidden": ["cpu_req", "cpu_lim"]}
    assert config.labels("Pod") == ["app.kubernetes.io/name", "example.com/team"]
    assert config.headers("Pod") == {"app.kubernetes.io/name": "App"}
    text = path.read_text()
    assert "|" not in text and "->" not in text and "order.2=age" in text
    assert "labels.0=app.kubernetes.io/name" in text and "labels_name.0=App" in text


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


def test_headings_may_contain_separators_and_arrows(tmp_path):
    path = tmp_path / "k.ini"
    fresh(path).set_labels("Pod", ["app", "team"], {"app": "A | B, C; D", "team": "x -> y = z"})
    again = fresh(path)
    assert again.headers("Pod") == {"app": "A | B, C; D", "team": "x -> y = z"}
    with pytest.raises(ValueError):
        again.set_labels("Pod", ["app"], {"app": "two\nlines"})
    assert again.headers("Pod")["app"] == "A | B, C; D"  # a rejected change saves nothing


def test_garbage_falls_back_to_defaults(tmp_path):
    path = tmp_path / "k.ini"
    bad = QSettings(str(path), QSettings.IniFormat)
    bad.setValue("columns/labels", "not json")
    bad.setValue("columns.Pod/labels", "has space/key = ok; bad key with spaces")
    bad.sync()
    s = fresh(path)
    assert s.labels("Node") == ["karpenter.sh/nodepool"]
    assert s.labels("Pod") == [] or "bad key with spaces" not in s.labels("Pod")


LEGACY_LAYOUT = '{"ConfigMap": {"order": ["name", "age"], "hidden": []}}'
LEGACY_LABELS = '{"Node": ["karpenter.sh/nodepool", "beta.kubernetes.io/os"], "Pod": [], "headers": {"Node": {"beta.kubernetes.io/os": "OS"}}}'


def test_old_file_is_rewritten_readably_on_first_load_without_any_save(tmp_path):
    path = tmp_path / "k.ini"
    old = QSettings(str(path), QSettings.IniFormat)
    old.setValue("columns/layout", LEGACY_LAYOUT)
    old.setValue("columns/labels", LEGACY_LABELS)
    old.setValue("terminal/macos", "iTerm2")
    old.sync()
    assert "{" in path.read_text()  # the unreadable JSON form

    first = fresh(path)  # just starting the app: nothing is saved by the user
    text = path.read_text()
    assert "{" not in text and "\\\"" not in text  # no JSON left, no escaped quotes
    assert "[columns.Node]" in text and "labels.0=karpenter.sh/nodepool" in text and "labels.1=beta.kubernetes.io/os" in text and "labels_name.1=OS" in text
    assert "[columns.ConfigMap]" in text and "order.0=name" in text and "order.1=age" in text and "|" not in text
    assert "iTerm2" in text  # unrelated settings survive
    assert first.labels("Node") == ["karpenter.sh/nodepool", "beta.kubernetes.io/os"]
    assert first.headers("Node") == {"beta.kubernetes.io/os": "OS"}
    assert first.layout("ConfigMap") == {"order": ["name", "age"], "hidden": []}
    again = fresh(path)  # a second start reads the new format and changes nothing
    assert path.read_text() == text and again.labels("Node") == first.labels("Node")


def test_unwritable_old_settings_still_load(tmp_path, monkeypatch):
    path = tmp_path / "k.ini"
    old = QSettings(str(path), QSettings.IniFormat)
    old.setValue("columns/labels", LEGACY_LABELS)
    old.sync()
    store = QSettings(str(path), QSettings.IniFormat)
    monkeypatch.setattr(QSettings, "status", lambda self: QSettings.AccessError)  # every save looks like it failed
    config = LabelSettings(store)  # must not raise
    assert config.labels("Node") == ["karpenter.sh/nodepool", "beta.kubernetes.io/os"]


def test_headings_are_paired_with_labels_by_number_not_position(tmp_path):
    path = tmp_path / "k.ini"
    hand = QSettings(str(path), QSettings.IniFormat)
    hand.beginGroup("columns.Pod")
    for key, value in (("labels.0", "example.com/a"), ("labels.2", "example.com/c"), ("labels.1", "example.com/b"),
                       ("labels_name.2", "See"), ("labels_name.0", "Aye"), ("labels_name.7", "orphan heading")):
        hand.setValue(key, value)
    hand.endGroup()
    hand.sync()
    config = fresh(path)
    assert config.labels("Pod") == ["example.com/a", "example.com/b", "example.com/c"]
    assert config.headers("Pod") == {"example.com/a": "Aye", "example.com/c": "See"}  # b has none; 7 matches nothing


def test_previous_arrow_format_is_read_and_rewritten_as_labels_name(tmp_path):
    path = tmp_path / "k.ini"
    old = QSettings(str(path), QSettings.IniFormat)
    old.beginGroup("columns.Node")
    old.setValue("labels.0", "karpenter.sh/nodepool -> TSET")
    old.setValue("labels.1", "beta.kubernetes.io/os -> OS")
    old.setValue("labels.2", "example.com/plain")
    old.endGroup()
    old.sync()
    config = fresh(path)
    assert config.labels("Node") == ["karpenter.sh/nodepool", "beta.kubernetes.io/os", "example.com/plain"]
    assert config.headers("Node") == {"karpenter.sh/nodepool": "TSET", "beta.kubernetes.io/os": "OS"}
    lines = path.read_text().splitlines()
    assert "labels.0=karpenter.sh/nodepool" in lines and "labels_name.0=TSET" in lines
    assert "labels_name.1=OS" in lines and "->" not in path.read_text()
    assert not any(line.startswith("labels_name.2") for line in lines)


def test_removing_a_heading_removes_its_labels_name_line(tmp_path):
    path = tmp_path / "k.ini"
    config = fresh(path)
    config.set_labels("Pod", ["app", "team"], {"app": "App", "team": "Team"})
    assert "labels_name.0=App" in path.read_text() and "labels_name.1=Team" in path.read_text()
    config.set_labels("Pod", ["team", "app"], {"team": "Team"})  # reordered, and app lost its heading
    text = path.read_text()
    assert "labels.0=team" in text and "labels_name.0=Team" in text and "labels.1=app" in text
    assert "labels_name.1" not in text and "App" not in text
    assert fresh(path).headers("Pod") == {"team": "Team"}
