"""Source… in a usage history window: where the Prometheus / VictoriaMetrics history is read from. Three choices: look for a service automatically,
reach a named service through the API server proxy, or through a `kubectl port-forward` that Klotse starts (for a service the proxy refuses or a
store reachable only from this computer). For a port-forward, SSL and certificate checks can be set here or left to the Preferences default.
Test checks the chosen settings before OK. The dialog only collects and checks the text; the workspace saves it and starts nothing until OK."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox,
                               QPushButton, QRadioButton, QToolButton, QVBoxLayout)

from ..k8s import prometheus
from . import theme
from .port_forward import valid_port

AUTO, PROXY, FORWARD = "auto", "proxy", "forward"


def split_ref(text):
    """(`namespace/service:port`, path prefix) of a saved reference; the whole text and "" when it cannot be read."""
    try:
        ref = prometheus.parse_ref(text)
    except ValueError:
        return str(text), ""
    return prometheus.ref_text({**ref, "prefix": ""}), ref["prefix"]


class PrometheusSourceDialog(QDialog):
    candidates_requested = Signal()  # the Candidates button: the owner looks for services in the background and calls set_candidates
    test_requested = Signal()  # the Test button: the owner checks the chosen settings and calls set_test_result

    def __init__(self, text="", via="proxy", local=0, tls="", insecure="", default_tls=False, default_insecure=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Prometheus or VictoriaMetrics")
        self.auto = QRadioButton("Look for one automatically")
        self.proxy = QRadioButton("Through the API server proxy")
        self.forward = QRadioButton("Through a port-forward")
        for button, note in ((self.auto, "Klotse checks the services of the cluster."),
                             (self.proxy, "Needs get on services/proxy."),
                             (self.forward, "For a service the API server cannot reach, or a store you reach only from this computer.")):
            button.setToolTip(note)
        self.service = QLineEdit()
        self.service.setPlaceholderText("namespace/service:port, for example monitoring/vmselect:8481")
        self.prefix = QLineEdit()
        self.prefix.setPlaceholderText("optional, for example /select/0/prometheus")
        self.local = QLineEdit()
        self.local.setPlaceholderText("empty: a free port on 127.0.0.1")
        self.candidates = QToolButton()
        self.candidates.setText("Candidates ▾")
        self.candidates.setPopupMode(QToolButton.InstantPopup)
        self.candidates_menu = QMenu(self.candidates)
        self.candidates_menu.addAction("Looking for services…").setEnabled(False)
        self.candidates.setMenu(self.candidates_menu)
        self.candidates_menu.aboutToShow.connect(self.candidates_requested.emit)
        service_row = QHBoxLayout()
        service_row.addWidget(self.service, 1)
        service_row.addWidget(self.candidates)
        self.tls_box = QComboBox()
        self.tls_box.setAccessibleName("Use SSL through the port-forward")
        self.tls_box.setToolTip("Connect to the forwarded port with https")
        for label, value in ((f"Preferences ({'SSL' if default_tls else 'plain HTTP'})", ""), ("Use SSL (https)", "on"), ("Plain HTTP", "off")):
            self.tls_box.addItem(label, value)
        self.insecure_box = QComboBox()
        self.insecure_box.setAccessibleName("Certificate checks through the port-forward")
        self.insecure_box.setToolTip("Ignore certificate errors for the forwarded https service (self-signed or internal certificates)")
        for label, value in ((f"Preferences ({'ignore errors' if default_insecure else 'check certificates'})", ""),
                             ("Ignore certificate errors", "on"), ("Check certificates", "off")):
            self.insecure_box.addItem(label, value)
        self.tls_box.setCurrentIndex(max(0, self.tls_box.findData(tls if tls in ("on", "off") else "")))
        self.insecure_box.setCurrentIndex(max(0, self.insecure_box.findData(insecure if insecure in ("on", "off") else "")))
        form = QFormLayout()
        form.addRow("Service", service_row)
        form.addRow("Path prefix", self.prefix)
        form.addRow("Local port", self.local)
        form.addRow("SSL", self.tls_box)
        form.addRow("Certificates", self.insecure_box)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        theme.muted(self.hint)
        self.test_button = QPushButton("Test")
        self.test_button.setToolTip("Check the service with these settings. Starts the port-forward if needed; nothing is saved.")
        self.test_button.clicked.connect(self.test_requested.emit)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        self.test_result.setAccessibleName("Test result")
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        self.buttons.addButton(self.test_button, QDialogButtonBox.ActionRole)
        layout = QVBoxLayout(self)
        for button in (self.auto, self.proxy, self.forward):
            layout.addWidget(button)
            button.toggled.connect(self._sync)
        layout.addLayout(form)
        layout.addWidget(self.hint)
        layout.addWidget(self.test_result)
        layout.addWidget(self.buttons)
        service, prefix = split_ref(text) if text else ("", "")
        self.service.setText(service)
        self.prefix.setText(prefix)
        self.local.setText(str(local) if local and via == FORWARD else "")
        (self.auto if not text else self.forward if via == FORWARD else self.proxy).setChecked(True)
        self._sync()

    def _sync(self, *_):
        named = not self.auto.isChecked()
        for widget in (self.service, self.prefix, self.candidates):
            widget.setEnabled(named)
        forwarding = self.forward.isChecked()
        self.local.setEnabled(forwarding)
        self.tls_box.setEnabled(forwarding)
        self.insecure_box.setEnabled(forwarding)
        self.test_button.setEnabled(named)
        self.hint.setText("Nothing starts until you press OK. The choice is saved for this cluster; the forward is started again only when "
                          "you choose this source here." if forwarding else "")

    def set_candidates(self, refs):
        """The services found in the cluster (refs from `prometheus.candidates`); choosing one fills the fields."""
        menu = self.candidates_menu
        menu.clear()
        if not refs:
            menu.addAction("No Prometheus or VictoriaMetrics service found").setEnabled(False)
            return
        for ref in refs[:12]:
            menu.addAction(prometheus.ref_text(ref), lambda r=ref: self._pick(r))

    def _pick(self, ref):
        self.service.setText(prometheus.ref_text({**ref, "prefix": ""}))
        self.prefix.setText(ref.get("prefix", ""))

    def set_test_result(self, ok, message):
        """Shown under the fields after Test: what worked or which step failed."""
        self.test_result.setText(("✓ " if ok else "✗ ") + message)

    def mode(self):
        return AUTO if self.auto.isChecked() else FORWARD if self.forward.isChecked() else PROXY

    def values(self):
        """(text, via, local): "" for the automatic search, else namespace/service:port plus the prefix; via is proxy or forward."""
        if self.auto.isChecked():
            return "", PROXY, 0
        text = self.service.text().strip() + self.prefix.text().strip()
        return text, self.mode(), valid_port(self.local.text(), allow_empty=True) if self.forward.isChecked() else 0

    def options(self):
        """(tls, insecure) choices: "on", "off" or "" (use the Preferences setting)."""
        return self.tls_box.currentData() or "", self.insecure_box.currentData() or ""

    def _accept(self):
        try:
            text, via, _local = self.values()
            if text:
                prometheus.parse_ref(text)
            elif via != AUTO:
                raise ValueError("Write the service as namespace/service:port, for example monitoring/prometheus-server:80.")
        except ValueError as error:
            QMessageBox.warning(self, "Prometheus or VictoriaMetrics", str(error))
            return
        self.accept()
