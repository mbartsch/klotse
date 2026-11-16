"""#499 analysis windows, captured with the Windows platform (text renders) and invented data (no cluster). Before = master's src,
after = this branch's src. Usage:
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src python docs/ux-review/499/capture_analysis_windows.py OUTDIR"""
import os
import sys
from types import SimpleNamespace as NS

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])
out = sys.argv[1]
os.makedirs(out, exist_ok=True)

from klotse.ui.audit_dialog import AuditDialog  # noqa: E402
from klotse.ui.capacity_dialog import CapacityDialog  # noqa: E402
from klotse.ui.diagnostics_dialog import DiagnosticsDialog  # noqa: E402
from klotse.ui.health_dialog import HealthDialog  # noqa: E402
from klotse.ui.multi_cluster import MultiClusterDialog  # noqa: E402
from klotse.ui.permissions_dialog import PermissionsDialog  # noqa: E402
from klotse.ui.preflight_dialog import PreflightDialog  # noqa: E402
from klotse.ui.rbac_dialog import RbacDialog  # noqa: E402
from klotse.ui.search_dialog import SearchDialog  # noqa: E402
from klotse.ui.security_dialog import SecurityDialog  # noqa: E402
from klotse.ui.trivy_dialog import TrivyDialog  # noqa: E402
from klotse.ui.unused_dialog import UnusedDialog  # noqa: E402

FINDINGS = [{"severity": s, "kind": "Deployment", "namespace": "shop", "name": f"web-{i}", "problem": "no readiness probe",
             "hint": "add one"} for i, s in enumerate(("error", "warning", "info"))]


def grab(name, dialog):
    dialog.resize(900, 520)
    dialog.show()
    app.processEvents()
    dialog.grab().save(os.path.join(out, name + ".png"))
    dialog.close()


cap = CapacityDialog("prod-eu")
cap.show_data([], [])
grab("capacity", cap)

health = HealthDialog("prod-eu", "namespace shop")
try:
    health.show_findings(FINDINGS)
except KeyError:
    from klotse.k8s import health as h
    health.show_findings([dict(f, severity={"error": h.ERROR, "warning": h.WARN, "info": h.INFO}[f["severity"]]) for f in FINDINGS])
health.filter.setText("web-1")
grab("health", health)

security = SecurityDialog("prod-eu", "namespace shop")
security.show_findings([])
grab("security", security)

preflight = PreflightDialog("prod-eu", "cluster-wide")
preflight.show_error("ApiException: (403)\nReason: Forbidden\nHTTP response body: {\"message\": \"forbidden\"}")
grab("preflight", preflight)

unused = UnusedDialog("prod-eu", "namespace shop")
unused.show_result([], {"ConfigMap": 12, "Secret": 4, "PersistentVolumeClaim": 0})
grab("unused", unused)

rbac = RbacDialog("prod-eu")
rbac.show_data({"roles": [], "clusterroles": [], "rolebindings": [], "clusterrolebindings": []})
grab("rbac", rbac)

trivy = TrivyDialog("prod-eu <test>")
trivy.show_rows("vulnerabilities", [])
grab("trivy", trivy)

search = SearchDialog("prod-eu", "shop")
search.show_error(search.serial, "boom")
grab("search", search)

log = NS(path="C:/invented/changes.log", entries=lambda: [
    {"time": "2026-11-16 10:00", "cluster": "prod-eu", "action": "scale", "target": "shop/web", "result": "done", "detail": "3"}])
grab("change_log", AuditDialog(log))

perms = {"resources": [{"groups": [""], "resources": ["pods"], "verbs": ["get", "list"], "names": []}], "urls": []}
grab("permissions", PermissionsDialog("prod-eu", "shop", perms))

grab("diagnostics", DiagnosticsDialog(None, "prod-eu", "shop"))

source = NS(kinds=lambda: [("pods", "Pods")], contexts=lambda: ["prod-eu", "staging"], table_kind=lambda key: "Pod",
            columns=lambda key: [("Name", "name"), ("Namespace", "namespace")], fetch=lambda c, k: (lambda ns: []))
grab("multi_cluster", MultiClusterDialog(source))
print("saved to", out)
