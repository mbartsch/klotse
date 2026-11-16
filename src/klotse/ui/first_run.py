"""#501: what the centre of the main window shows while no cluster is open (a new user sees this first)."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


class FirstRunPage(QWidget):
    """Two states: no kubeconfig context yet (point to the kubeconfig and to the cloud), or contexts that are not connected
    (point to the navigator). Both buttons only ask the main window to open the matching dialog."""

    choose_kubeconfig = Signal()
    add_from_cloud = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("firstRunPage")
        self.setAccessibleName("Getting started")
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 18px; font-weight: 600;")
        self.text = QLabel()
        self.text.setWordWrap(True)
        self.text.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.choose_button = QPushButton("Choose kubeconfig…")
        self.choose_button.setToolTip("Preferences > Kubeconfig: pick the file that lists your clusters")
        self.choose_button.clicked.connect(self.choose_kubeconfig)
        self.cloud_button = QPushButton("Add clusters from the cloud…")
        self.cloud_button.setToolTip("List EKS, GKE, AKS or OKE clusters with the provider's CLI and add them to a kubeconfig file")
        self.cloud_button.clicked.connect(self.add_from_cloud)
        buttons = QHBoxLayout()
        buttons.addWidget(self.choose_button)
        buttons.addWidget(self.cloud_button)
        buttons.addStretch(1)
        body = QVBoxLayout(self)
        body.setContentsMargins(32, 32, 32, 32)
        body.addWidget(self.title)
        body.addWidget(self.text)
        body.addLayout(buttons)
        body.addStretch(1)
        self.set_has_contexts(False)

    def set_has_contexts(self, has_contexts):
        """Pick the wording for the state: clusters listed but none open, or none listed at all."""
        self.choose_button.setVisible(not has_contexts)
        self.cloud_button.setVisible(not has_contexts)
        if has_contexts:
            self.title.setText("No cluster is open")
            self.text.setText("Double-click a cluster in the navigator on the left to connect to it. Clusters stay listed "
                              "there until you remove them from the kubeconfig.")
        else:
            self.title.setText("No clusters yet")
            self.text.setText("Klotse reads clusters from a kubeconfig file. Choose the file that lists yours, or add clusters "
                              "from your cloud provider (its command-line tool must be installed and signed in).")
