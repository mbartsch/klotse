import base64
import binascii
from copy import deepcopy

from PySide6.QtGui import QFontDatabase
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMenu, QPlainTextEdit, QPushButton, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
                               QWidgetAction)

from . import rbac_view
from .manifest import dump_manifest


class DetailPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.editor = QPlainTextEdit(readOnly=True)
        self.editor.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.decode_button = QToolButton(text="Decode values…")
        self.decode_button.setToolTip("Choose secret keys to decode in Details")
        self.decode_button.setPopupMode(QToolButton.InstantPopup)
        self.decode_menu = QMenu(self.decode_button)
        picker = QWidget()
        picker.setFixedWidth(360)
        picker_layout = QVBoxLayout(picker)
        self.decode_filter = QLineEdit(placeholderText="Filter secret keys…", clearButtonEnabled=True)
        self.decode_list = QListWidget()
        self.decode_list.setFixedHeight(280)
        self.decode_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.decode_count = QLabel()
        self.decode_selected_button = QPushButton("Decode selected")
        self.decode_selected_button.setEnabled(False)
        self.hide_values_button = QPushButton("Hide decoded values")
        self.hide_values_button.hide()
        picker_layout.addWidget(self.decode_filter)
        picker_layout.addWidget(self.decode_list)
        picker_layout.addWidget(self.decode_count)
        picker_layout.addWidget(self.decode_selected_button)
        picker_action = QWidgetAction(self.decode_menu)
        picker_action.setDefaultWidget(picker)
        self.decode_menu.addAction(picker_action)
        self.decode_filter.textChanged.connect(self._filter_keys)
        self.decode_filter.returnPressed.connect(self._decode_selected)
        self.decode_selected_button.clicked.connect(self._decode_selected)
        self.decode_list.itemChanged.connect(self._selection_changed)
        self.hide_values_button.clicked.connect(self._hide_values)
        self.decode_menu.aboutToShow.connect(self._open_picker)
        self.decode_button.setMenu(self.decode_menu)
        self.decode_button.hide()
        self._secret_row = None
        self._secret_data = {}
        self._decoded_keys = set()
        self.show_managed_fields = False  # metadata.managedFields is long and rarely useful; opt in from View
        # Readable view for kinds that have one (RBAC); the YAML stays one click away. Text from the cluster is escaped.
        self.formatted = QTextBrowser()
        self.formatted.setOpenLinks(False)
        self.formatted.setOpenExternalLinks(False)
        self.formatted_button = QPushButton("Formatted", checkable=True)
        self.yaml_button = QPushButton("YAML", checkable=True)
        self.view_bar = QWidget()
        bar = QHBoxLayout(self.view_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(self.formatted_button)
        bar.addWidget(self.yaml_button)
        bar.addStretch(1)
        modes = QButtonGroup(self)
        modes.setExclusive(True)
        modes.addButton(self.formatted_button)
        modes.addButton(self.yaml_button)
        self.formatted_button.setChecked(True)
        self.formatted_button.toggled.connect(self._show_mode)
        self.view_bar.hide()
        self.formatted.hide()
        self._formatted_ok = False  # the current row has a formatted view
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view_bar)
        layout.addWidget(self.decode_button)
        layout.addWidget(self.hide_values_button)
        layout.addWidget(self.formatted)
        layout.addWidget(self.editor)

    def toPlainText(self):
        return self.editor.toPlainText()

    def show_row(self, row):
        self._secret_row = row
        self._secret_data = getattr(row, "secret_data", {})
        self._decoded_keys.clear()
        self.hide_values_button.hide()
        self.decode_list.clear()
        for key in sorted(self._secret_data):
            item = QListWidgetItem(key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.decode_list.addItem(item)
        self._selection_changed()
        self.decode_filter.clear()
        self._filter_keys("")
        self.decode_menu.hide()
        self.decode_button.setVisible(bool(self._secret_data))
        self.editor.setPlainText(dump_manifest(self._displayed(row.raw)))
        self._render_formatted(row)

    def _render_formatted(self, row):
        kind = getattr(row, "rbac_kind", None)
        self._formatted_ok = False
        if kind in rbac_view.RBAC_KINDS:
            try:
                self.formatted.setHtml(rbac_view.render(kind, row.raw))
                self._formatted_ok = True
            except Exception as e:  # a odd manifest must never break Details; the YAML is still there
                self.formatted.setPlainText(f"Cannot format this {kind}: {type(e).__name__}: {e}")
        self.view_bar.setVisible(self._formatted_ok)
        self._show_mode()

    def _show_mode(self, *_):
        formatted = self._formatted_ok and self.formatted_button.isChecked()
        self.formatted.setVisible(formatted)
        self.editor.setVisible(not formatted)

    def set_show_managed_fields(self, show):
        """Include or hide metadata.managedFields in Details; the shown resource is redrawn, scroll kept."""
        show = bool(show)
        if show == self.show_managed_fields:
            return
        self.show_managed_fields = show
        if self._secret_row is None:
            return
        scroll = self.editor.verticalScrollBar().value()
        if self._decoded_keys:
            self._render_secret()
        else:
            self.editor.setPlainText(dump_manifest(self._displayed(self._secret_row.raw)))
        self.editor.verticalScrollBar().setValue(scroll)

    def _displayed(self, body):
        """The manifest as shown: without managedFields unless enabled. Never modifies the row's own raw."""
        metadata = body.get("metadata") if isinstance(body, dict) else None
        if self.show_managed_fields or not isinstance(metadata, dict) or "managedFields" not in metadata:
            return body
        return {**body, "metadata": {k: v for k, v in metadata.items() if k != "managedFields"}}

    def _open_picker(self):
        self.decode_filter.clear()
        self._filter_keys("")
        self.decode_filter.setFocus()

    def _filter_keys(self, query):
        query = query.casefold()
        matches = []
        for i in range(self.decode_list.count()):
            item = self.decode_list.item(i)
            item.setHidden(query not in item.text().casefold())
            if not item.isHidden():
                matches.append(item)
        self.decode_list.setCurrentItem(matches[0] if matches else None)
        self.decode_count.setText(f"{len(matches)} of {self.decode_list.count()} keys" if matches else "No matching keys")

    def _checked_keys(self):
        return {self.decode_list.item(i).text() for i in range(self.decode_list.count())
                if self.decode_list.item(i).checkState() == Qt.Checked}

    def _selection_changed(self, *_):
        count = len(self._checked_keys())
        self.decode_selected_button.setText(f"Decode selected ({count})")
        self.decode_selected_button.setEnabled(bool(count))

    def _decode_selected(self):
        keys = self._checked_keys()
        if not keys:
            return
        self._decoded_keys = keys
        self.decode_menu.hide()
        self._render_secret()

    def _hide_values(self):
        self._decoded_keys.clear()
        for i in range(self.decode_list.count()):
            self.decode_list.item(i).setCheckState(Qt.Unchecked)
        self._render_secret()

    def _render_secret(self):
        body = deepcopy(self._secret_row.raw)
        for key in self._decoded_keys:
            body["data"][key] = self._decode_value(self._secret_data[key])
        self.editor.setPlainText(dump_manifest(self._displayed(body)))
        self.hide_values_button.setVisible(bool(self._decoded_keys))

    @staticmethod
    def _decode_value(encoded):
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError, binascii.Error):
            text = "Unable to decode this value: invalid base64."
        else:
            try:
                text = decoded.decode("utf-8")
            except UnicodeDecodeError:
                text = "Binary value (hex):\n" + decoded.hex(" ")
        return text
