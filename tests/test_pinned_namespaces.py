from types import SimpleNamespace

from PySide6.QtCore import QSettings

from klotse.k8s.models import NamespaceRow
from klotse.ui.namespace_pins import NamespacePins
from klotse.ui.workspace import ALL_NS, ClusterWorkspace


def store(tmp_path):
    return QSettings(str(tmp_path / "k.ini"), QSettings.IniFormat)


def rows(*names):
    return [NamespaceRow(n, "Active", "1d", "") for n in names]


def items(ws):
    return [ws.ns_combo.itemText(i) for i in range(ws.ns_combo.count())]


def test_pins_are_per_cluster_and_survive_a_reload(tmp_path):
    pins = NamespacePins(store(tmp_path))
    pins.set_pinned("a", "shop", True)
    pins.set_pinned("a", "db", True)
    pins.set_pinned("b", "shop", True)
    assert pins.pinned("a") == ["shop", "db"]
    assert pins.pinned("b") == ["shop"]
    pins.set_pinned("a", "shop", False)
    again = NamespacePins(store(tmp_path))
    assert again.pinned("a") == ["db"] and again.pinned("b") == ["shop"]
    assert not again.is_pinned("a", "shop")


def test_pinned_namespaces_come_first_and_the_button_toggles(qtbot, tmp_path):
    from klotse.ui.customizations import LabelSettings
    ws = ClusterWorkspace(SimpleNamespace(), "ctx", label_settings=LabelSettings(store(tmp_path)))
    qtbot.addWidget(ws)
    ws._set_namespaces(rows("alpha", "default", "zeta"))
    assert items(ws) == [ALL_NS, "alpha", "default", "zeta"]
    assert not ws.pin_button.isEnabled() or ws.namespace() != ALL_NS
    ws.set_namespace("zeta")
    assert ws.pin_button.isEnabled() and ws.pin_button.text() == "☆"
    ws.pin_button.click()
    assert items(ws) == [ALL_NS, "zeta", "alpha", "default"]
    assert ws.namespace() == "zeta" and ws.pin_button.text() == "★"
    ws.pin_button.click()
    assert items(ws) == [ALL_NS, "alpha", "default", "zeta"] and ws.pin_button.text() == "☆"


def test_all_namespaces_cannot_be_pinned_and_a_gone_namespace_is_ignored(qtbot, tmp_path):
    from klotse.ui.customizations import LabelSettings
    settings = LabelSettings(store(tmp_path))
    NamespacePins(settings.store).set_pinned("ctx", "deleted", True)
    ws = ClusterWorkspace(SimpleNamespace(), "ctx", label_settings=settings)
    qtbot.addWidget(ws)
    ws._set_namespaces(rows("default", "shop"))
    assert items(ws) == [ALL_NS, "default", "shop"]
    ws.set_namespace(ALL_NS)
    assert not ws.pin_button.isEnabled()
