"""Source… in a usage history window: where the Prometheus / VictoriaMetrics history is read from. Three choices: look for a service automatically,
reach a named service through the API server proxy, or through a `kubectl port-forward` that Klotse starts (for a service the proxy refuses or a
store reachable only from this computer). The dialog only collects and checks the text; the workspace saves it and starts nothing until OK."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QRadioButton,
                               QToolButton, QVBoxLayout)

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

    def __init__(self, text="", via="proxy", local=0, parent=None):
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
        form = QFormLayout()
        form.addRow("Service", service_row)
        form.addRow("Path prefix", self.prefix)
        form.addRow("Local port", self.local)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        theme.muted(self.hint)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        for button in (self.auto, self.proxy, self.forward):
            layout.addWidget(button)
            button.toggled.connect(self._sync)
        layout.addLayout(form)
        layout.addWidget(self.hint)
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
        self.local.setEnabled(self.forward.isChecked())
        self.hint.setText("Nothing starts until you press OK. The choice is saved for this cluster; the forward is started again only when "
                          "you choose this source here." if self.forward.isChecked() else "")

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

    def mode(self):
        return AUTO if self.auto.isChecked() else FORWARD if self.forward.isChecked() else PROXY

    def values(self):
        """(text, via, local): "" for the automatic search, else namespace/service:port plus the prefix; via is proxy or forward."""
        if self.auto.isChecked():
            return "", PROXY, 0
        text = self.service.text().strip() + self.prefix.text().strip()
        return text, self.mode(), valid_port(self.local.text(), allow_empty=True) if self.forward.isChecked() else 0

    def _accept(self):
        try:
            text, via, _local = self.values()
            if text:
                ref = prometheus.parse_ref(text)
                if via == FORWARD and ref["scheme"] == "https":
                    raise ValueError("A port-forward to an https service is not supported. Use the API server proxy for it.")
            elif via != AUTO:
                raise ValueError("Write the service as namespace/service:port, for example monitoring/prometheus-server:80.")
        except ValueError as error:
            QMessageBox.warning(self, "Prometheus or VictoriaMetrics", str(error))
            return
        self.accept()
