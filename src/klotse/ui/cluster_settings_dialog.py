import re

from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox, QVBoxLayout)

from .dialog_sizing import resize_dialog


def setting_rows(form, settings, saved):
    """A text box (with its help below it) per Setting in `form`, filled from `saved`; returns {key: QLineEdit}."""
    fields = {}
    for setting in settings:
        edit = QLineEdit(saved.get(setting.key, ""))
        edit.setToolTip(setting.help)
        fields[setting.key] = edit
        form.addRow(setting.label, edit)
        if setting.help:
            hint = QLabel(setting.help)
            hint.setWordWrap(True)
            hint.setEnabled(False)
            form.addRow("", hint)
    return fields


def invalid_value(settings, values):
    """The message for the first value that does not match its Setting's pattern, else ""."""
    for setting in settings:
        value = values.get(setting.key, "")
        if value and setting.pattern and not re.fullmatch(setting.pattern, value):
            hint = setting.help or "it does not match the format this plugin expects"  # never the raw regular expression (#500)
            return f"{setting.label}: “{value}” is not valid ({hint})"
    return ""


class ClusterSettingsDialog(QDialog):
    """A plugin's settings for one cluster (the cluster list's menu -> "<plugin>: settings for this cluster…"): a text box
    per `Plugin.cluster_settings` entry. A cloud's plugin (API 14 `Plugin.cloud`) uses CloudSettingsDialog instead.

    An empty box means "not set for this cluster": the plugin falls back to its general settings. Save writes through
    `PluginManager.set_cluster_settings`, which makes every open list ask the plugin again."""

    def __init__(self, manager, plugin, context, parent=None):
        super().__init__(parent)
        self.manager, self.plugin, self.context = manager, plugin, context
        self.setWindowTitle(f"{plugin.name or plugin.id} — {context}")
        layout = QVBoxLayout(self)
        intro = QLabel(f"Settings of {plugin.name or plugin.id} for the cluster <b>{context}</b> only. "
                       f"Leave a box empty to use the plugin's general settings.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        layout.addLayout(form)
        self.fields = setting_rows(form, plugin.cluster_settings, manager.cluster_settings_for(plugin.id, context))
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        resize_dialog(self, max(480, self.sizeHint().width()), self.sizeHint().height())

    def values(self):
        return {key: edit.text().strip() for key, edit in self.fields.items()}

    def save(self):
        values = self.values()
        problem = invalid_value(self.plugin.cluster_settings, values)
        if problem:
            QMessageBox.warning(self, "Cannot save", problem)
            return
        try:
            self.manager.set_cluster_settings(self.plugin.id, self.context, values)
        except OSError as error:
            QMessageBox.warning(self, "Cannot save the plugin settings", str(error))
            return
        self.accept()
