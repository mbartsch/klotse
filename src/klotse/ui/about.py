import platform

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import Qt, qVersion
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout)

from .. import __version__
from . import branding

NAME = "Klotse"
TAGLINE = "A native Kubernetes desktop manager"
DESCRIPTION = ("Browse and manage clusters, workloads, nodes and custom resources, with logs, shells, "
               "drain and scale actions. Built with Python, PySide6/Qt and the Kubernetes client; "
               "uses kubectl for shells and drain.")
AUTHOR = "Marcelo H. Bartsch"
REPO_URL = "https://github.com/mbartsch/klotse"
LOGO_SIZE = 128
LICENSE = "Apache License 2.0"  # keep in step with the LICENSE file and pyproject.toml
LICENSE_URL = "https://www.apache.org/licenses/LICENSE-2.0"
TRADEMARK_NOTE = ("Kubernetes is a registered trademark of The Linux Foundation. Klotse is an independent "
                  "project and is not affiliated with or endorsed by The Linux Foundation or the Kubernetes project.")


def kubernetes_client_version():
    try:
        import kubernetes
        return kubernetes.__version__
    except (ImportError, AttributeError):
        return "unknown"


def license_text():
    return LICENSE or "Not specified yet"


def license_html():
    return f"<a href='{LICENSE_URL}'>{LICENSE}</a>" if LICENSE else license_text()


def details():
    """Plain-text summary shown in the dialog and copied by the Copy button (useful in bug reports)."""
    return "\n".join([
        f"{NAME} {__version__}",
        f"Project: {REPO_URL}",
        f"Author: {AUTHOR}",
        f"License: {license_text()}",
        f"Python {platform.python_version()} · PySide6 {pyside_version} · Qt {qVersion()} · "
        f"kubernetes client {kubernetes_client_version()}",
        f"Platform: {platform.platform()}",
    ])


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {NAME}")
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        pixmap = branding.square_logo(LOGO_SIZE)
        if not pixmap.isNull():
            logo = QLabel()
            logo.setPixmap(pixmap)
            logo.setFixedSize(pixmap.size())  # wrapped text beside it must not squeeze the row below the logo's height
            logo.setAlignment(Qt.AlignTop)
            top.addWidget(logo)
        title = QLabel(f"<h2 style='margin:0'>{NAME}</h2><b>Version {__version__}</b><br>{TAGLINE}")
        title.setTextFormat(Qt.RichText)
        top.addWidget(title, 1)
        layout.addLayout(top)

        description = QLabel(DESCRIPTION)
        description.setWordWrap(True)
        layout.addWidget(description)
        self.info = QLabel(
            f"<table cellspacing='4'>"
            f"<tr><td><b>Project</b></td><td><a href='{REPO_URL}'>{REPO_URL}</a></td></tr>"
            f"<tr><td><b>Author</b></td><td>{AUTHOR}</td></tr>"
            f"<tr><td><b>License</b></td><td>{license_html()}</td></tr>"
            f"<tr><td><b>Runtime</b></td><td>Python {platform.python_version()}, PySide6 {pyside_version}, "
            f"Qt {qVersion()}, kubernetes client {kubernetes_client_version()}</td></tr>"
            f"<tr><td><b>Platform</b></td><td>{platform.platform()}</td></tr></table>")
        self.info.setTextFormat(Qt.RichText)
        self.info.setOpenExternalLinks(True)
        self.info.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.info.setWordWrap(True)
        layout.addWidget(self.info)
        trademark = QLabel(TRADEMARK_NOTE)
        trademark.setWordWrap(True)
        trademark.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(trademark)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.copy_button = QPushButton("Copy details")
        self.copy_button.setToolTip("Copy the version and runtime details, e.g. for a bug report")
        self.copy_button.clicked.connect(lambda: QGuiApplication.clipboard().setText(details()))
        buttons.addButton(self.copy_button, QDialogButtonBox.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setMinimumWidth(480)
