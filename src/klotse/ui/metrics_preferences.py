"""Preferences > Metrics: the default usage-history source for every cluster, and each cluster's own source, which can be added, edited, tested
or removed. A source is a Prometheus / VictoriaMetrics service, a URL (called directly from this computer, optionally with a bearer token or a
user name and password taken from an environment variable), or metrics-server only (no Prometheus). A saved entry whose cluster is no longer
in the kubeconfig is marked "Not in kubeconfig"; nothing is removed on its own (only "Remove unused entries", after Yes/No). If the kubeconfig
cannot be read, nothing is marked. Test asks an open, connected cluster to check the source in the background; a URL is checked from this
computer; it saves nothing. Changes are staged here and written by `save()`, which the Preferences dialog calls when it is saved."""
from functools import partial

from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from PySide6.QtWidgets import QCheckBox

from ..k8s import prometheus
from .metrics_sources import METRICS_SERVER, plain_http_with_auth

HTTP_WARNING = ("Plain http:// sends the token or password unencrypted: anyone on the network path can read it. "
                "Use https:// where you can. Tick the box below to accept it for this source.")
HTTP_ACK = "I understand the token or password is sent unencrypted over http://, and accept it"

AUTOMATIC, PROMETHEUS, METRICS_ONLY = "Find it automatically", "Prometheus / VictoriaMetrics or URL", "metrics-server only"
AUTH_CHOICES = (("", "No authentication"),
                ("bearer", "Bearer token from an environment variable"),
                ("basic", "User name and password (password from an environment variable)"))
NO_AUTH = ("", "", "")


def kubeconfig_contexts(path):
    """The context names in the kubeconfig at `path` (None = the default file), or None when it cannot be read."""
    try:
        from kubernetes import config
        contexts, _active = config.list_kube_config_contexts(config_file=path or None)
    except Exception:  # noqa: BLE001 - an unreadable kubeconfig marks nothing as stale
        return None
    return [str(item["name"]) for item in contexts]


def valid_source(text):
    """The source text as saved: metrics-server, a Prometheus address, or a URL. ValueError says what is wrong."""
    text = str(text).strip()
    if text.lower() == METRICS_SERVER:
        return METRICS_SERVER
    prometheus.parse_ref(text)
    return text


def auth_fields(auth_box, env_edit, user_edit):
    """(kind, secret source, user name) from the auth widgets. The secret source is the environment variable name, or `file:` + the
    path when the file choice is selected; the user name only for basic."""
    kind = auth_box.currentData() or ""
    value = env_edit.text().strip()
    if kind and env_edit.source_box.currentData() == "file" and value:
        value = "file:" + value
    return kind, value, user_edit.text().strip() if kind == "basic" else ""


def _auth_widgets(parent_form, auth):
    """The authentication widgets (kind, secret, user) set from `auth` = (kind, secret source, user). The secret source is an environment
    variable name, or `file:` + a path (saved that way); the choice of where to read it is shown beside the field."""
    box = QComboBox()
    for kind, label in AUTH_CHOICES:
        box.addItem(label, kind)
    box.setCurrentIndex(max(0, [kind for kind, _ in AUTH_CHOICES].index(auth[0] or "")))
    box.setAccessibleName("Authentication for a URL source")
    source_box = QComboBox()
    source_box.addItem("Environment variable", "variable")
    source_box.addItem("File", "file")
    source_box.setAccessibleName("Where the token or password is read from")
    saved = str(auth[1] or "")
    is_file = saved.startswith("file:")
    source_box.setCurrentIndex(1 if is_file else 0)
    env_edit = QLineEdit(saved[len("file:"):] if is_file else saved)
    env_edit.source_box = source_box
    env_edit.setAccessibleName("Environment variable or file holding the token or password")
    user_edit = QLineEdit(auth[2])
    user_edit.setPlaceholderText("user name")
    user_edit.setAccessibleName("User name")

    def changed(*_):
        kind = box.currentData() or ""
        source_box.setEnabled(bool(kind))
        env_edit.setEnabled(bool(kind))
        user_edit.setEnabled(kind == "basic")
        env_edit.setPlaceholderText("/path/to/file, for example /run/secrets/metrics_token"
                                    if source_box.currentData() == "file" else "name of the variable, for example METRICS_TOKEN")

    box.currentIndexChanged.connect(changed)
    source_box.currentIndexChanged.connect(changed)
    changed()
    parent_form.addRow("Authentication (URL only)", box)
    parent_form.addRow("Read the secret from", source_box)
    parent_form.addRow("Variable or file", env_edit)
    parent_form.addRow("User name", user_edit)
    box.source_box = source_box
    return box, env_edit, user_edit


class AddSourceDialog(QDialog):
    """Add or edit a cluster's source. A connected cluster lists the services found on it (`discover(name, done)` calls done with the list,
    or None when the cluster is not connected); the user can always type the address instead, or a URL with authentication."""

    def __init__(self, clusters, discover=None, parent=None, cluster="", service="", auth=NO_AUTH, fixed_cluster=False, ack=False):
        super().__init__(parent)
        self.setWindowTitle("Metrics source" if fixed_cluster else "Add a metrics source")
        self.discover = discover
        self.cluster = QComboBox()
        self.cluster.setEditable(not fixed_cluster)
        self.cluster.addItems(clusters)
        if fixed_cluster:
            self.cluster.setEditText(cluster)
            self.cluster.setEnabled(False)
        else:
            self.cluster.lineEdit().setPlaceholderText("cluster name from the kubeconfig")
            if cluster:
                self.cluster.setEditText(cluster)
        self.cluster.setAccessibleName("Cluster")
        self.service = QComboBox()
        self.service.setEditable(True)
        self.service.setEditText(service)
        self.service.lineEdit().setPlaceholderText("namespace/service:port[/prefix], https://host/prefix, or metrics-server")
        self.service.lineEdit().setAccessibleName("Service or URL")
        self.status = QLabel("")
        self.status.setWordWrap(True)
        form = QFormLayout()
        form.addRow("Cluster", self.cluster)
        form.addRow("Service or URL", self.service)
        self.auth_box, self.auth_env, self.auth_user = _auth_widgets(form, auth)
        self.ack_box = QCheckBox(HTTP_ACK)
        self.ack_box.setChecked(ack)
        self.http_warning = QLabel(HTTP_WARNING)
        self.http_warning.setWordWrap(True)
        self.http_warning.setAccessibleName("Plain http warning")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.status)
        layout.addWidget(self.http_warning)
        layout.addWidget(self.ack_box)
        layout.addWidget(buttons)
        self.service.currentTextChanged.connect(self._update_ack)
        self.auth_box.currentIndexChanged.connect(self._update_ack)
        self._update_ack()
        self.cluster.currentTextChanged.connect(self._cluster_changed)
        self._cluster_changed(self.cluster.currentText())

    def _cluster_changed(self, name):
        typed = self.service.currentText()  # keep what is typed while the services are looked for
        self.service.clear()
        self.service.setEditText(typed)
        name = name.strip()
        if not name:
            self.status.setText("Choose or type the cluster.")
            return
        if self.discover is None:
            self._found(None, name)
            return
        self.status.setText("Looking for services on the cluster…")
        self.discover(name, lambda result, asked=name: self._found(result, asked))

    def _found(self, result, asked):
        try:
            if asked != self.cluster.currentText().strip():
                return  # the cluster was changed meanwhile
            typed = self.service.currentText()
            if result is None:
                self.status.setText("This cluster is not connected: type the service or URL, for example monitoring/prometheus-server:80 "
                                    "or https://vm.example.com/select/0/prometheus. Connect the cluster to pick one it has.")
            elif not result:
                self.status.setText("No Prometheus or VictoriaMetrics service found on this cluster. Type one.")
            else:
                self.service.addItems(result)
                self.status.setText(f"Found {len(result)} on this cluster: pick one, or type another.")
            self.service.setEditText(typed)
        except RuntimeError:  # the dialog was closed while the services were being looked for
            pass

    def chosen(self):
        return self.cluster.currentText().strip(), self.service.currentText().strip()

    def auth_choice(self):
        return auth_fields(self.auth_box, self.auth_env, self.auth_user)

    def _needs_ack(self):
        return plain_http_with_auth(self.service.currentText(), self.auth_box.currentData() or "")

    def _update_ack(self, *_):
        needed = self._needs_ack()
        self.http_warning.setVisible(needed)
        self.ack_box.setVisible(needed)

    def ack_choice(self):
        """True when the plain-http warning was accepted for the chosen source (only then is it kept)."""
        return bool(self._needs_ack() and self.ack_box.isChecked())

    def accept(self):
        if self._needs_ack() and not self.ack_box.isChecked():
            QMessageBox.warning(self, "Metrics source", "Tick the box to accept that the token or password is sent unencrypted over "
                                "http://, or use https://.")
            return
        super().accept()


class MetricsPanel(QWidget):
    def __init__(self, metrics, contexts, parent=None, tester=None, discoverer=None):
        super().__init__(parent)
        self.metrics = metrics
        self.contexts = contexts  # None when the kubeconfig could not be read
        self.tester = tester  # tester(context or None, text, done(message), auth): None = testing is not available
        self.discoverer = discoverer  # discoverer(name, done): the services found on a connected cluster
        self.default_ref, self.default_via, self.default_local = metrics.get_default()
        self.default_auth = metrics.get_default_auth()
        self.changes = {}  # context -> entry to keep, or None to remove its own entry
        self.saved = dict(metrics.entries())

        self.mode = QComboBox()
        self.mode.addItems([AUTOMATIC, PROMETHEUS, METRICS_ONLY])
        self.mode.setCurrentIndex(2 if self.default_ref == METRICS_SERVER else 1 if self.default_ref else 0)
        self.default_edit = QLineEdit("" if self.default_ref == METRICS_SERVER else self.default_ref)
        self.default_edit.setPlaceholderText("namespace/service:port[/prefix], or https://host/prefix")
        self.default_edit.setAccessibleName("Default Prometheus, VictoriaMetrics or URL")
        self.default_test = QPushButton("Test")
        self.default_test.clicked.connect(self._test_default)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.metrics_only_note = QLabel("metrics-server only: each cluster's own metrics-server is used, the one of the connected cluster, "
                                        "not a central one. A cluster that is not connected has no metrics.")
        self.metrics_only_note.setWordWrap(True)
        default_row = QHBoxLayout()
        default_row.addWidget(self.default_edit, 1)
        default_row.addWidget(self.default_test)
        defaults = QFormLayout()
        defaults.addRow("Default for all clusters", self.mode)
        defaults.addRow("Service or URL", default_row)
        defaults.addRow(self.metrics_only_note)
        self.default_auth_box, self.default_env, self.default_user = _auth_widgets(defaults, self.default_auth)
        self.default_ack = QCheckBox(HTTP_ACK)
        self.default_ack.setChecked(metrics.get_default_ack())
        self.default_warning = QLabel(HTTP_WARNING)
        self.default_warning.setWordWrap(True)
        defaults.addRow(self.default_warning)
        defaults.addRow(self.default_ack)
        self.default_edit.textChanged.connect(self._update_default_ack)
        self.default_auth_box.currentIndexChanged.connect(self._update_default_ack)
        self.mode.currentIndexChanged.connect(self._update_default_ack)
        self._update_default_ack()
        self._mode_changed()

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Cluster", "Source", "Status", ""])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.doubleClicked.connect(lambda *_: self.edit_selected())
        self.table.itemSelectionChanged.connect(self._update_buttons)

        self.add_button = QPushButton("Add…")
        self.edit_button = QPushButton("Edit…")
        self.remove_button = QPushButton("Remove")
        self.unused_button = QPushButton()
        self.add_button.clicked.connect(self.add_source)
        self.edit_button.clicked.connect(self.edit_selected)
        self.remove_button.clicked.connect(self.remove_selected)
        self.unused_button.clicked.connect(self.remove_unused)
        actions = QHBoxLayout()
        for button in (self.add_button, self.edit_button, self.remove_button, self.unused_button):
            actions.addWidget(button)
        actions.addStretch(1)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.result.setAccessibleName("Test result")
        note = QLabel("Test checks the source on a connected cluster (a URL is checked from this computer) and saves nothing. "
                      "An empty source means: look for one automatically. Remove forgets the saved setting after a Yes/No.")
        note.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addLayout(defaults)
        layout.addWidget(QLabel("Per cluster"))
        layout.addWidget(self.table, 1)
        layout.addLayout(actions)
        layout.addWidget(self.result)
        layout.addWidget(note)
        self._refresh()

    # --- state -------------------------------------------------------------------------------------------------------

    def _mode_changed(self, *_):
        self.default_edit.setEnabled(self.mode.currentIndex() == 1)
        self.default_test.setEnabled(self.mode.currentIndex() != 0)
        self.default_auth_box.setEnabled(self.mode.currentIndex() == 1)
        self.metrics_only_note.setVisible(self.mode.currentIndex() == 2)

    def _names(self):
        return sorted(set(self.saved) | set(self.changes))

    def _entry(self, name):
        if name in self.changes:
            return self.changes[name]
        return self.saved.get(name)

    def _is_stale(self, name):
        return self.contexts is not None and name not in self.contexts

    def _visible(self):
        return [name for name in self._names() if self._entry(name)]

    def stale_names(self):
        """Clusters with a saved entry that the kubeconfig no longer lists (none when the kubeconfig cannot be read)."""
        return [name for name in self._visible() if self._is_stale(name)]

    def _refresh(self):
        names = self._visible()
        self.table.setRowCount(len(names))
        for row, name in enumerate(names):
            entry = self._entry(name)
            if self.contexts is None:
                status = "Kubeconfig not readable"
            else:
                status = "Not in kubeconfig" if self._is_stale(name) else "In kubeconfig"
            source = "metrics-server only" if entry["ref"] == METRICS_SERVER else entry["ref"]
            if entry.get("auth"):
                source += f"  ({entry['auth']})"
            for column, value in enumerate((name, source, status)):
                self.table.setItem(row, column, QTableWidgetItem(value))
            test = QPushButton("Test")
            test.clicked.connect(partial(self._test_row, name))
            self.table.setCellWidget(row, 3, test)
        self.table.clearSelection()
        self.unused_button.setText(f"Remove unused entries ({len(self.stale_names())})")
        self._update_buttons()

    def _update_buttons(self, *_):
        selected = bool(self.table.selectedItems())
        self.edit_button.setEnabled(selected)
        self.remove_button.setEnabled(selected)
        self.unused_button.setEnabled(bool(self.stale_names()))

    def _selected_name(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        return self.table.item(rows[0].row(), 0).text()

    def _chosen_auth(self):
        return auth_fields(self.default_auth_box, self.default_env, self.default_user)

    # --- test --------------------------------------------------------------------------------------------------------

    def _show_result(self, message):
        try:
            self.result.setText(str(message))
        except RuntimeError:  # the panel was closed while the check ran
            pass

    def _run_test(self, context, text, auth=NO_AUTH):
        if self.tester is None:
            self._show_result("Testing is not available here.")
            return
        self._show_result("Testing…")
        self.tester(context, text, self._show_result, auth)

    def _test_default(self):
        index = self.mode.currentIndex()
        if index == 2:
            self._run_test(None, METRICS_SERVER)
            return
        try:
            text = valid_source(self.default_edit.text()) if index == 1 else ""
        except ValueError as error:
            QMessageBox.warning(self, "Metrics source", str(error))
            return
        if not text:
            self._show_result("Nothing to test: the default is to look for one automatically.")
            return
        if self._default_needs_ack() and not self.default_ack.isChecked():
            self._show_result("Tick the plain-http box first: the token or password would be sent unencrypted.")
            return
        self._run_test(None, text, self._chosen_auth())

    def _test_row(self, name):
        entry = self._entry(name)
        if entry and plain_http_with_auth(entry["ref"], entry.get("auth", "")) and not entry.get("ack_http"):
            self._show_result("Accept the plain-http warning first: use Edit… for this cluster.")
            return
        if entry:
            self._run_test(name, entry["ref"], (entry.get("auth", ""), entry.get("auth_env", ""), entry.get("auth_user", "")))

    # --- actions -----------------------------------------------------------------------------------------------------

    def add_source(self, preselect=None):
        """Add a source for a cluster that has none: a connected cluster offers the services found on it, others take a typed address.
        `preselect` is the cluster to start with; when it already has a source, its Edit… dialog opens instead."""
        if preselect and self._entry(preselect) is not None:
            self._edit(preselect)
            return
        names = [name for name in (self.contexts or []) if self._entry(name) is None]
        dialog = AddSourceDialog(names, self.discoverer, self, cluster=preselect or "")
        accepted = dialog.exec() == QDialog.Accepted
        name, text = dialog.chosen()
        kind, variable, user = dialog.auth_choice()
        ack = dialog.ack_choice()
        dialog.deleteLater()
        if not accepted:
            return
        if not name:
            QMessageBox.warning(self, "Metrics source", "Choose or type the cluster.")
            return
        if self._entry(name) is not None:
            QMessageBox.warning(self, "Metrics source", f"{name} already has a source: use Edit… to change it.")
            return
        try:
            text = valid_source(text)
        except ValueError as error:
            QMessageBox.warning(self, "Metrics source", str(error))
            return
        self.changes[name] = self._entry_dict(text, "proxy", 0, kind, variable, user, ack=ack)
        self._refresh()

    def edit_selected(self):
        """Edit the selected cluster's source: the same dialog as Add…, with the cluster fixed."""
        name = self._selected_name()
        if name:
            self._edit(name)

    def _edit(self, name):
        entry = self._entry(name)
        dialog = AddSourceDialog([name], self.discoverer, self, cluster=name, service=entry["ref"],
                                 auth=(entry.get("auth", ""), entry.get("auth_env", ""), entry.get("auth_user", "")), fixed_cluster=True,
                                 ack=entry.get("ack_http", False))
        accepted = dialog.exec() == QDialog.Accepted
        _name, text = dialog.chosen()
        kind, variable, user = dialog.auth_choice()
        ack = dialog.ack_choice()
        dialog.deleteLater()
        if not accepted:
            return
        text = text.strip()
        if not text:
            self.changes[name] = None  # empty: use the default again
        else:
            try:
                text = valid_source(text)
            except ValueError as error:
                QMessageBox.warning(self, "Metrics source", str(error))
                return
            self.changes[name] = self._entry_dict(text, entry.get("via", "proxy"), entry.get("local", 0), kind, variable, user,
                                                  tls=entry.get("tls", ""), insecure=entry.get("insecure", ""), ack=ack)
        self._refresh()

    @staticmethod
    def _entry_dict(text, via, local, kind, variable, user, tls="", insecure="", ack=False):
        url = text.lower().startswith(("http://", "https://"))
        kind, variable, user = (kind, variable, user) if url else NO_AUTH
        return {"ref": text, "via": via if not url else "proxy", "local": local, "tls": tls, "insecure": insecure,
                "auth": kind, "auth_env": variable, "auth_user": user,
                "ack_http": bool(ack) and plain_http_with_auth(text, kind)}

    def remove_selected(self):
        name = self._selected_name()
        if not name:
            return
        answer = QMessageBox.question(self, "Remove source",
                                      f"Remove the saved source for {name}? It will use the default again.",
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            self.changes[name] = None
            self._refresh()

    def remove_unused(self):
        names = self.stale_names()
        if not names:
            return
        answer = QMessageBox.question(self, "Remove unused entries",
                                      "Remove the saved sources for clusters that are not in the kubeconfig?\n\n" + "\n".join(names),
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer == QMessageBox.Yes:
            for name in names:
                self.changes[name] = None
            self._refresh()

    # --- saving ------------------------------------------------------------------------------------------------------

    def _chosen_default(self):
        index = self.mode.currentIndex()
        if index == 0:
            return ""
        if index == 2:
            return METRICS_SERVER
        text = self.default_edit.text().strip()
        if not text:
            raise ValueError("Type the service or URL for the default, or choose Find it automatically.")
        return valid_source(text)  # ValueError says what is wrong

    def save(self):
        """Write the default and the staged per-cluster changes. Raises ValueError for a bad address or an unaccepted plain-http
        authentication, OSError when not written."""
        default = self._chosen_default()
        auth = self._chosen_auth() if default and default != METRICS_SERVER else NO_AUTH
        ack = bool(default and self.default_ack.isChecked() and plain_http_with_auth(default, auth[0]))
        if self._default_needs_ack() and not self.default_ack.isChecked():
            raise ValueError("Tick the plain-http box for the default first: the token or password would be sent unencrypted.")
        if (default, auth, ack) != (self.default_ref, self.default_auth, self.metrics.get_default_ack()):
            keep = bool(default) and default != METRICS_SERVER
            self.metrics.set_default(default, via=self.default_via if keep else "proxy", local=self.default_local if keep else 0,
                                     auth=auth[0], auth_env=auth[1], auth_user=auth[2], ack_http=ack)
        for name, entry in self.changes.items():
            if entry is None:
                self.metrics.set(name, "")
            else:
                if plain_http_with_auth(entry["ref"], entry.get("auth", "")) and not entry.get("ack_http"):
                    raise ValueError(f"Accept the plain-http warning for {name} first (use Edit…).")
                self.metrics.set(name, entry["ref"], via=entry["via"], local=entry["local"], tls=entry["tls"], insecure=entry["insecure"],
                                 auth=entry.get("auth", ""), auth_env=entry.get("auth_env", ""), auth_user=entry.get("auth_user", ""),
                                 ack_http=entry.get("ack_http", False))
        self.changes = {}

    def _default_needs_ack(self):
        return self.mode.currentIndex() == 1 and plain_http_with_auth(self.default_edit.text().strip(), self._chosen_auth()[0])

    def _update_default_ack(self, *_):
        needed = self._default_needs_ack()
        self.default_warning.setVisible(needed)
        self.default_ack.setVisible(needed)
