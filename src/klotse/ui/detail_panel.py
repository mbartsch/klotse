import base64
import binascii
from copy import deepcopy

from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMenu, QPlainTextEdit, QPushButton, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
                               QWidgetAction)

from . import events_view, rbac_view, theme, workload_view
from .manifest import dump_manifest


class YamlHighlighter(QSyntaxHighlighter):
    """Light syntax colours for the read-only YAML view: keys, comments and plain scalars (numbers, true/false, null).
    Colours come from the active theme at the time the text is set, so a theme switch needs no restart."""

    KEY = QRegularExpression(r"^(\s*(?:-\s+)*)([^\s#:][^:#]*?)(:)(?=\s|$)")
    SCALAR = QRegularExpression(r"(?<=:\s|-\s)(-?\d+(?:\.\d+)?|true|false|null|~)\s*$")
    COMMENT = QRegularExpression(r"(?<![\"'\w])#.*$")

    def highlightBlock(self, text):
        key = QTextCharFormat()
        key.setForeground(QColor(theme.ACCENT_HI))
        scalar = QTextCharFormat()
        scalar.setForeground(QColor(theme.OK))
        comment = QTextCharFormat()
        comment.setForeground(QColor(theme.MUTED))
        m = self.KEY.match(text)
        if m.hasMatch():
            self.setFormat(m.capturedStart(2), m.capturedLength(2), key)
        m = self.SCALAR.match(text)
        if m.hasMatch():
            self.setFormat(m.capturedStart(1), m.capturedLength(1), scalar)
        m = self.COMMENT.match(text)
        if m.hasMatch():
            self.setFormat(m.capturedStart(), m.capturedLength(), comment)


class DetailPanel(QWidget):
    events_requested = Signal(object)  # the row whose events the Events view wants; answer with show_events()

    def __init__(self):
        super().__init__()
        self.editor = QPlainTextEdit(readOnly=True)
        self.highlighter = YamlHighlighter(self.editor.document())
        self.editor.setPlaceholderText("Select a resource to see its details.")
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
        self.events_button = QPushButton("Events", checkable=True)
        self.events_button.setToolTip("What the cluster says happened to this object (kept by Kubernetes for about an hour)")
        self.view_bar = QWidget()
        bar = QHBoxLayout(self.view_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(self.formatted_button)
        bar.addWidget(self.yaml_button)
        bar.addWidget(self.events_button)
        bar.addStretch(1)
        modes = QButtonGroup(self)
        modes.setExclusive(True)
        modes.addButton(self.formatted_button)
        modes.addButton(self.yaml_button)
        modes.addButton(self.events_button)
        self.formatted_button.setChecked(True)
        self.formatted_button.toggled.connect(self._show_mode)
        self.events_button.toggled.connect(self._show_mode)
        self.events = QTextBrowser()
        self.events.setOpenLinks(False)
        self.events.setOpenExternalLinks(False)
        self.events.hide()
        self._events_ok = False  # the current row can have events (it has a UID and is not an event itself)
        self._events_row = None  # the row the Events view is waiting for or showing
        self.view_bar.hide()
        # Tools for the YAML text: find (Enter = next, Shift+Enter = previous), copy and line wrap
        self.find_edit = QLineEdit(placeholderText="Find in YAML…", clearButtonEnabled=True)
        self.find_edit.setMaximumWidth(260)
        self.find_edit.returnPressed.connect(self._find_next)
        self.find_edit.textChanged.connect(lambda _t: self._find_from_start())
        self.copy_button = QPushButton("Copy YAML")
        self.copy_button.clicked.connect(self._copy_yaml)
        self.wrap_box = QCheckBox("Wrap lines")
        self.wrap_box.toggled.connect(lambda on: self.editor.setLineWrapMode(
            QPlainTextEdit.WidgetWidth if on else QPlainTextEdit.NoWrap))
        self.yaml_tools = QWidget()
        tools = QHBoxLayout(self.yaml_tools)
        tools.setContentsMargins(0, 0, 0, 0)
        tools.addWidget(self.find_edit)
        tools.addWidget(self.copy_button)
        tools.addWidget(self.wrap_box)
        tools.addStretch(1)
        self.yaml_tools.hide()
        self.formatted.hide()
        self._formatted_ok = False  # the current row has a formatted view
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view_bar)
        layout.addWidget(self.yaml_tools)
        layout.addWidget(self.decode_button)
        layout.addWidget(self.hide_values_button)
        layout.addWidget(self.formatted)
        layout.addWidget(self.events)
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
        self._events_ok = self._has_events(row)
        self._events_row = None
        self._render_formatted(row)

    @staticmethod
    def _has_events(row):
        raw = getattr(row, "raw", None)
        return (isinstance(raw, dict) and bool((raw.get("metadata") or {}).get("uid"))
                and "involvedObject" not in raw)  # an Event is not shown with events of its own

    def show_events(self, row, events=None, error=None):
        """Fill the Events view with the answer for `row` (ignored when another row is selected by now)."""
        if row is not self._events_row:
            return
        if error:
            self.events.setHtml(f"<p style='color:{theme.BAD}'>Cannot read the events: {events_view._e(error)}</p>")
        else:
            self.events.setHtml(events_view.render(events or [], getattr(row, "name", "")))

    def _render_formatted(self, row):
        kind, view = getattr(row, "rbac_kind", None), rbac_view
        if kind not in rbac_view.RBAC_KINDS:
            kind, view = workload_view.kind_of(row.raw), workload_view
        self._formatted_ok = False
        if kind is not None:
            try:
                self.formatted.setHtml(view.render(kind, row.raw))
                self._formatted_ok = True
            except Exception as e:  # a odd manifest must never break Details; the YAML is still there
                self.formatted.setPlainText(f"Cannot format this {kind}: {type(e).__name__}: {e}")
        self.view_bar.setVisible(self._formatted_ok or self._events_ok)
        self.formatted_button.setVisible(self._formatted_ok)
        self.events_button.setVisible(self._events_ok)
        self._show_mode()

    def _show_mode(self, *_):
        events = self._events_ok and self.events_button.isChecked()
        formatted = not events and self._formatted_ok and self.formatted_button.isChecked()
        self.formatted.setVisible(formatted)
        self.events.setVisible(events)
        self.editor.setVisible(not formatted and not events)
        self.yaml_tools.setVisible(not formatted and not events and self._secret_row is not None)
        if not events:
            self._events_row = None  # opening Events again asks again: events change while you watch
        elif self._events_row is not self._secret_row:
            self._events_row = self._secret_row
            self.events.setHtml(f"<p style='color:{theme.MUTED}'>Loading events…</p>")
            self.events_requested.emit(self._secret_row)

    def _find_from_start(self):
        cursor = self.editor.textCursor()
        cursor.setPosition(0)
        self.editor.setTextCursor(cursor)
        self._find_next()

    def _find_next(self):
        text = self.find_edit.text()
        if not text:
            return
        if not self.editor.find(text):  # wrap around once
            cursor = self.editor.textCursor()
            cursor.setPosition(0)
            self.editor.setTextCursor(cursor)
            self.editor.find(text)

    def _copy_yaml(self):
        QGuiApplication.clipboard().setText(self.editor.toPlainText())
        self.copy_button.setText("Copied")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(1200, lambda: self.copy_button.setText("Copy YAML"))

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
