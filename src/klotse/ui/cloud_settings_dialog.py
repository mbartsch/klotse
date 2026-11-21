"""Cloud settings for one cluster (#818): which cloud it runs in, and only that cloud's own settings for the cluster.

One entry in the cluster list's menu replaces a "<plugin>: settings for this cluster…" entry per cloud plugin. The Cloud
choice is Klotse's (PluginManager.set_cluster_cloud) and reaches every cloud plugin as settings["cloud"]; "Auto" keeps the
plugins' own check of each node (its providerID) and shows the settings of the cloud the kubeconfig names."""
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QMessageBox, QVBoxLayout,
                               QWidget)

from ..k8s.cloud_detect import AUTO, CLOUDS, effective
from .cluster_settings_dialog import invalid_value, setting_rows
from .dialog_sizing import resize_dialog

ORDER = tuple(CLOUDS)  # aws, azure, gcp, oci


def cloud_name(cloud):
    return CLOUDS.get(cloud, cloud)


class CloudSettingsDialog(QDialog):
    """`detected` is (cloud id, reason) from cloud_detect.detect, ("", "") when the kubeconfig does not say."""

    def __init__(self, manager, context, detected=("", ""), parent=None):
        super().__init__(parent)
        self.manager, self.context = manager, context
        self.detected, self.reason = detected
        self.plugins = {plugin.cloud: plugin for plugin in manager.cloud_plugins()}
        self.setWindowTitle(f"Cloud settings — {context}")
        layout = QVBoxLayout(self)
        intro = QLabel(f"The cloud the cluster <b>{context}</b> runs in, and that cloud's settings for this cluster only.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        layout.addLayout(form)
        self.choice = QComboBox()
        self.choice.setObjectName("cloudChoice")
        auto = f"Auto: {cloud_name(self.detected)}, {self.reason}" if self.detected else "Auto: not detected from the kubeconfig"
        self.choice.addItem(auto, AUTO)
        saved = manager.cluster_cloud(context)
        for cloud in sorted(set(self.plugins) | ({saved} - {AUTO}), key=lambda c: (ORDER.index(c) if c in ORDER else 99, c)):
            text = cloud_name(cloud) + ("" if cloud in self.plugins else " (its plugin is not enabled)")
            self.choice.addItem(text, cloud)
        self.choice.setCurrentIndex(max(0, self.choice.findData(saved)))
        form.addRow("Cloud", self.choice)
        hint = QLabel("Auto chooses the settings below from the kubeconfig, and the cloud plugins check each node's cloud "
                      "themselves. Choose a cloud when the nodes do not report it (a local emulator, a custom node pool).")
        hint.setWordWrap(True)
        hint.setEnabled(False)
        form.addRow("", hint)

        self.pages = QWidget()  # one page per cloud; only the chosen one is visible, so only it counts for the size
        self.pages.setObjectName("cloudSettingsPages")
        pages = QVBoxLayout(self.pages)
        pages.setContentsMargins(0, 0, 0, 0)
        self.current_page = None
        self.fields = {}  # cloud -> {key: QLineEdit}: typed text stays when the choice changes back
        self._page = {}
        for cloud, plugin in self.plugins.items():
            page = QWidget()
            box = QVBoxLayout(page)
            box.setContentsMargins(0, 8, 0, 0)
            if plugin.cluster_settings:
                title = QLabel(f"<b>{plugin.name or plugin.id}</b> for this cluster only. Leave a box empty to use the "
                               "plugin's general settings.")
                title.setWordWrap(True)
                box.addWidget(title)
                rows = QFormLayout()
                box.addLayout(rows)
                self.fields[cloud] = setting_rows(rows, plugin.cluster_settings, manager.cluster_settings_for(plugin.id, context))
            else:
                note = QLabel(f"{plugin.name or plugin.id} has no other settings for one cluster.")
                note.setWordWrap(True)
                box.addWidget(note)
            pages.addWidget(page)
            self._page[cloud] = page
        self.none_page = QLabel("No enabled cloud plugin for this cluster. Choose its cloud above, or enable the cloud's "
                                "plugin in Preferences > Plugins.")
        self.none_page.setWordWrap(True)
        pages.addWidget(self.none_page)
        layout.addWidget(self.pages)
        layout.addStretch(1)
        self.choice.currentIndexChanged.connect(self._show_settings)
        self._show_settings()

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        resize_dialog(self, max(520, self.sizeHint().width()), self.sizeHint().height())

    def cloud(self):
        """The cloud whose settings are shown: the choice, else the detected one ("" when neither has a plugin)."""
        cloud = effective(self.choice.currentData(), self.detected)
        return cloud if cloud in self.plugins else ""

    def _show_settings(self):
        """Show the chosen cloud's page only (hidden pages take no room, so a cloud without settings leaves no gap)."""
        self.current_page = self._page.get(self.cloud(), self.none_page)
        for page in [*self._page.values(), self.none_page]:
            page.setVisible(page is self.current_page)
        if self.isVisible():
            self.layout().activate()
            self.resize(self.width(), self.sizeHint().height())

    def values(self):
        return {key: edit.text().strip() for key, edit in self.fields.get(self.cloud(), {}).items()}

    def save(self):
        cloud, values = self.cloud(), self.values()
        plugin = self.plugins.get(cloud)
        problem = invalid_value(plugin.cluster_settings, values) if plugin else ""
        if problem:
            QMessageBox.warning(self, "Cannot save", problem)
            return
        try:
            self.manager.set_cluster_cloud(self.context, self.choice.currentData())
            if plugin is not None and plugin.cluster_settings:
                self.manager.set_cluster_settings(plugin.id, self.context, values)
        except OSError as error:
            QMessageBox.warning(self, "Cannot save the cloud settings", str(error))
            return
        self.accept()
