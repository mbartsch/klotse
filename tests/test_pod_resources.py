from PySide6.QtCore import Qt

from kubeman.k8s.models import PodRow
from kubeman.ui.overview import POD_COLUMNS, POD_COLUMNS_BASIC, RESOURCE_COLUMNS
from kubeman.ui.customizations import DEFAULT_HIDDEN
from kubeman.ui.resource_table import ResourceTable
from kubeman.ui.table_model import TableModel


def row(containers=(), **fields):
    raw = {"spec": {"containers": [{"name": f"c{i}", "resources": r} for i, r in enumerate(containers)]}}
    return PodRow("p", "ns", "1/1", "Running", 0, "n", "1d", raw, **fields)


def test_requests_and_limits_are_summed_in_cores_and_mib():
    r = row([{"requests": {"cpu": "250m", "memory": "128Mi"}, "limits": {"cpu": "1", "memory": "256Mi"}},
             {"requests": {"cpu": "250m", "memory": "1Gi"}, "limits": {"memory": "512Mi"}}])
    assert (r.cpu_req, r.cpu_lim) == (0.5, 1.0)
    assert (r.mem_req, r.mem_lim) == (1152.0, 768.0)


def test_unset_odd_and_missing_manifest_give_zero_not_errors():
    assert row([{}, {"requests": None}]).resources == (0.0, 0.0, 0.0, 0.0)
    assert row([{"requests": {"cpu": "bogus"}}]).resources == (0.0, 0.0, 0.0, 0.0)
    assert PodRow("p", "ns", "1/1", "Running", 0, "n", "1d", {}).resources == (0.0, 0.0, 0.0, 0.0)


def test_resources_are_parsed_lazily_and_only_once():
    r = row([{"requests": {"cpu": "100m"}}])
    assert r._resources is None  # nothing computed until a request/limit value is read
    assert r.cpu_req == 0.1 and r._resources is not None


def test_percentages_against_request_and_limit_and_unknowns():
    r = row([{"requests": {"cpu": "100m", "memory": "200Mi"}, "limits": {"cpu": "200m"}}], cpu_used=0.15, mem_used=300)
    assert round(r.cpu_req_pct) == 150 and round(r.cpu_lim_pct) == 75
    assert round(r.mem_req_pct) == 150 and r.mem_lim_pct == -1  # no limit set
    assert row([{"requests": {"cpu": "1"}}]).cpu_req_pct == -1  # no metrics


def test_columns_display_blank_when_unknown(qtbot):
    model = TableModel(POD_COLUMNS)
    known = row([{"requests": {"cpu": "200m"}, "limits": {"cpu": "400m"}}], cpu_used=0.1)
    model.set_rows([known, row()])
    col = {c[1]: i for i, c in enumerate(POD_COLUMNS)}

    def show(r, attr):
        return model.data(model.index(r, col[attr]), Qt.DisplayRole)

    assert [show(0, a) for a in ("cpu_req", "cpu_lim", "cpu_req_pct", "cpu_lim_pct")] == ["200", "400", "50%", "25%"]
    assert [show(1, a) for a in ("cpu_req", "cpu_lim", "cpu_req_pct", "mem_lim_pct")] == ["", "", "", ""]


def test_hidden_by_default_so_nothing_is_read_unless_shown(qtbot):
    from kubeman.ui.customizations import LabelSettings
    from PySide6.QtCore import QSettings
    import tempfile, os
    assert set(DEFAULT_HIDDEN["Pod"]) == RESOURCE_COLUMNS == {c[1] for c in POD_COLUMNS} - {c[1] for c in POD_COLUMNS_BASIC}
    with tempfile.TemporaryDirectory() as d:
        settings = LabelSettings(QSettings(os.path.join(d, "s.ini"), QSettings.IniFormat))
        table = ResourceTable(POD_COLUMNS, "Pod")
        qtbot.addWidget(table)
        table.set_label_columns([], None, settings.layout("Pod"))
        shown = [c[1] for c in table.model.columns]
        assert not RESOURCE_COLUMNS & set(shown) and "cpu_used" in shown
        r = row([{"requests": {"cpu": "1"}}])
        table.set_rows([r])
        table.proxy.sort(0)
        assert r._resources is None  # hidden columns were never evaluated
        settings.set_layout("Pod", [], [])  # user shows them all
        table.set_label_columns([], None, settings.layout("Pod"))
        assert RESOURCE_COLUMNS <= {c[1] for c in table.model.columns}
