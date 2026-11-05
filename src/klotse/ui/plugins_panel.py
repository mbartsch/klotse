from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from . import theme

WARNING = ("Plugins are code that runs with your permissions and can see everything Klotse shows. "
           "Enable only plugins you trust. Changes on this page take effect immediately.")


class PluginsPanel(QWidget):
    """The Plugins page of Preferences: every plugin that was found, whether it is enabled, its settings, and why one
    failed. Unlike the other pages it applies each change at once (enabling a plugin must show its columns now)."""

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self._pending = []  # (timer, save) of multi-line settings that were typed in and not saved yet
        warning = QLabel(WARNING)
        warning.setWordWrap(True)
        self.list = QListWidget()
        self.list.setMaximumWidth(260)
        self.list.currentItemChanged.connect(self._show_details)
        self.list.itemChanged.connect(self._toggled)
        self.title = QLabel()
        self.title.setStyleSheet("font-weight: 600; font-size: 15px;")
        self.description = QLabel()
        self.description.setWordWrap(True)
        self.source = QLabel()
        self.source.setWordWrap(True)
        self.source.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.provides = QLabel()
        self.provides.setWordWrap(True)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #e5484d;")
        self.settings_box = QGroupBox("Settings")
        self.settings_form = QFormLayout(self.settings_box)
        details = QWidget()
        side = QVBoxLayout(details)
        side.setContentsMargins(8, 0, 0, 0)
        for widget in (self.title, self.description, self.source, self.provides, self.error, self.settings_box):
            side.addWidget(widget)
        side.addStretch(1)
        self.empty = QLabel("")
        self.empty.setWordWrap(True)
        body = QHBoxLayout()
        body.addWidget(self.list)
        scroll = QScrollArea()  # the settings can be taller than the dialog; scrolling keeps long notes from being squeezed
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(details)
        body.addWidget(scroll, 1)
        folder = QPushButton("Open plugins folder")
        folder.clicked.connect(self.open_plugins_folder)
        reload_button = QPushButton("Reload plugins")
        reload_button.setToolTip("Look for plugins again (after adding a file to the plugins folder)")
        reload_button.clicked.connect(self.reload)
        buttons = QHBoxLayout()
        buttons.addWidget(folder)
        buttons.addWidget(reload_button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(warning)
        layout.addLayout(body, 1)
        layout.addWidget(self.empty)
        layout.addLayout(buttons)
        manager.errors_changed.connect(self._refresh_details)
        self.populate()

    # ---- the list ------------------------------------------------------------
    def populate(self, select=None):
        self.list.blockSignals(True)
        self.list.clear()
        for found in self.manager.loaded:
            item = QListWidgetItem(found.plugin.name or found.id if found.plugin else f"⚠ {found.id}")
            item.setData(Qt.UserRole, found)
            if found.plugin is not None:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if self.manager.is_enabled(found.id) else Qt.Unchecked)
            else:  # it did not load: it can be read about but not enabled
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self.empty.setText("" if self.manager.loaded else (
            "No plugins found. Plugins are Python files in the plugins folder, or pip packages that declare a "
            "klotse.plugins entry point."))
        wanted = next((i for i in range(self.list.count())
                       if select and self.list.item(i).data(Qt.UserRole).id == select), 0)
        if self.list.count():
            self.list.setCurrentRow(wanted)
        else:
            self._show_details(None, None)

    def reload(self):
        current = self._current()
        self.manager.reload()
        self.populate(select=current.id if current else None)

    def _current(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _toggled(self, item):
        found = item.data(Qt.UserRole)
        if found.plugin is None:
            return
        enabled = item.checkState() == Qt.Checked
        try:
            self.manager.set_enabled(found.id, enabled)
        except OSError as error:
            QMessageBox.warning(self, "Cannot save", str(error))
            self.list.blockSignals(True)
            item.setCheckState(Qt.Unchecked if enabled else Qt.Checked)  # it was not saved: show the truth
            self.list.blockSignals(False)
            return
        self._refresh_details()

    # ---- the details of the selected plugin ---------------------------------------
    def flush(self):
        """Save multi-line settings that were typed in but whose timer has not fired yet (leaving the page, another plugin)."""
        for timer, save in list(self._pending):
            try:
                if timer.isActive():
                    timer.stop()
                    save()
            except RuntimeError:
                pass  # the box is already gone

    def hideEvent(self, event):
        self.flush()
        super().hideEvent(event)

    def _show_details(self, item, _previous):
        self.flush()
        self._pending = []  # the boxes of the plugin shown so far are about to be replaced
        found = item.data(Qt.UserRole) if item is not None else None
        while self.settings_form.rowCount():
            self.settings_form.removeRow(0)
        self.settings_box.setVisible(False)
        if found is None:
            for label in (self.title, self.description, self.source, self.provides, self.error):
                label.clear()
            return
        plugin = found.plugin
        self.title.setText(plugin.name or plugin.id if plugin else found.id)
        self.description.setText(plugin.description if plugin else "")
        self.source.setText(f"Source: {found.source}: {found.origin}" + (f"\nVersion: {plugin.version}" if plugin and plugin.version else ""))
        self.provides.setText(self._provides_text(plugin) if plugin else "")
        self._refresh_details()
        if plugin is None:
            return
        values = self.manager.settings_for(plugin.id)
        for setting in plugin.settings:
            if setting.kind == "note":  # a warning box that belongs to the settings below it; it holds no value
                note = QLabel(setting.label)
                note.setWordWrap(True)
                note.setTextFormat(Qt.RichText)
                note.setStyleSheet(theme.warning_style() + " padding:12px; font-size:14px;")
                self.settings_form.addRow(note)
                continue
            if setting.kind == "bool":
                box = QCheckBox(setting.label)
                box.setChecked(bool(values[setting.key]))
                box.toggled.connect(lambda on, s=setting: self._save_setting(plugin.id, s.key, on))
                widget, label = box, ""
            elif setting.kind == "multiline":
                widget = QPlainTextEdit(str(values[setting.key]))
                widget.setFixedHeight(96)
                widget.setTabChangesFocus(True)
                timer = QTimer(widget, singleShot=True, interval=700)  # one save after typing stops, not one per key
                save = lambda w=widget, s=setting: self._save_setting(plugin.id, s.key, w.toPlainText())  # noqa: E731
                timer.timeout.connect(save)
                widget.textChanged.connect(timer.start)
                widget.save_timer = timer
                self._pending.append((timer, save))
                label = setting.label
            else:
                widget = QLineEdit(str(values[setting.key]))
                if setting.kind == "password":
                    widget.setEchoMode(QLineEdit.Password)
                widget.editingFinished.connect(lambda w=widget, s=setting: self._save_setting(plugin.id, s.key, w.text()))
                label = setting.label
            if setting.help:
                widget.setToolTip(setting.help)
            if label:
                self.settings_form.addRow(label, widget)
            else:
                self.settings_form.addRow(widget)
        self.settings_box.setVisible(bool(plugin.settings))

    @staticmethod
    def _provides_text(plugin):
        """What the plugin adds, so the user knows what enabling it does (and which actions can change things)."""
        lines = []
        if plugin.columns:
            lines.append("Columns: " + ", ".join(f"{c.title} ({'/'.join(c.kinds)})" for c in plugin.columns))
        if plugin.actions:
            lines.append("Right-click actions: " + ", ".join(
                f"{'⚠ ' if a.dangerous else ''}{a.title} ({'/'.join(a.kinds)})" for a in plugin.actions))
            if any(a.dangerous for a in plugin.actions):
                lines.append("⚠ marks actions that can change things; they always ask before they run.")
        return "\n".join(lines)

    def _refresh_details(self):
        found = self._current()
        if found is None:
            return
        problem = found.error or self.manager.errors.get(found.id, "")
        self.error.setText(f"Problem: {problem}" if problem else "")

    def _save_setting(self, plugin_id, key, value):
        try:
            self.manager.set_setting(plugin_id, key, value)
        except OSError as error:
            QMessageBox.warning(self, "Cannot save", str(error))

    def open_plugins_folder(self):
        if self.manager.folder is None:
            return None
        from .preferences import open_folder  # preferences imports this module, so not at the top
        error = open_folder(self.manager.folder)
        if error:
            QMessageBox.warning(self, "Plugins folder", error)
        return error
