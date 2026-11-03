from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ..k8s.resources import Row
from .theme import MUTED

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
    """Header of a pod's tab: the pod's own actions (container actions are on the rows below)."""

    action_requested = Signal(str)  # logs | edit | terminate | force-terminate

    def __init__(self):
        super().__init__()
        self.title = QLabel()
        self.title.setStyleSheet("font-weight: 600;")
        self.state = QLabel()
        self.state.setStyleSheet(f"color: {MUTED};")
        self.buttons = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.title)
        lay.addWidget(self.state)
        lay.addStretch(1)
        for text, action in (("Logs", "logs"), ("Edit…", "edit"), ("Terminate…", "terminate"),
                             ("Force terminate…", "force-terminate")):
            button = QPushButton(text)
            button.clicked.connect(lambda _=False, a=action: self.action_requested.emit(a))
            self.buttons.append(button)
            lay.addWidget(button)

    def set_row(self, row):
        self.title.setText(f"Pod {row.namespace}/{row.name}")
        self.state.setText(f"— {row.status}" + (f" on {row.node}" if row.node else ""))
        for button in self.buttons:
            button.setEnabled(True)

    def set_gone(self, name):
        self.title.setText(f"Pod {name}")
        self.state.setText("— no longer in the cluster")
        for button in self.buttons:
            button.setEnabled(False)
