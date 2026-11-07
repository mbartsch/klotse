from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSpinBox)

from ..k8s import drain_plan
from . import theme

from .terminal import DEBUG_IMAGE, NODE_SHELL_IMAGE, kubectl_base


class DrainDialog(QDialog):
    def __init__(self, context, node, parent=None, kubeconfig=None, preview_loader=None):
        super().__init__(parent)
        self.kubeconfig = kubeconfig
        self.preview_loader = preview_loader  # fn(on_result, on_error): loads (pods, budgets); None = no preview
        self._preview_data = None
        self.setWindowTitle("Drain node")
        form = QFormLayout(self)
        info = QLabel(f"Cluster: {context}\nNode: {node}\n\n"
                      "Cordon this node and evict its pods. The node stays cordoned, including on failure or stop.\n"
                      "PodDisruptionBudgets and pod termination grace periods are respected.")
        info.setWordWrap(True)
        form.addRow(info)
        self.ignore_daemonsets = QCheckBox("Skip DaemonSet pods (leave them running)")
        self.ignore_daemonsets.setChecked(True)
        self.delete_emptydir = QCheckBox("Allow deletion of emptyDir data")
        self.force = QCheckBox("Allow eviction of pods without a controller (--force)")
        self.timeout = QSpinBox()
        self.timeout.setRange(1, 120)
        self.timeout.setValue(10)
        self.timeout.setSuffix(" min")
        for field in (self.ignore_daemonsets, self.delete_emptydir, self.force):
            form.addRow(field)
        form.addRow("Timeout", self.timeout)
        self.preview_button = QPushButton("Preview what will happen")
        self.preview_text = QPlainTextEdit(readOnly=True)
        self.preview_text.setMinimumHeight(160)
        self.preview_text.setPlaceholderText("Press Preview to list the pods on this node and what would happen to each. Nothing is changed.")
        if preview_loader is not None:
            form.addRow(self.preview_button)
            form.addRow(self.preview_text)
            self.preview_button.clicked.connect(self.load_preview)
            for field in (self.ignore_daemonsets, self.delete_emptydir, self.force):
                field.toggled.connect(lambda _=None: self._render_preview())
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Drain")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.context, self.node = context, node

    def load_preview(self):
        self.preview_button.setEnabled(False)
        self.preview_text.setPlainText("Loading the pods on this node…")
        self.preview_loader(self._preview_loaded, self._preview_failed)

    def _preview_loaded(self, data):
        self.preview_button.setEnabled(True)
        self._preview_data = data
        self._render_preview()

    def _preview_failed(self, message):
        self.preview_button.setEnabled(True)
        self.preview_text.setPlainText(f"Could not load the preview: {message}")

    def _render_preview(self):
        """Redo the verdict for the ticked options (the pods are loaded once)."""
        if self._preview_data is None:
            return
        pods, budgets = self._preview_data
        entries = drain_plan.plan(pods, budgets, self.ignore_daemonsets.isChecked(), self.delete_emptydir.isChecked(),
                                  self.force.isChecked())
        marks = {drain_plan.EVICT: "evict", drain_plan.DELETE: "remove", drain_plan.SKIP: "skip", drain_plan.STOP: "REFUSED"}
        lines = [drain_plan.summary(entries), ""]
        for e in sorted(entries, key=lambda e: (e["action"], e["namespace"], e["name"])):
            line = f"{marks[e['action']]:8} {e['namespace']}/{e['name']}: {e['reason']}"
            if e["pdb"]:
                line += f"  [{e['pdb']}]"
            lines.append(line)
        if not entries:
            lines.append("No pods on this node.")
        self.preview_text.setPlainText(chr(10).join(lines))

    def command(self):
        argv = kubectl_base(self.context, self.kubeconfig) + [
            "--request-timeout=30s", "drain", self.node, f"--timeout={self.timeout.value()}m"]
        for field, flag in ((self.ignore_daemonsets, "--ignore-daemonsets"),
                            (self.delete_emptydir, "--delete-emptydir-data"), (self.force, "--force")):
            if field.isChecked():
                argv.append(flag)
        return argv


class DebugDialog(QDialog):
    """Options for `kubectl debug` on a pod: the image of the ephemeral container and the container to share processes with."""

    def __init__(self, context, pod, containers, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Debug pod (ephemeral container)")
        form = QFormLayout(self)
        info = QLabel(f"Cluster: {context}\nPod: {pod}\n\n"
                      "Add a temporary container to this running pod and open a shell in it (kubectl debug).\n"
                      "Ephemeral containers cannot be removed from a pod: this one stays listed until the pod is replaced.\n"
                      "It needs the pod's ephemeralcontainers permission and a cluster that supports ephemeral containers.")
        info.setWordWrap(True)
        form.addRow(info)
        self.image = QLineEdit(DEBUG_IMAGE)
        self.target = QComboBox()
        self.target.addItem("(none)", None)
        for name in containers:
            self.target.addItem(name, name)
        self.target.setToolTip("Share the process namespace of this container, so its processes can be seen from the debug shell")
        self.sharing = QLabel("With a container chosen, its processes show up in ps and its files can be read at /proc/<pid>/root/ (same user or root needed).\n"
                              "Other pods are separate: their files are not reachable from here (only shared volumes, or the node shell).")
        self.sharing.setTextFormat(Qt.PlainText)  # "<pid>" must not be read as markup
        self.sharing.setWordWrap(True)
        theme.muted(self.sharing)
        form.addRow("Debug image", self.image)
        form.addRow("Share processes with", self.target)
        form.addRow(self.sharing)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.open_button = buttons.button(QDialogButtonBox.Ok)
        self.open_button.setText("Open debug shell")
        self.image.textChanged.connect(lambda t: self.open_button.setEnabled(bool(t.strip())))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self):
        return self.image.text().strip(), self.target.currentData()


class NodeShellDialog(QDialog):
    def __init__(self, context, node, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Node shell (nsenter)")
        form = QFormLayout(self)
        info = QLabel(f"Cluster: {context}\nNode: {node}\n\n"
                      "Create a privileged debug pod and enter the Linux host namespaces with nsenter.\n"
                      "Uses the kubectl node-shell Linux setup and starts the host's bash login shell.\n"
                      "The pod is removed when the session exits. If the terminal is killed or the connection\n"
                      "is lost, delete the nsenter pod manually (its name appears in the terminal).")
        info.setWordWrap(True)
        form.addRow(info)
        self.namespace = QLineEdit("default")
        self.image = QLineEdit(NODE_SHELL_IMAGE)
        form.addRow("Debug pod namespace", self.namespace)
        form.addRow("Debug image (must include nsenter)", self.image)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.open_button = buttons.button(QDialogButtonBox.Ok)
        self.open_button.setText("Open shell")
        def validate():
            self.open_button.setEnabled(bool(self.namespace.text().strip() and self.image.text().strip()))
        self.namespace.textChanged.connect(validate)
        self.image.textChanged.connect(validate)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
