import re

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox, QVBoxLayout)


class ClusterSettingsDialog(QDialog):
    """A plugin's settings for one cluster (Plugins menu -> the plugin): a text box per `Plugin.cluster_settings` entry.

    An empty box means "not set for this cluster": the plugin falls back to its general settings. Save writes through
    `PluginManager.set_cluster_settings`, which makes every open list ask the plugin again."""

    def __init__(self, manager, plugin, context, parent=None):
        super().__init__(parent)
        self.manager, self.plugin, self.context = manager, plugin, context
        self.setWindowTitle(f"{plugin.name or plugin.id} — {context}")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        intro = QLabel(f"Settings of {plugin.name or plugin.id} for the cluster <b>{context}</b> only. "
                       f"Leave a box empty to use the plugin's general settings.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        layout.addLayout(form)
        saved = manager.cluster_settings_for(plugin.id, context)
        self.fields = {}
        for setting in plugin.cluster_settings:
            edit = QLineEdit(saved.get(setting.key, ""))
            edit.setToolTip(setting.help)
            self.fields[setting.key] = edit
            form.addRow(setting.label, edit)
            if setting.help:
                hint = QLabel(setting.help)
                hint.setWordWrap(True)
                hint.setEnabled(False)
                form.addRow("", hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return {key: edit.text().strip() for key, edit in self.fields.items()}

    def save(self):
        values = self.values()
        for setting in self.plugin.cluster_settings:
            value = values[setting.key]
            if value and setting.pattern and not re.fullmatch(setting.pattern, value):
                QMessageBox.warning(self, "Cannot save", f"{setting.label}: “{value}” is not valid ({setting.help or setting.pattern})")
                return
        try:
            self.manager.set_cluster_settings(self.plugin.id, self.context, values)
        except OSError as error:
            QMessageBox.warning(self, "Cannot save the plugin settings", str(error))
            return
        self.accept()
