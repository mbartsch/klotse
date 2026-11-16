"""File > Add clusters from the cloud…: list a cloud account's clusters and add the ones ticked.

The clusters come from the enabled cloud plugins (each one lists its clusters and writes their credentials; see
klotse.plugins.api.ClusterSource). Core only shows the dialog and merges the result. Credentials are written to a separate file
(klotse-cloud.klotse) unless the main kubeconfig was chosen in Preferences. Writing to the main file asks first (Yes/No, default
No) and keeps a .bak copy. Commands run on the thread pool, never on the GUI thread."""
import os
import shutil

from .dialog_sizing import resize_dialog
from PySide6.QtCore import QThreadPool, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..k8s import cloud_clusters as cloud
from ..plugins.api import CloudError
from ..workers import Worker
from .dialog_actions import action_box

NO_PLUGIN_TEXT = ("No cloud plugin is enabled. Enable one in Plugins (for example Google Cloud (GKE) Plugin, AWS Plugin, "
                  "Azure (AKS) Plugin or Oracle Cloud (OKE) Plugin), then open this dialog again.")


def _short(message):
    return message[len("CloudError: "):] if message.startswith("CloudError: ") else message


class CloudClustersDialog(QDialog):
    """`target()` returns (path, is_main): where the credentials go, and whether that is the main kubeconfig.
    `sources` is a list of (plugin, ClusterSource) for the enabled plugins that list clusters."""

    clusters_added = Signal(str, bool, object)  # target path, is the main kubeconfig, context names added

    def __init__(self, target, sources, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add clusters from the cloud")
        resize_dialog(self, 720, 560)
        self.target_path, self.is_main = target()
        self.sources = list(sources)
        self._clusters = []
        self._boxes = []
        self._busy = False
        self._detected = {}  # field key -> the value Klotse filled in from the local configuration

        self.provider = QComboBox()
        for _plugin, source in self.sources:
            self.provider.addItem(source.title)
        self.provider.currentIndexChanged.connect(self._provider_changed)

        self.edits = {}
        self.labels = {}
        self.form = QFormLayout()
        for _plugin, source in self.sources:
            for field in source.fields:
                if field.key not in self.edits:
                    self.edits[field.key] = QLineEdit()
                    self.edits[field.key].setAccessibleName(field.label)
                    self.labels[field.key] = QLabel()
                    self.form.addRow(self.labels[field.key], self.edits[field.key])

        self.list_button = QPushButton("List clusters")
        self.list_button.clicked.connect(self._list)
        self.add_button = QPushButton("Add selected clusters")
        self.add_button.setEnabled(False)
        self.add_button.clicked.connect(self._add)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        # the platform's own order, as in every other dialog (#499: this one built its own row)
        buttons = QHBoxLayout()
        buttons.addWidget(action_box(self, (self.list_button, QDialogButtonBox.ActionRole),
                                     (self.add_button, QDialogButtonBox.AcceptRole),
                                     (self.close_button, QDialogButtonBox.RejectRole)))

        # One large checkbox per cluster, named in full: a checkbox is far easier to see than a table's small indicator.
        self.list_holder = QWidget()
        self.list_layout = QVBoxLayout(self.list_holder)
        self.list_layout.setAlignment(Qt.AlignTop)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.list_holder)
        self.scroll.setMinimumHeight(180)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.target_note = QLabel(self._target_text())
        self.target_note.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(self.provider)
        layout.addLayout(self.form)
        layout.addLayout(buttons)
        layout.addWidget(self.scroll, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.target_note)
        if not self.sources:
            self.status.setText(NO_PLUGIN_TEXT)
            self.list_button.setEnabled(False)
            self.provider.setEnabled(False)
        else:
            self._provider_changed()

    def _current(self):
        """(plugin, source) of the selected provider, or None when no plugin is enabled."""
        index = self.provider.currentIndex()
        return self.sources[index] if 0 <= index < len(self.sources) else None

    def _target_text(self):
        if self.is_main:
            return f"Clusters will be merged into the main kubeconfig: {self.target_path} (a .bak copy is kept first)."
        return f"Clusters will be written to {self.target_path}, which Klotse then reads with the other kubeconfig files."

    def _provider_changed(self):
        current = self._current()
        if current is None:
            return
        plugin, source = current
        wanted = {f.key: f for f in source.fields}
        for key, edit in self.edits.items():
            field = wanted.get(key)
            self.labels[key].setVisible(field is not None)
            edit.setVisible(field is not None)
            if field is not None:
                self.labels[key].setText(field.label + (" *" if field.required else ""))
        self._fill_defaults(plugin, source)
        self._clear_list()
        self._clusters = []
        self.add_button.setEnabled(False)
        self.status.setText("Fill in the fields (* required), then List clusters.")

    def _fill_defaults(self, plugin, source):
        """Fill the plugin's detected defaults into empty fields. A field still holding an earlier detected value (for example
        the region of another provider, since the fields are shared) is replaced; typed values are kept."""
        try:
            found = plugin.cluster_defaults(source, os.environ)
        except (OSError, ValueError):
            found = {}
        for key, value in (found or {}).items():
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
        self.list_button.setEnabled(not busy and bool(self.sources))
        self.add_button.setEnabled(not busy and bool(self._clusters))
        self.provider.setEnabled(not busy)
        if text:
            self.status.setText(text)

    # ---- listing ---------------------------------------------------------
    def _list(self):
        if self._busy or self._current() is None:
            return
        plugin, source = self._current()
        values = self._values()
        self._set_busy(True, "Listing clusters…")
        worker = Worker(self._list_worker, plugin, source, values)
        worker.signals.result.connect(self._listed)
        worker.signals.error.connect(self._failed)
        QThreadPool.globalInstance().start(worker)

    @staticmethod
    def _list_worker(plugin, source, values):
        return plugin.list_clusters(source, values, cloud.run)

    def _clear_list(self):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._boxes = []

    def _listed(self, clusters):
        self._set_busy(False)
        self._clusters = list(clusters)
        self._clear_list()
        for cluster in self._clusters:
            details = "  ·  ".join(part for part in (cluster.location, cluster.group or cluster.state) if part)
            box = QCheckBox(f"{cluster.name}" + (f"    ({details})" if details else ""))
            box.setStyleSheet("QCheckBox { font-size: 14px; padding: 6px; } "
                              "QCheckBox::indicator { width: 22px; height: 22px; }")
            self.list_layout.addWidget(box)
            self._boxes.append(box)
        self.status.setText(f"{len(self._clusters)} cluster(s) found. Tick the ones to add." if self._clusters
                            else "No clusters found in this scope.")
        self.add_button.setEnabled(bool(self._clusters))

    def _failed(self, message):
        self._set_busy(False, _short(message))

    # ---- adding ----------------------------------------------------------
    def _ticked(self):
        return [cluster for cluster, box in zip(self._clusters, self._boxes) if box.isChecked()]

    def _add(self):
        if self._busy or self._current() is None:
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
        plugin, source = self._current()
        values = self._values()
        self._set_busy(True, f"Adding {len(chosen)} cluster(s)…")
        worker = Worker(self._add_worker, plugin, source, values, chosen, self.target_path)
        worker.signals.result.connect(self._added)
        worker.signals.error.connect(self._failed)
        QThreadPool.globalInstance().start(worker)

    @staticmethod
    def _add_worker(plugin, source, values, chosen, target):
        """Each cluster's credentials go to a private temporary file first; only then are they merged into `target`."""
        workdir = cloud.scratch_dir()
        added, skipped, errors = [], [], []
        try:
            for index, cluster in enumerate(chosen):
                piece = workdir / f"{index}.yaml"
                try:
                    plugin.cluster_credentials(source, cluster, values, piece, cloud.run)
                    if not piece.exists():
                        errors.append(f"{cluster.name}: the CLI wrote no kubeconfig")
                        continue
                    new, existing = cloud.merge_into(target, piece)
                    added += new
                    skipped += existing
                except CloudError as error:
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
