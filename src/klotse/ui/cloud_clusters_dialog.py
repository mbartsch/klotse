"""File > Add clusters from the cloud…: list a cloud account's clusters with the provider's CLI and add the ones ticked.

Credentials are written to a separate file (klotse-cloud.klotse) unless the main kubeconfig was chosen in Preferences. Writing to
the main file asks first (Yes/No, default No) and keeps a .bak copy. Commands run on the thread pool, never on the GUI thread."""
import shutil

from PySide6.QtCore import QThreadPool, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..k8s import cloud_clusters as cloud
from . import theme
from ..workers import Worker


def _short(message):
    return message[len("CloudError: "):] if message.startswith("CloudError: ") else message


class CloudClustersDialog(QDialog):
    """`target()` returns (path, is_main): where the credentials go, and whether that is the main kubeconfig."""

    clusters_added = Signal(str, bool, object)  # target path, is the main kubeconfig, context names added

    def __init__(self, target, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add clusters from the cloud")
        self.resize(720, 560)
        self.target_path, self.is_main = target()
        self._clusters = []
        self._busy = False
        self._detected = {}  # field key -> the value Klotse filled in from the local configuration

        self.provider = QComboBox()
        for key, spec in cloud.PROVIDERS.items():
            self.provider.addItem(spec.title, key)
        self.provider.currentIndexChanged.connect(self._provider_changed)

        self.edits = {}
        self.form = QFormLayout()
        for spec in cloud.PROVIDERS.values():
            for field in spec.fields:
                if field.key not in self.edits:
                    self.edits[field.key] = QLineEdit()
                    self.edits[field.key].setAccessibleName(field.label)
        self.labels = {}
        for key, edit in self.edits.items():
            self.labels[key] = QLabel()
            self.form.addRow(self.labels[key], edit)

        self.list_button = QPushButton("List clusters")
        self.list_button.clicked.connect(self._list)
        self.add_button = QPushButton("Add selected clusters")
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self._add)
        buttons = QHBoxLayout()
        buttons.addWidget(self.list_button)
        buttons.addStretch(1)
        buttons.addWidget(self.add_button)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Add", "Cluster", "Location", "Group or state"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        # The tick box is the table's own indicator; the app theme leaves it small and faint, so it is drawn larger here.
        self.table.setStyleSheet(
            f"QTableView::indicator {{ width: 20px; height: 20px; border: 2px solid {theme.MUTED}; border-radius: 4px; "
            f"background: {theme.BASE}; }}"
            f"QTableView::indicator:checked {{ background: {theme.ACCENT}; border-color: {theme.ACCENT}; }}")
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)  # the tick box keeps its width
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.target_note = QLabel(self._target_text())
        self.target_note.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(self.provider)
        layout.addLayout(self.form)
        layout.addLayout(buttons)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.target_note)
        self._provider_changed()

    def _target_text(self):
        if self.is_main:
            return f"Clusters will be merged into the main kubeconfig: {self.target_path} (a .bak copy is kept first)."
        return f"Clusters will be written to {self.target_path}, which Klotse then reads with the other kubeconfig files."

    def _provider_changed(self):
        spec = cloud.PROVIDERS[self.provider.currentData()]
        wanted = {f.key: f for f in spec.fields}
        for key, edit in self.edits.items():
            field = wanted.get(key)
            self.labels[key].setVisible(field is not None)
            edit.setVisible(field is not None)
            if field is not None:
                self.labels[key].setText(field.label + (" *" if field.required else ""))
        self._fill_defaults(spec.key)
        self.table.setRowCount(0)
        self._clusters = []
        self.add_button.setEnabled(False)
        self.status.setText("Fill in the fields (* required), then List clusters.")

    def _fill_defaults(self, provider_key):
        """Fill the provider's detected defaults into empty fields. A field still holding an earlier detected value (for
        example the region of another provider, since the fields are shared) is replaced; typed values are kept."""
        for key, value in cloud.detect_defaults(provider_key).items():
            edit = self.edits.get(key)
            if edit is None:
                continue
            current = edit.text().strip()
            if not current or current == self._detected.get(key):
                edit.setText(value)
                self._detected[key] = value

    def _values(self):
        return {key: edit.text() for key, edit in self.edits.items()}

    def _set_busy(self, busy, text=""):
        self._busy = busy
        self.list_button.setEnabled(not busy)
        self.add_button.setEnabled(not busy and bool(self._clusters))
        self.provider.setEnabled(not busy)
        if text:
            self.status.setText(text)

    # ---- listing ---------------------------------------------------------
    def _list(self):
        if self._busy:
            return
        provider = self.provider.currentData()
        values = self._values()
        try:
            cloud.list_argv(provider, values)  # checks the fields before anything runs
        except cloud.CloudError as error:
            self.status.setText(str(error))
            return
        self._set_busy(True, "Listing clusters…")
        worker = Worker(self._list_worker, provider, values)
        worker.signals.result.connect(self._listed)
        worker.signals.error.connect(self._failed)
        QThreadPool.globalInstance().start(worker)

    @staticmethod
    def _list_worker(provider, values):
        text = cloud.run(cloud.list_argv(provider, values))
        return cloud.parse_clusters(provider, text, values)

    def _listed(self, clusters):
        self._set_busy(False)
        self._clusters = list(clusters)
        self.table.setRowCount(len(self._clusters))
        for row, cluster in enumerate(self._clusters):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check.setCheckState(Qt.Unchecked)
            self.table.setItem(row, 0, check)
            self.table.setItem(row, 1, QTableWidgetItem(cluster.name))
            self.table.setItem(row, 2, QTableWidgetItem(cluster.location))
            self.table.setItem(row, 3, QTableWidgetItem(cluster.group or cluster.state))
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(0, 64)  # the tick box must stay large enough to see; resizing to contents made it tiny
        self.status.setText(f"{len(self._clusters)} cluster(s) found. Tick the ones to add." if self._clusters
                            else "No clusters found in this scope.")
        self.add_button.setEnabled(bool(self._clusters))

    def _failed(self, message):
        self._set_busy(False, _short(message))

    # ---- adding ----------------------------------------------------------
    def _ticked(self):
        return [self._clusters[row] for row in range(self.table.rowCount())
                if self.table.item(row, 0) is not None and self.table.item(row, 0).checkState() == Qt.Checked]

    def _add(self):
        if self._busy:
            return
        chosen = self._ticked()
        if not chosen:
            self.status.setText("Tick at least one cluster.")
            return
        if self.is_main and QMessageBox.question(
                self, "Change the main kubeconfig",
                f"Merge {len(chosen)} cluster(s) into the main kubeconfig at\n{self.target_path}?\n\nA .bak copy is kept first.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        provider = self.provider.currentData()
        values = self._values()
        self._set_busy(True, f"Adding {len(chosen)} cluster(s)…")
        worker = Worker(self._add_worker, provider, values, chosen, self.target_path)
        worker.signals.result.connect(self._added)
        worker.signals.error.connect(self._failed)
        QThreadPool.globalInstance().start(worker)

    @staticmethod
    def _add_worker(provider, values, chosen, target):
        """Each cluster's credentials go to a private temporary file first; only then are they merged into `target`."""
        workdir = cloud.scratch_dir()
        added, skipped, errors = [], [], []
        try:
            for index, cluster in enumerate(chosen):
                piece = workdir / f"{index}.yaml"
                try:
                    cluster = cloud.resolve_location(provider, cluster, values)
                    argv, env = cloud.credentials_argv(provider, cluster, values, piece)
                    cloud.run(argv, env=env)
                    if not piece.exists():
                        errors.append(f"{cluster.name}: the CLI wrote no kubeconfig")
                        continue
                    new, existing = cloud.merge_into(target, piece)
                    added += new
                    skipped += existing
                except cloud.CloudError as error:
                    errors.append(f"{cluster.name}: {error}")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        return added, skipped, errors

    def _added(self, result):
        added, skipped, errors = result
        self._set_busy(False)
        lines = [f"Added {len(added)} context(s)."]
        if skipped:
            lines.append("Already in the file, not changed: " + ", ".join(skipped))
        lines += [f"Failed: {error}" for error in errors]
        self.status.setText("\n".join(lines))
        if added:
            self.clusters_added.emit(self.target_path, self.is_main, list(added))
