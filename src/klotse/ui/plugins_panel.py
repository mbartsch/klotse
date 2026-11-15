from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget)

from . import theme

WARNING = ("Plugins are code that runs with your permissions and can see everything Klotse shows. "
           "Enable only plugins you trust. Ticking a plugin and changing its settings take effect when you press Save in "
           "Preferences; Cancel leaves them as they were.")


class PluginsPanel(QWidget):
    """The Plugins page of Preferences: every plugin that was found, whether it is enabled, its settings, and why one
    failed. Ticks and settings are kept here until `apply()` (Preferences' Save)."""

    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self._wanted = {}  # plugin id -> enabled, for ticks not applied yet
        self._wanted_settings = {}  # (plugin id, key) -> value, for settings changed and not applied yet
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
        self.error.setStyleSheet(f"color: {theme.BAD};")
        for label in (self.title, self.description, self.source, self.provides, self.error):
            label.setTextFormat(Qt.PlainText)
        self.settings_box = QGroupBox("Settings")
        self.settings_form = QFormLayout(self.settings_box)
        details = QWidget()
        side = QVBoxLayout(details)
        side.setContentsMargins(8, 0, 0, 0)
        summary = QGroupBox("Plugin summary")
        summary_layout = QVBoxLayout(summary)
        for widget in (self.title, self.description, self.source, self.provides):
            summary_layout.addWidget(widget)
        side.addWidget(summary)
        side.addWidget(self.settings_box)
        self.section_boxes = {}
        self.section_layout = QVBoxLayout()
        side.addLayout(self.section_layout)
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
        layout.addWidget(self.error)  # status remains visible while settings scroll
        error_actions = QHBoxLayout()
        self.retry_error = QPushButton("Retry plugin")
        self.retry_error.setToolTip("Retry loading the plugin or refresh its data in connected clusters")
        self.retry_error.clicked.connect(self._retry_error)
        self.dismiss_error = QPushButton("Dismiss error")
        self.dismiss_error.setToolTip("Dismiss this message; this does not fix the underlying problem")
        self.dismiss_error.clicked.connect(self._dismiss_error)
        error_actions.addWidget(self.retry_error)
        error_actions.addWidget(self.dismiss_error)
        error_actions.addStretch(1)
        layout.addLayout(error_actions)
        layout.addLayout(body, 1)
        layout.addWidget(self.empty)
        layout.addLayout(buttons)
        manager.errors_changed.connect(self._refresh_details)
        manager.changed.connect(self._manager_changed)
        self.populate()

    # ---- the list ------------------------------------------------------------
    def populate(self, select=None):
        self.list.blockSignals(True)
        self.list.clear()
        for found in self.manager.loaded:
            plugin = found.manifest or found.plugin
            item = QListWidgetItem(plugin.name or found.id if plugin else f"⚠ {found.id}")
            item.setData(Qt.UserRole, found)
            if plugin is not None:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if self._shown_enabled(found) else Qt.Unchecked)
            else:  # it did not load: it can be read about but not enabled
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self.empty.setText("" if self.manager.loaded else (
            "No plugins found. Add a Python plugin and its JSON manifest to the plugins folder, or install a "
            "package with klotse.plugins and klotse.plugin_manifests entry points."))
        wanted = next((i for i in range(self.list.count())
                       if select and self.list.item(i).data(Qt.UserRole).id == select), 0)
        if self.list.count():
            self.list.setCurrentRow(wanted)
        else:
            self._show_details(None, None)
        self._manager_changed()

    def _manager_changed(self):
        """Reflect asynchronous loading without replacing settings boxes while the user is editing them."""
        items = [self.list.item(i) for i in range(self.list.count())]
        if len(items) != len(self.manager.loaded) or any(
                item.data(Qt.UserRole) is not found for item, found in zip(items, self.manager.loaded)):
            current = self._current()
            self.populate(select=current.id if current else None)
            return
        self.list.blockSignals(True)
        try:
            for item in items:
                found = item.data(Qt.UserRole)
                plugin = found.manifest or found.plugin
                name = plugin.name or found.id if plugin else f"⚠ {found.id}"
                item.setText(f"{name} (loading…)" if found.loading else name)
                if plugin is not None:
                    item.setCheckState(Qt.Checked if self._shown_enabled(found) else Qt.Unchecked)
        finally:
            self.list.blockSignals(False)
        self._refresh_details()

    def reload(self):
        current = self._current()
        self.manager.reload()
        self.populate(select=current.id if current else None)

    def _current(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _shown_enabled(self, found):
        if found.id in self._wanted:
            return self._wanted[found.id]
        return bool(found.loading or self.manager.is_enabled(found.id))

    def _toggled(self, item):
        found = item.data(Qt.UserRole)
        if found.manifest is None and found.plugin is None:
            return
        enabled = item.checkState() == Qt.Checked
        if enabled == bool(found.loading or self.manager.is_enabled(found.id)):
            self._wanted.pop(found.id, None)  # back to what is applied
        else:
            self._wanted[found.id] = enabled
        self._refresh_details()

    def apply(self):
        """Enable or disable the plugins ticked since the last apply (Preferences' Save). False, with a message, when one
        could not be saved; the ticks that failed stay pending."""
        for (plugin_id, key), value in list(self._wanted_settings.items()):
            try:
                self.manager.set_setting(plugin_id, key, value)
            except OSError as error:
                QMessageBox.warning(self, "Cannot save", str(error))
                return False
            del self._wanted_settings[(plugin_id, key)]
        for plugin_id, enabled in list(self._wanted.items()):
            try:
                self.manager.set_enabled(plugin_id, enabled)
            except OSError as error:
                QMessageBox.warning(self, "Cannot save", str(error))
                return False
            except ValueError as error:
                QMessageBox.warning(self, "Cannot enable plugin", str(error))
                return False
            del self._wanted[plugin_id]
        self._refresh_details()
        return True

    # ---- the details of the selected plugin ---------------------------------------
    def _show_details(self, item, _previous):
        found = item.data(Qt.UserRole) if item is not None else None
        while self.settings_form.rowCount():
            self.settings_form.removeRow(0)
        self.settings_box.setVisible(False)
        for box, _form in self.section_boxes.values():
            self.section_layout.removeWidget(box)
            box.deleteLater()
        self.section_boxes.clear()
        if found is None:
            for label in (self.title, self.description, self.source, self.provides, self.error):
                label.clear()
            self._refresh_details()
            return
        plugin = found.manifest or found.plugin
        self.title.setText(plugin.name or plugin.id if plugin else found.id)
        self.description.setText(plugin.description if plugin else "")
        self.source.setText(f"Source: {found.source}: {found.origin}" + (f"\nVersion: {plugin.version}" if plugin and plugin.version else ""))
        self.provides.setText(self._provides_text(plugin) if plugin else "")
        self._refresh_details()
        if plugin is None:
            return
        values = {**self.manager.settings_for(plugin.id),
                  **{key: value for (pid, key), value in self._wanted_settings.items() if pid == plugin.id}}
        pending_notes = []
        for setting in plugin.settings:
            if setting.section:
                if setting.section not in self.section_boxes:
                    box = QGroupBox(setting.section)
                    form = QFormLayout(box)
                    self.section_layout.addWidget(box)
                    self.section_boxes[setting.section] = (box, form)
                form = self.section_boxes[setting.section][1]
            else:
                form = self.settings_form
            if setting.kind == "note":  # a warning box that belongs to the settings below it; it holds no value
                note = QLabel(setting.label)
                note.setWordWrap(True)
                note.setTextFormat(Qt.RichText)
                note.setStyleSheet(theme.warning_style() + " padding:12px; font-size:14px;")
                pending_notes.append((form, note))
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
                widget.textChanged.connect(lambda w=widget, s=setting: self._save_setting(plugin.id, s.key, w.toPlainText()))
                widget.setObjectName("settingBox")
                widget.setStyleSheet("QPlainTextEdit#settingBox { border: 1px solid palette(mid); border-radius: 8px; "
                                     "padding: 4px; background: palette(base); }")
                widget.setLineWrapMode(QPlainTextEdit.WidgetWidth)  # long lines wrap by default
                wrap = QCheckBox("Wrap long lines")
                wrap.setChecked(True)
                wrap.toggled.connect(lambda on, w=widget: w.setLineWrapMode(QPlainTextEdit.WidgetWidth if on else QPlainTextEdit.NoWrap))
                holder = QWidget()
                column = QVBoxLayout(holder)
                column.setContentsMargins(0, 0, 0, 0)
                column.addWidget(widget)
                column.addWidget(wrap)
                widget.wrap_box = wrap
                label = setting.label
            else:
                widget = QLineEdit(str(values[setting.key]))
                if setting.kind == "password":
                    widget.setEchoMode(QLineEdit.Password)
                widget.editingFinished.connect(lambda w=widget, s=setting: self._save_setting(plugin.id, s.key, w.text()))
                label = setting.label
            if setting.help:
                widget.setToolTip(setting.help)
            if setting.kind == "multiline":  # the label on its own line, so the box has the full width
                form.addRow(QLabel(label))
                form.addRow(holder)
            elif label:
                form.addRow(label, widget)
            else:
                form.addRow(widget)
            remaining = []
            for note_form, note in pending_notes:
                if note_form is form:
                    note_form.addRow(note)
                else:
                    remaining.append((note_form, note))
            pending_notes = remaining
        for note_form, note in pending_notes:
            note_form.addRow(note)
        self.settings_box.setVisible(self.settings_form.rowCount() > 0)

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
        if plugin.overview_cards:
            lines.append("Overview cards: " + ", ".join(card.title for card in plugin.overview_cards))
        if plugin.cluster_settings:
            lines.append("Per-cluster settings: " + ", ".join(setting.label for setting in plugin.cluster_settings))
        return "\n".join(lines)

    def _refresh_details(self):
        found = self._current()
        problem = (found.error or self.manager.errors.get(found.id, "")) if found else ""
        self.error.setText(("Retrying plugin: " if found.loading else "Problem: ") + problem if problem else
                           ("Loading plugin..." if found and found.loading else ""))
        if found and problem and self.manager.has_action_error(found.id):
            self.error.setText(self.error.text() + "\nTo retry an action, run it again from its original menu.")
        self.dismiss_error.setVisible(bool(problem))
        can_retry = bool(found and ((found.plugin is None and found.manifest is not None and found.loader) or
                         (found.plugin and self.manager.is_enabled(found.id) and
                          self.manager.has_retryable_error(found.id))))
        self.retry_error.setVisible(bool(problem) and can_retry)
        self.retry_error.setEnabled(bool(found and not found.loading))

    def _dismiss_error(self):
        found = self._current()
        if found:
            self.manager.dismiss_error(found.id)

    def _retry_error(self):
        found = self._current()
        if found and self.manager.retry_plugin(found.id) and found.plugin is not None:
            self.error.setText(self.error.text() + "\nRetry requested in connected clusters. The error stays until a successful result or dismissal.")

    def _save_setting(self, plugin_id, key, value):
        self._wanted_settings[(plugin_id, key)] = value  # saved by apply()

    def open_plugins_folder(self):
        if self.manager.folder is None:
            return None
        from .preferences import open_folder  # preferences imports this module, so not at the top
        error = open_folder(self.manager.folder)
        if error:
            QMessageBox.warning(self, "Plugins folder", error)
        return error
