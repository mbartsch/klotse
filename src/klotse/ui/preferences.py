from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from .plugins_panel import PluginsPanel
from .settings_store import default_store
from .terminal_settings import TerminalPanel
from .theme import ThemeError, template_text
from .dialog_sizing import resize_dialog


class KubeconfigSettings(QObject):
    """The kubeconfig Klotse reads contexts from: a custom file, or None for KUBECONFIG / ~/.kube/config.

    Saved as `kubeconfig/path` in the shared settings store. A saved file that no longer exists is ignored.
    """

    def __init__(self, settings=None, parent=None):
        super().__init__(parent)
        self.store = settings if settings is not None else default_store()
        saved = str(self.store.value("kubeconfig/path", "") or "")
        self.path = saved if saved and Path(saved).is_file() else None
        self.missing = saved if saved and self.path is None else None  # remembered so the UI can say why it is ignored

    def set_path(self, path):
        path = str(path or "").strip() or None
        if path and not Path(path).is_file():
            raise ValueError(f"Kubeconfig file not found: {path}")
        if path:
            self.store.setValue("kubeconfig/path", path)
        else:
            self.store.remove("kubeconfig/path")
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save preferences to {self.store.fileName()}")
        self.path, self.missing = path, None


class DebugSettings:
    """Debug options, `[debug]` in the settings file. `timings`: write `timings.log` (see `k8s/timing.py`). Off by default."""

    def __init__(self, settings=None):
        self.store = settings if settings is not None else default_store()
        self.timings = str(self.store.value("debug/timings", "false")).lower() in ("true", "1")

    def set_timings(self, on):
        self.store.setValue("debug/timings", bool(on))
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save preferences to {self.store.fileName()}")
        self.timings = bool(on)


def open_folder(folder):
    """Show a folder in Explorer / Finder / the file manager; returns an error message or None."""
    try:
        Path(folder).mkdir(parents=True, exist_ok=True)  # nothing is saved until the first customization
    except OSError as error:
        return f"Cannot create {folder}: {error}"
    return None if QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))) else f"Could not open {folder}"


class PreferencesDialog(QDialog):
    """Application preferences. Accepting only validates; the caller saves/applies `chosen_kubeconfig()`."""

    def __init__(self, kubeconfig, config_folder, parent=None, terminal=None, plugins=None, themes=None, debug=None, forward=None, query=None,
                 proxy=None, metrics=None):
        super().__init__(parent)
        self.debug = debug
        self.forward = forward  # Usage history through a port-forward: SSL and certificate checks (the per-cluster choice overrides them)
        self.kubeconfig, self.config_folder = kubeconfig, Path(config_folder)
        self.theme_settings = themes
        self.setWindowTitle("Preferences")
        general = QWidget()
        form = QFormLayout(general)

        self.path = QLineEdit(kubeconfig.path or "", readOnly=True)
        self.path.setPlaceholderText("Default (KUBECONFIG or ~/.kube/config)")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        default = QPushButton("Use default")
        default.clicked.connect(lambda: self.path.clear())
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        row.addWidget(default)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("Kubeconfig", holder)
        self.note = QLabel("Changing the kubeconfig closes the open clusters and reloads the context list.\n"
                           "kubectl commands (shell, attach, drain) use the same file.")
        self.note.setWordWrap(True)
        if kubeconfig.missing:
            self.note.setText(f"Saved kubeconfig not found, using the default: {kubeconfig.missing}\n" + self.note.text())
        form.addRow(self.note)

        self.proxy_settings = proxy
        if proxy is not None:
            self.proxy_url = QLineEdit(proxy.url or "")
            self.proxy_url.setPlaceholderText("None: use the kubeconfig or environment, for example http://proxy.corp:3128 or socks5://proxy.corp:1080")
            self.proxy_url.setAccessibleName("HTTP proxy address")
            self.no_proxy_edit = QLineEdit(proxy.no_proxy_text())
            self.no_proxy_edit.setPlaceholderText("localhost, 10.0.0.0/8")
            self.no_proxy_edit.setAccessibleName("Hosts that bypass the proxy")
            form.addRow("HTTP proxy", self.proxy_url)
            form.addRow("Bypass proxy for", self.no_proxy_edit)
            proxy_note = QLabel("Used for clusters whose kubeconfig names no proxy-url. Clusters already open keep the proxy "
                                "they connected with; reconnect to use a new one.")
            proxy_note.setWordWrap(True)
            form.addRow(proxy_note)
            spdy_note = QLabel("Kubernetes versions before 1.31 stream exec, attach, cp and port-forward over SPDY, which "
                               "a proxy may not carry reliably. "
                               '<a href="https://kubernetes.io/blog/2024/08/20/websockets-transition/">'
                               "Kubernetes: transition from SPDY to WebSockets</a>")
            spdy_note.setWordWrap(True)
            spdy_note.setOpenExternalLinks(True)
            form.addRow(spdy_note)

        if themes is not None:
            self._theme_rows(form)
        self.forward_tls_box = self.forward_insecure_box = None
        if forward is not None:
            self.forward_tls_box = QCheckBox("Use SSL (https) for usage history through a port-forward")
            self.forward_tls_box.setChecked(forward.tls)
            self.forward_insecure_box = QCheckBox("Ignore certificate errors for it (self-signed or internal certificates)")
            self.forward_insecure_box.setChecked(forward.insecure)
            self.forward_tls_box.setToolTip("The default for every cluster. A cluster can override it in its Source… dialog.")
            holder = QWidget()
            column = QVBoxLayout(holder)
            column.setContentsMargins(0, 0, 0, 0)
            column.addWidget(self.forward_tls_box)
            column.addWidget(self.forward_insecure_box)
            form.addRow("Usage history", holder)
        self.query_autocomplete_box = None
        if query is not None:
            self.query_defaults = query
            self.query_autocomplete_box = QCheckBox("Metrics query: suggest metric names while typing (asks the source for every name, once per window)")
            self.query_autocomplete_box.setChecked(query.autocomplete)
            self.query_autocomplete_box.setToolTip("Off by default. A large Prometheus or VictoriaMetrics can have a very long list of names.")
            form.addRow("Metrics query", self.query_autocomplete_box)
        self.open_button = QPushButton("Open config folder")
        self.open_button.setToolTip("Show Klotse's settings file in Explorer / Finder / the file manager (debug)")
        self.open_button.clicked.connect(self._open_folder)
        form.addRow("Settings", self.open_button)
        form.addRow("", QLabel(str(self.config_folder)))
        self.timing_box = None
        if debug is not None:
            self.timing_box = QCheckBox("Write a timing log (debug)")
            self.timing_box.setChecked(debug.timings)
            self.timing_box.setToolTip("Writes timings.log next to the settings file: how long each load takes and where the time goes. "
                                       "Only names, counts and seconds, never object names or data. Takes effect when you press Save.")
            form.addRow("Debug", self.timing_box)

        # More pages when the caller has something to configure: the macOS terminal, the plugins. With only the General
        # page there is no tab bar.
        self.terminal_panel = TerminalPanel(terminal) if terminal is not None else None
        self.plugins_panel = PluginsPanel(plugins) if plugins is not None else None
        self.tabs = QTabWidget()
        self.tabs.addTab(general, "General")
        self.metrics_panel = None
        if metrics is not None:
            from .metrics_preferences import MetricsPanel, kubeconfig_contexts
            self.metrics_panel = MetricsPanel(metrics, kubeconfig_contexts(kubeconfig.path))
            self.tabs.addTab(self.metrics_panel, "Metrics")
        if self.terminal_panel is not None:
            self.tabs.addTab(self.terminal_panel, "Terminal")
        if self.plugins_panel is not None:
            self.tabs.addTab(self.plugins_panel, "Plugins")
        self.tabs.tabBar().setVisible(self.tabs.count() > 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons = buttons
        self.tabs.currentChanged.connect(self._update_button_labels)
        self._update_button_labels()
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(buttons)
        resize_dialog(self, 780, 560) if plugins is not None else resize_dialog(self, 640, 0)

    def _update_button_labels(self, *_):
        """Make clear that Cancel applies to the other preference pages, not Plugins."""
        on_plugins = self.plugins_panel is not None and self.tabs.currentWidget() is self.plugins_panel
        self.buttons.button(QDialogButtonBox.Save).setText("Save other changes" if on_plugins else "Save")
        self.buttons.button(QDialogButtonBox.Cancel).setText("Cancel other changes" if on_plugins else "Cancel")

    def _theme_rows(self, form):
        self.theme_box = QComboBox()
        self.theme_box.currentTextChanged.connect(self._theme_changed)
        open_folder_button = QPushButton("Open themes folder")
        open_folder_button.clicked.connect(self._open_themes_folder)
        export = QPushButton("Export current theme…")
        export.setToolTip("Save the selected theme as a file to edit; put it in the themes folder to use it")
        export.clicked.connect(self._export_theme)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.theme_box, 1)
        row.addWidget(open_folder_button)
        row.addWidget(export)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("Theme", holder)
        self.theme_note = QLabel()
        self.theme_note.setWordWrap(True)
        form.addRow(self.theme_note)
        self.reload_themes(select=self.theme_settings.selected().name)

    def reload_themes(self, select=None):
        """Fill the theme list from the built-in themes and the themes folder; unusable files are listed below it."""
        themes, problems = self.theme_settings.scan()
        keep = select or self.theme_box.currentText()
        self.theme_box.blockSignals(True)
        self.theme_box.clear()
        self.theme_box.addItems(list(themes))
        if keep in themes:
            self.theme_box.setCurrentText(keep)
        self.theme_box.blockSignals(False)
        self._problems = problems
        self._theme_changed()

    def _theme_changed(self, *_):
        themes = self.theme_settings.themes()
        chosen = themes.get(self.theme_box.currentText())
        lines = [f"Theme files (.ini) go in {self.theme_settings.folder}. The theme applies when you save."]
        if chosen is not None and chosen.source:
            lines.append(f"Loaded from {chosen.source}")
        lines += [f"Ignored {name}: {message}" for name, message in self._problems]
        self.theme_note.setText("\n".join(lines))

    def _open_themes_folder(self):
        error = open_folder(self.theme_settings.folder)
        if error:
            QMessageBox.warning(self, "Cannot open themes folder", error)

    def _export_theme(self):
        chosen = self.theme_settings.themes().get(self.theme_box.currentText())
        if chosen is None:
            return
        folder = self.theme_settings.folder
        start = str(folder / (chosen.name.lower().replace(" ", "-") + "-copy.ini"))
        path, _ = QFileDialog.getSaveFileName(self, "Export theme", start, "Theme files (*.ini)")
        if not path:
            return
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(template_text(chosen), encoding="utf-8")
        except OSError as error:
            QMessageBox.warning(self, "Cannot export theme", str(error))
            return
        if Path(path).parent == folder:
            self.reload_themes()

    def chosen_theme(self):
        """The theme name picked in the dialog, or None when the dialog has no theme list."""
        return self.theme_box.currentText() if self.theme_settings is not None else None

    def _browse(self):
        start = self.path.text() or str(Path.home() / ".kube")
        chosen, _ = QFileDialog.getOpenFileName(self, "Select kubeconfig", start, "All files (*)")
        if chosen:
            self.path.setText(chosen)

    def _open_folder(self):
        error = open_folder(self.config_folder)
        if error:
            QMessageBox.warning(self, "Cannot open config folder", error)

    def chosen_kubeconfig(self):
        """The kubeconfig path picked in the dialog (None = default)."""
        return self.path.text().strip() or None

    def _save(self):
        chosen = self.chosen_kubeconfig()
        if chosen and not Path(chosen).is_file():
            QMessageBox.warning(self, "Cannot save preferences", f"Kubeconfig file not found: {chosen}")
            return
        if self.terminal_panel is not None:
            try:
                self.terminal_panel.save()
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, "Cannot save preferences", str(error))
                return
        if self.proxy_settings is not None:
            try:
                self.proxy_settings.set(self.proxy_url.text(), self.no_proxy_edit.text())
            except ValueError as error:
                QMessageBox.warning(self, "Cannot save preferences", str(error))
                return
        if self.timing_box is not None:
            try:
                self.debug.set_timings(self.timing_box.isChecked())
            except OSError as error:
                QMessageBox.warning(self, "Cannot save preferences", str(error))
                return
        if self.forward_tls_box is not None:
            try:
                self.forward.set(self.forward_tls_box.isChecked(), self.forward_insecure_box.isChecked())
            except OSError as error:
                QMessageBox.warning(self, "Cannot save preferences", str(error))
                return
        if self.metrics_panel is not None:
            try:
                self.metrics_panel.save()
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, "Cannot save preferences", str(error))
                return
        if self.query_autocomplete_box is not None:
            try:
                self.query_defaults.set(self.query_autocomplete_box.isChecked())
            except OSError as error:
                QMessageBox.warning(self, "Cannot save preferences", str(error))
                return
        self.accept()  # the caller saves and applies the kubeconfig (closing open clusters needs confirmation first)
