from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QWidget

from . import theme
from .action_bar import ElidedLabel, actions_button


class NodeActionBar(QWidget):
    """Node actions shown above the "pods on this node" tab, so the node can be cordoned, drained or deleted
    without going back to the Nodes list. Emits the same action names the Nodes context menu uses."""

    action_requested = Signal(str)  # cordon | uncordon | drain | node-shell | delete-node | force-delete-node

    def __init__(self, node):
        super().__init__()
        self.node, self.cordoned = node, False
        self.title = ElidedLabel()
        self.title.setStyleSheet("font-weight: 600;")
        self.state = ElidedLabel()
        theme.muted(self.state)
        # "cordon" is a toggle: the entry is renamed and sends "uncordon" while the node is cordoned
        self.menu_button, self.items = actions_button(
            [("Cordon", "cordon"), ("Drain…", "drain"), ("Shell (nsenter)…", "node-shell"), None,
             ("Delete node…", "delete-node"), ("Force delete…", "force-delete-node")], self._emit)
        self.cordon = self.items[0]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.title, 2)
        lay.addWidget(self.state, 3)
        lay.addWidget(self.menu_button)
        self.set_row(None, gone=False)

    def _emit(self, action):
        self.action_requested.emit(("uncordon" if self.cordoned else "cordon") if action == "cordon" else action)

    def set_row(self, row, gone=True):
        """Reflect the node's current state. row=None with gone=True means the node no longer exists."""
        self.title.setText(f"Node {self.node}")
        if row is None:
            self.state.setText("— no longer in the cluster" if gone else "")
            self.menu_button.setEnabled(not gone)
            for action in self.items:
                action.setEnabled(not gone)
            return
        self.cordoned = bool((row.raw.get("spec") or {}).get("unschedulable", False))
        self.cordon.setText("Uncordon" if self.cordoned else "Cordon")
        self.state.setText(f"— {row.status}")
        self.menu_button.setEnabled(True)
        for action in self.items:
            action.setEnabled(True)
