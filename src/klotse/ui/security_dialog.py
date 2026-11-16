"""View > Pod security…: workloads that break the Kubernetes Pod Security Standards (see k8s/pod_security.py). It is the Health check's
table (severity, kind, namespace, name, problem, hint) with its own title and note. Double-click goes to the workload."""
from PySide6.QtWidgets import QLabel

from . import analysis_window as aw
from .health_dialog import HealthDialog

NOTE = ("Checked against the Pod Security Standards from the pod manifests: baseline is what a pod must never do (privileged, host access, "
        "hostPath, extra capabilities), restricted is the hardened set. Advice, not a verdict: networking and storage components in "
        "kube-system legitimately need more. Only manifests are read; nothing is changed.")


class SecurityDialog(HealthDialog):
    def __init__(self, context, scope, parent=None):
        super().__init__(context, scope, parent)
        self.setWindowTitle(aw.title("Pod security", context))
        self.reload_button.setText("Refresh")  # one name for reloading in every analysis window (#499)
        self.filter.setPlaceholderText("Filter by workload, namespace or what it does…")
        for label in self.findChildren(QLabel):
            if label.text().startswith("A finding is advice"):
                label.setText(NOTE)
