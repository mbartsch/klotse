"""View > Upgrade preflight…: read-only checks before a Kubernetes upgrade or a node drain (see k8s/preflight.py). The window is the
Health check's table with a target-version box; changing the target re-judges the data already read, nothing is asked again."""
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSpinBox

from ..k8s import health, preflight
from .health_dialog import HealthDialog

NOTE = ("Looking only. Deprecated APIs come from the API server's own request counts when your account may read /metrics (what was "
        "used since it started), otherwise from the versions it still serves (manifests that use them would break, but may not exist). "
        "Certificates are cert-manager's; control plane and kubelet certificates are not visible through the API.")


class PreflightDialog(HealthDialog):
    def __init__(self, context, scope, parent=None):
        super().__init__(context, scope, parent)
        self.setWindowTitle("Upgrade preflight")
        self.data = None
        self.problems = []
        self.reload_button.setText("Read again")
        self.target = QSpinBox()
        self.target.setPrefix("1.")
        self.target.setRange(20, 99)
        self.target.setValue(30)
        self.target.setToolTip("The Kubernetes minor version you plan to upgrade the control plane to")
        self.target.valueChanged.connect(lambda _=None: self.judge())
        self.server_label = QLabel("")
        row = QHBoxLayout()
        row.addWidget(QLabel("Upgrade to"))
        row.addWidget(self.target)
        row.addWidget(self.server_label, 1)
        self.layout().insertLayout(2, row)
        self.filter.setPlaceholderText("Filter by check, namespace, name or problem…")
        for label in self.findChildren(QLabel):
            if label.text().startswith("A finding is advice"):
                label.setText(NOTE)

    def show_data(self, data, problems):
        """The data read by `KubeService.preflight_data`; sets the target to the next minor the first time."""
        first = self.data is None
        self.data, self.problems = data, list(problems)
        server = data.get("server_version")
        self.server_label.setText(f"API server {server}" if server else "API server version unknown")
        suggested = preflight.default_target(server)
        if first and suggested:
            self.target.blockSignals(True)
            self.target.setValue(suggested)
            self.target.blockSignals(False)
        self.judge()

    def judge(self):
        if self.data is None:
            return
        findings = preflight.check(self.data, self.target.value())
        self.show_findings(findings, self.problems)
        if not findings:
            self.summary.setText("Nothing found for an upgrade to 1." + str(self.target.value()) + ". "
                                 + (("Could not read: " + "; ".join(self.problems) + ".") if self.problems else ""))
        else:
            self.summary.setText(f"For an upgrade to 1.{self.target.value()}: " + health.summary(findings)[1] + "."
                                 + ((" Could not read: " + "; ".join(self.problems) + ".") if self.problems else ""))
