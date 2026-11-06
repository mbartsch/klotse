from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QWidget

from ..k8s.resources import Row
from . import theme
from .action_bar import ElidedLabel, actions_button

CONTAINER_COLUMNS = [("Name", "name"), ("Type", "type"), ("State", "state"), ("Ready", "ready"),
                     ("Restarts", "restarts"), ("Image", "image")]
CONTAINER_FIELDS = (("containers", "containerStatuses", "Container"),
                    ("initContainers", "initContainerStatuses", "Init"),
                    ("ephemeralContainers", "ephemeralContainerStatuses", "Ephemeral"))


def pod_containers(raw):
    """(name, type, state, ready, restarts, image) for regular, init and ephemeral containers."""
    spec, status = raw.get("spec") or {}, raw.get("status") or {}
    rows = []
    for field, status_field, kind in CONTAINER_FIELDS:
        states = {c["name"]: c for c in status.get(status_field) or []}
        for container in spec.get(field) or []:
            current = states.get(container["name"], {})
            state = current.get("state") or {}
            phase = next(iter(state), "Unknown")
            reason = (state.get(phase) or {}).get("reason")
            rows.append((container["name"], kind, reason or phase.capitalize(),
                         "Yes" if current.get("ready") else "No", str(current.get("restartCount", 0)),
                         container.get("image", "")))
    return rows


def container_rows(raw):
    """Table rows for a pod's containers; selecting one shows that container's spec in the details panel."""
    specs = {c["name"]: c for field, _, _ in CONTAINER_FIELDS for c in (raw.get("spec") or {}).get(field) or []}
    return [Row(name, "", specs.get(name, {}), type=kind, state=state, ready=ready, restarts=restarts, image=image)
            for name, kind, state, ready, restarts, image in pod_containers(raw)]


class PodActionBar(QWidget):
    """Header of a pod's tab: the pod's name and state (cut with … when the tab is narrow, never covered) and one Actions menu
    with the pod's own actions (container actions are on the rows below)."""

    action_requested = Signal(str)  # logs | edit | copy-files | terminate | force-terminate

    def __init__(self):
        super().__init__()
        self.title = ElidedLabel()
        self.title.setStyleSheet("font-weight: 600;")
        self.state = ElidedLabel()
        theme.muted(self.state)
        self.menu_button, self.items = actions_button(
            [("Logs", "logs"), ("Edit…", "edit"), ("Copy files…", "copy-files"), None, ("Terminate…", "terminate"), ("Force terminate…", "force-terminate")],
            self.action_requested.emit)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.title, 2)
        lay.addWidget(self.state, 3)
        lay.addWidget(self.menu_button)

    def set_row(self, row):
        self.title.setText(f"Pod {row.namespace}/{row.name}")
        self.state.setText(f"— {row.status}" + (f" on {row.node}" if row.node else ""))
        self.menu_button.setEnabled(True)
        for action in self.items:
            action.setEnabled(True)

    def set_gone(self, name):
        self.title.setText(f"Pod {name}")
        self.state.setText("— no longer in the cluster")
        self.menu_button.setEnabled(False)
        for action in self.items:
            action.setEnabled(False)
