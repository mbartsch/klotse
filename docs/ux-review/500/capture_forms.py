"""#500 forms review: the workload editor's new-object form (Name and Namespace marked as required), and the binding editor's
Add row with an empty name (said in a box). Windows Qt platform, invented data, no cluster. Before = master's src, after =
this branch's src. Usage:
  QT_QPA_PLATFORM=windows PYTHONPATH=<tree>/src;<tree>/tests python docs/ux-review/500/capture_forms.py OUTDIR"""
import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])
out = sys.argv[1]
os.makedirs(out, exist_ok=True)

from klotse.ui import workload_model  # noqa: E402
from klotse.ui.account_directory import AccountDirectory  # noqa: E402
from klotse.ui.binding_editor import BindingEditDialog  # noqa: E402
from klotse.ui.workload_editor import WorkloadEditDialog  # noqa: E402


class Service:
    def list_service_account_names(self):
        return [("dev", "ci"), ("build", "runner")]


def sync_run(fn, on_result, *args, on_error=None):
    on_result(fn(*args))


workload = WorkloadEditDialog("Deployment", workload_model.skeleton("Deployment", "default"), create=True, namespaces=["default"])
workload.resize(620, 360)
workload.show()
app.processEvents()
workload.grab().save(os.path.join(out, "workload-new-required.png"))
workload.close()

directory = AccountDirectory(Service(), sync_run)
directory.start()
binding = BindingEditDialog("RoleBinding", {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
                                             "metadata": {"name": "rb", "namespace": "shop"},
                                             "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "r"},
                                             "subjects": []}, directory)
binding.resize(620, 360)
binding.show()
binding.name_edit.setText("")
app.processEvents()


def take():
    box = app.activeModalWidget()
    if box is not None:
        box.grab().save(os.path.join(out, "binding-add-empty-name.png"))
        box.done(0)


QTimer.singleShot(400, take)
binding.add_name_button.click()  # an empty name: the message, not silence
app.processEvents()
binding.close()
print("captured", out)
