from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from . import theme


class NodeActionBar(QWidget):
    """Node actions shown above the "pods on this node" tab, so the node can be cordoned, drained or deleted
    without going back to the Nodes list. Emits the same action names the Nodes context menu uses."""

    action_requested = Signal(str)  # cordon | uncordon | drain | node-shell | delete-node | force-delete-node

    def __init__(self, node):
        super().__init__()
        self.node, self.cordoned = node, False
        self.title = QLabel()
        self.title.setStyleSheet("font-weight: 600;")
        self.state = QLabel()
        theme.muted(self.state)
        self.cordon = QPushButton("Cordon")
        self.cordon.clicked.connect(lambda: self.action_requested.emit("uncordon" if self.cordoned else "cordon"))
        self.buttons = [self.cordon]
        for text, action in (("Drain…", "drain"), ("Shell (nsenter)…", "node-shell"),
                             ("Delete node…", "delete-node"), ("Force delete…", "force-delete-node")):
            button = QPushButton(text)
            button.clicked.connect(lambda _=False, a=action: self.action_requested.emit(a))
            self.buttons.append(button)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.title)
        lay.addWidget(self.state)
        lay.addStretch(1)
        for button in self.buttons:
            lay.addWidget(button)
        self.set_row(None, gone=False)

    def set_row(self, row, gone=True):
        """Reflect the node's current state. row=None with gone=True means the node no longer exists."""
        self.title.setText(f"Node {self.node}")
        if row is None:
            self.state.setText("— no longer in the cluster" if gone else "")
            for button in self.buttons:
                button.setEnabled(not gone)
            return
        self.cordoned = bool((row.raw.get("spec") or {}).get("unschedulable", False))
        self.cordon.setText("Uncordon" if self.cordoned else "Cordon")
        self.state.setText(f"— {row.status}")
        for button in self.buttons:
            button.setEnabled(True)
