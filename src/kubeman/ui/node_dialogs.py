from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout,
                               QLabel, QLineEdit, QSpinBox)

from .terminal import NODE_SHELL_IMAGE, kubectl_base


class DrainDialog(QDialog):
    def __init__(self, context, node, parent=None, kubeconfig=None):
        super().__init__(parent)
        self.kubeconfig = kubeconfig
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
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Drain")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.context, self.node = context, node

    def command(self):
        argv = kubectl_base(self.context, self.kubeconfig) + [
            "--request-timeout=30s", "drain", self.node, f"--timeout={self.timeout.value()}m"]
        for field, flag in ((self.ignore_daemonsets, "--ignore-daemonsets"),
                            (self.delete_emptydir, "--delete-emptydir-data"), (self.force, "--force")):
            if field.isChecked():
                argv.append(flag)
        return argv


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
