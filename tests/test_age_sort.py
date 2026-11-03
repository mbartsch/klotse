from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSortFilterProxyModel, Qt

from klotse.ui.table_model import TableModel, age_sort_value


@pytest.mark.parametrize("age, seconds", [
    ("8h", 28800), ("1d", 86400), ("35m", 2100), ("35min", 2100),
    ("12s", 12), ("0s", 0), ("100d", 8640000), ("", -1), (None, -1),
])
def test_age_sort_values(age, seconds):
    assert age_sort_value(age) == seconds


def test_age_sort_orders_durations_and_preserves_display(qapp):
    ages = ["8h", "35m", "1d", "9s", "12h", "100d", "10m", "2d"]
    model = TableModel([("Name", "name"), ("Age", "age")])
    model.set_rows([SimpleNamespace(name=f"row-{i}", age=age) for i, age in enumerate(ages)])
    proxy = QSortFilterProxyModel()
    proxy.setSourceModel(model)
    proxy.setSortRole(Qt.EditRole)
    proxy.sort(1, Qt.DescendingOrder)
    descending = [proxy.index(i, 1).data() for i in range(proxy.rowCount())]
    assert descending == ["100d", "2d", "1d", "12h", "8h", "35m", "10m", "9s"]
    proxy.sort(1, Qt.AscendingOrder)
    assert [proxy.index(i, 1).data() for i in range(proxy.rowCount())] == list(reversed(descending))
    assert model.index(0, 1).data(Qt.DisplayRole) == "8h"
    assert model.index(0, 1).data(Qt.EditRole) == 28800
    assert model.index(0, 0).data(Qt.EditRole) == "row-0"
