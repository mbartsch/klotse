"""Preferences > Metrics: the default usage-history source for every cluster, and each cluster's own source, which can be edited, tested
or removed. The source is a Prometheus / VictoriaMetrics service, or metrics-server only (no Prometheus). A saved entry whose cluster is
no longer in the kubeconfig is marked "Not in kubeconfig"; nothing is removed on its own (only "Remove unused entries", after Yes/No).
If the kubeconfig cannot be read, nothing is marked. Test asks an open, connected cluster to check the source in the background; it
saves nothing. Changes are staged here and written by `save()`, which the Preferences dialog calls when it is saved."""
from functools import partial

from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..k8s import prometheus
from .metrics_sources import METRICS_SERVER

AUTOMATIC, PROMETHEUS, METRICS_ONLY = "Find it automatically", "Prometheus / VictoriaMetrics", "metrics-server only"


def kubeconfig_contexts(path):
    """The context names in the kubeconfig at `path` (None = the default file), or None when it cannot be read."""
    try:
        from kubernetes import config
        contexts, _active = config.list_kube_config_contexts(config_file=path or None)
    except Exception:  # noqa: BLE001 - an unreadable kubeconfig marks nothing as stale
        return None
    return [str(item["name"]) for item in contexts]


def valid_source(text):
    """The source text as saved: metrics-server, or a Prometheus address. ValueError says what is wrong."""
    text = str(text).strip()
    if text.lower() == METRICS_SERVER:
        return METRICS_SERVER
    prometheus.parse_ref(text)
    return text


class MetricsPanel(QWidget):
    def __init__(self, metrics, contexts, parent=None, tester=None):
        super().__init__(parent)
        self.metrics = metrics
        self.contexts = contexts  # None when the kubeconfig could not be read
        self.tester = tester  # tester(context or None, text, done(message)); None = testing is not available
        self.default_ref, self.default_via, self.default_local = metrics.get_default()
        self.changes = {}  # context -> entry to keep, or None to remove its own entry
        self.saved = dict(metrics.entries())

        self.mode = QComboBox()
        self.mode.addItems([AUTOMATIC, PROMETHEUS, METRICS_ONLY])
        self.mode.setCurrentIndex(2 if self.default_ref == METRICS_SERVER else 1 if self.default_ref else 0)
        self.default_edit = QLineEdit(self.default_ref if self.default_ref != METRICS_SERVER else "")
        self.default_edit.setPlaceholderText("namespace/service:port[/prefix], for example monitoring/prometheus-server:80")
        self.default_edit.setAccessibleName("Default Prometheus or VictoriaMetrics service")
        self.default_test = QPushButton("Test")
        self.default_test.clicked.connect(self._test_default)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        default_row = QHBoxLayout()
        default_row.addWidget(self.default_edit, 1)
        default_row.addWidget(self.default_test)
        defaults = QFormLayout()
        defaults.addRow("Default for all clusters", self.mode)
        defaults.addRow("Service", default_row)
        self._mode_changed()

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Cluster", "Source", "Status", ""])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.doubleClicked.connect(lambda *_: self.edit_selected())
        self.table.itemSelectionChanged.connect(self._update_buttons)

        self.edit_button = QPushButton("Edit")
        self.remove_button = QPushButton("Remove")
        self.unused_button = QPushButton()
        self.edit_button.clicked.connect(self.edit_selected)
        self.remove_button.clicked.connect(self.remove_selected)
        self.unused_button.clicked.connect(self.remove_unused)
        actions = QHBoxLayout()
        for button in (self.edit_button, self.remove_button, self.unused_button):
            actions.addWidget(button)
        actions.addStretch(1)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.result.setAccessibleName("Test result")
        note = QLabel("Test checks the source on a connected cluster and saves nothing. An empty source means: look for one automatically. "
                      "Remove forgets the saved setting after a Yes/No.")
        note.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addLayout(defaults)
        layout.addWidget(QLabel("Per cluster"))
        layout.addWidget(self.table, 1)
        layout.addLayout(actions)
        layout.addWidget(self.result)
        layout.addWidget(note)
        self._refresh()

    # --- state -------------------------------------------------------------------------------------------------------

    def _mode_changed(self, *_):
        self.default_edit.setEnabled(self.mode.currentIndex() == 1)
        self.default_test.setEnabled(self.mode.currentIndex() != 0)

    def _names(self):
        return sorted(set(self.saved) | set(self.changes))

    def _entry(self, name):
        if name in self.changes:
            return self.changes[name]
        return self.saved.get(name)

    def _is_stale(self, name):
        return self.contexts is not None and name not in self.contexts

    def _visible(self):
        return [name for name in self._names() if self._entry(name)]

    def stale_names(self):
        """Clusters with a saved entry that the kubeconfig no longer lists (none when the kubeconfig cannot be read)."""
        return [name for name in self._visible() if self._is_stale(name)]

    def _refresh(self):
        names = self._visible()
        self.table.setRowCount(len(names))
        for row, name in enumerate(names):
            entry = self._entry(name)
            if self.contexts is None:
                status = "Kubeconfig not readable"
            else:
                status = "Not in kubeconfig" if self._is_stale(name) else "In kubeconfig"
            source = "metrics-server only" if entry["ref"] == METRICS_SERVER else entry["ref"]
            for column, value in enumerate((name, source, status)):
                self.table.setItem(row, column, QTableWidgetItem(value))
            test = QPushButton("Test")
            test.clicked.connect(partial(self._test_row, name))
            self.table.setCellWidget(row, 3, test)
        self.table.clearSelection()
        self.unused_button.setText(f"Remove unused entries ({len(self.stale_names())})")
        self._update_buttons()

    def _update_buttons(self, *_):
        selected = bool(self.table.selectedItems())
        self.edit_button.setEnabled(selected)
        self.remove_button.setEnabled(selected)
        self.unused_button.setEnabled(bool(self.stale_names()))

    def _selected_name(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        return self.table.item(rows[0].row(), 0).text()

    # --- test --------------------------------------------------------------------------------------------------------

    def _show_result(self, message):
        try:
            self.result.setText(str(message))
        except RuntimeError:  # the panel was closed while the check ran
            pass

    def _run_test(self, context, text):
        if self.tester is None:
            self._show_result("Testing is not available here.")
            return
        self._show_result("Testing…")
        self.tester(context, text, self._show_result)

    def _test_default(self):
        index = self.mode.currentIndex()
        if index == 2:
            self._run_test(None, METRICS_SERVER)
            return
        try:
            text = valid_source(self.default_edit.text()) if index == 1 else ""
        except ValueError as error:
            QMessageBox.warning(self, "Metrics source", str(error))
            return
        if not text:
            self._show_result("Nothing to test: the default is to look for one automatically.")
            return
        self._run_test(None, text)

    def _test_row(self, name):
        entry = self._entry(name)
        if entry:
            self._run_test(name, entry["ref"])

    # --- actions -----------------------------------------------------------------------------------------------------

    def edit_selected(self):
        name = self._selected_name()
        if not name:
            return
        entry = self._entry(name)
        text, ok = QInputDialog.getText(self, "Metrics source",
                                        f"Source for {name}: namespace/service:port[/prefix], metrics-server, or empty to use the default",
                                        text=entry["ref"])
        if not ok:
            return
        text = text.strip()
        if text:
            try:
                text = valid_source(text)
            except ValueError as error:
                QMessageBox.warning(self, "Metrics source", str(error))
                return
            self.changes[name] = {**entry, "ref": text}
        else:
            self.changes[name] = None
        self._refresh()

    def remove_selected(self):
        name = self._selected_name()
        if not name:
            return
        answer = QMessageBox.question(self, "Remove source",
                                      f"Remove the saved source for {name}? It will use the default again.",
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.changes[name] = None
            self._refresh()

    def remove_unused(self):
        names = self.stale_names()
        if not names:
            return
        answer = QMessageBox.question(self, "Remove unused entries",
                                      "Remove the saved sources for clusters that are not in the kubeconfig?\n\n" + "\n".join(names),
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            for name in names:
                self.changes[name] = None
            self._refresh()

    # --- saving ------------------------------------------------------------------------------------------------------

    def _chosen_default(self):
        index = self.mode.currentIndex()
        if index == 0:
            return ""
        if index == 2:
            return METRICS_SERVER
        text = self.default_edit.text().strip()
        if not text:
            raise ValueError("Type the service for the default, or choose Find it automatically.")
        return valid_source(text)  # ValueError says what is wrong

    def save(self):
        """Write the default and the staged per-cluster changes. Raises ValueError for a bad address, OSError when not written."""
        default = self._chosen_default()
        if default != self.default_ref:
            keep = bool(default) and default != METRICS_SERVER
            self.metrics.set_default(default, via=self.default_via if keep else "proxy", local=self.default_local if keep else 0)
        for name, entry in self.changes.items():
            if entry is None:
                self.metrics.set(name, "")
            else:
                self.metrics.set(name, entry["ref"], via=entry["via"], local=entry["local"], tls=entry["tls"], insecure=entry["insecure"])
        self.changes = {}
