import base64
import binascii
from copy import deepcopy

from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMenu, QPlainTextEdit, QPushButton, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
                               QWidgetAction)

from ..k8s.related import KINDS as RELATED_KINDS
from . import events_view, karpenter_view, network_view, node_view, rbac_view, related_view, theme, workload_view
from .manifest import dump_manifest
from .action_icons import action_icon


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
    related_requested = Signal(object)  # the row whose owners and related objects the Related view wants; answer with show_related()
    goto_object = Signal(str, str, str)  # kind, namespace, name: a link in the Related view was clicked

    def __init__(self):
        super().__init__()
        self.setObjectName("detailPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.editor = QPlainTextEdit(readOnly=True)
        self.editor.setObjectName("detailYamlEditor")
        self.editor.setAccessibleName("Selected resource YAML")
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
        self.decode_filter.setAccessibleName("Filter secret keys to decode")
        self.decode_list = QListWidget()
        self.decode_list.setAccessibleName("Secret keys to decode")
        self.decode_list.setFixedHeight(280)
        self.decode_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.decode_count = QLabel()
        self.decode_selected_button = QPushButton("Decode selected")
        self.decode_selected_button.setAccessibleName("Decode selected secret keys")
        self.decode_selected_button.setEnabled(False)
        self.hide_values_button = QPushButton("Hide decoded values")
        self.hide_values_button.setAccessibleName("Hide decoded secret values")
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
        self.identity_header = QWidget()
        self.identity_header.setObjectName("detailIdentity")
        identity_layout = QVBoxLayout(self.identity_header)
        identity_layout.setContentsMargins(8, 6, 8, 6)
        identity_top = QHBoxLayout()
        self.identity_title = QLabel()
        self.identity_title.setWordWrap(True)
        self.identity_title.setStyleSheet("font-size: 16px; font-weight: 600")
        self.identity_title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.identity_status = QLabel()
        self.identity_status.setAccessibleName("Object status")
        self.copy_identity_button = QToolButton(text="Copy name")
        self.copy_identity_button.setAccessibleName("Copy object name")
        self.copy_identity_button.setIcon(action_icon("copy"))
        self.copy_identity_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.copy_identity_button.setToolTip("Copy the object's namespace/name (or name for a cluster-scoped object)")
        self.copy_identity_button.clicked.connect(self._copy_identity)
        identity_top.addWidget(self.identity_title, 1)
        identity_top.addWidget(self.copy_identity_button)
        identity_layout.addLayout(identity_top)
        identity_ids = QGridLayout()
        self.identity_scope = QLabel()
        self.identity_uid = QLabel()
        self.identity_uid.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.copy_uid_button = QToolButton(text="Copy UID")
        self.copy_uid_button.setAccessibleName("Copy object UID")
        self.copy_uid_button.setIcon(action_icon("copy"))
        self.copy_uid_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.copy_uid_button.clicked.connect(lambda: self._copy_text(self._identity_uid))
        self.identity_provider = QLabel()
        self.identity_provider.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.copy_provider_button = QToolButton(text="Copy provider ID")
        self.copy_provider_button.setAccessibleName("Copy node provider ID")
        self.copy_provider_button.setIcon(action_icon("copy"))
        self.copy_provider_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.copy_provider_button.clicked.connect(lambda: self._copy_text(self._identity_provider))
        identity_ids.addWidget(self.identity_scope, 0, 0, 1, 2)
        identity_ids.addWidget(self.identity_uid, 1, 0)
        identity_ids.addWidget(self.copy_uid_button, 1, 1)
        identity_ids.addWidget(self.identity_provider, 2, 0)
        identity_ids.addWidget(self.copy_provider_button, 2, 1)
        identity_ids.setColumnStretch(0, 1)
        for label in (self.identity_scope, self.identity_uid, self.identity_provider):
            label.setWordWrap(True)
            theme.muted(label)
        for button in (self.copy_uid_button, self.copy_provider_button):
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        identity_layout.addLayout(identity_ids)
        identity_layout.addWidget(self.identity_status)
        self.action_bar = QWidget()
        self.action_layout = QHBoxLayout(self.action_bar)
        self.action_layout.setContentsMargins(0, 0, 0, 0)
        self.action_layout.addStretch(1)
        identity_layout.addWidget(self.action_bar)
        self.action_bar.hide()
        self.identity_header.hide()
        self._identity_text = self._identity_uid = self._identity_provider = ""
        # Readable view for kinds that have one (RBAC); the YAML stays one click away. Text from the cluster is escaped.
        self.formatted = QTextBrowser()
        self.formatted.setObjectName("detailFormatted")
        self.formatted.setOpenLinks(False)
        self.formatted.setOpenExternalLinks(False)
        self.formatted.anchorClicked.connect(self._related_clicked)
        self.formatted_button = QPushButton("Summary", checkable=True)
        self.formatted_button.setAccessibleName("Show resource summary")
        self.formatted_button.setToolTip("A readable summary of the selected resource")
        self.yaml_button = QPushButton("YAML", checkable=True)
        self.yaml_button.setAccessibleName("Show resource YAML")
        self.events_button = QPushButton("Events", checkable=True)
        self.events_button.setAccessibleName("Show resource events")
        self.events_button.setToolTip("What the cluster says happened to this object (kept by Kubernetes for about an hour)")
        self.related_button = QPushButton("Related", checkable=True)
        self.related_button.setAccessibleName("Show related resources")
        self.related_button.setToolTip("Owners, what it owns, and the objects it uses or is used by; click one to go there")
        self.view_bar = QWidget()
        self.view_bar.setObjectName("detailViewBar")
        bar = QHBoxLayout(self.view_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.addWidget(self.formatted_button)
        bar.addWidget(self.yaml_button)
        bar.addWidget(self.events_button)
        bar.addWidget(self.related_button)
        for button in (self.formatted_button, self.yaml_button, self.events_button, self.related_button):
            button.setObjectName("detailViewButton")
            button.clicked.connect(lambda _checked=False: setattr(self, "_summary_fallback", False))
        bar.addStretch(1)
        modes = QButtonGroup(self)
        modes.setExclusive(True)
        modes.addButton(self.formatted_button)
        modes.addButton(self.yaml_button)
        modes.addButton(self.events_button)
        modes.addButton(self.related_button)
        self.formatted_button.setChecked(True)
        self.formatted_button.toggled.connect(self._show_mode)
        self.events_button.toggled.connect(self._show_mode)
        self.related_button.toggled.connect(self._show_mode)
        self.related = QTextBrowser()
        self.related.setObjectName("detailRelated")
        self.related.setAccessibleName("Resources related to the selection")
        self.related.setOpenLinks(False)
        self.related.setOpenExternalLinks(False)
        self.related.anchorClicked.connect(self._related_clicked)
        self.related.hide()
        self._related_ok = False  # the current row is of a kind that has a Related view
        self._related_row = None  # the row the Related view is waiting for or showing
        self.events = QTextBrowser()
        self.events.setObjectName("detailEvents")
        self.events.setAccessibleName("Events for the selected resource")
        self.events.setOpenLinks(False)
        self.events.setOpenExternalLinks(False)
        self.events.hide()
        self._events_ok = False  # the current row can have events (it has a UID and is not an event itself)
        self._events_row = None  # the row the Events view is waiting for or showing
        self.view_bar.hide()
        self.metadata_area = QWidget()
        metadata_layout = QVBoxLayout(self.metadata_area)
        metadata_layout.setContentsMargins(0, 0, 0, 0)
        metadata_layout.setSpacing(2)
        self.metadata_controls = {}
        for name in ("Labels", "Annotations"):
            button = QToolButton(text=name, checkable=True)
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.setArrowType(Qt.RightArrow)
            value = QPlainTextEdit(readOnly=True)
            value.setAccessibleName(f"{name} values")
            value.setMaximumHeight(120)
            value.setLineWrapMode(QPlainTextEdit.WidgetWidth)
            value.hide()
            button.toggled.connect(lambda checked, b=button, v=value: (b.setArrowType(Qt.DownArrow if checked else Qt.RightArrow), v.setVisible(checked)))
            metadata_layout.addWidget(button)
            metadata_layout.addWidget(value)
            self.metadata_controls[name] = (button, value)
        self.metadata_area.hide()
        # Tools for the YAML text: find (Enter = next, Shift+Enter = previous), copy and line wrap
        self.find_edit = QLineEdit(placeholderText="Find in YAML…", clearButtonEnabled=True)
        self.find_edit.setAccessibleName("Find text in resource YAML")
        self.find_edit.setMaximumWidth(260)
        self.find_edit.returnPressed.connect(self._find_next)
        self.find_edit.textChanged.connect(lambda _t: self._find_from_start())
        self.copy_button = QPushButton("Copy YAML")
        self.copy_button.setAccessibleName("Copy resource YAML")
        self.copy_button.setIcon(action_icon("copy"))
        self.copy_button.clicked.connect(self._copy_yaml)
        self.wrap_box = QCheckBox("Wrap lines")
        self.wrap_box.setAccessibleName("Wrap resource YAML lines")
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
        self._summary_fallback = False  # distinguish automatic YAML from a user's choice
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)
        layout.addWidget(self.identity_header)
        layout.addWidget(self.view_bar)
        layout.addWidget(self.metadata_area)
        layout.addWidget(self.yaml_tools)
        layout.addWidget(self.decode_button)
        layout.addWidget(self.hide_values_button)
        layout.addWidget(self.formatted)
        layout.addWidget(self.events)
        layout.addWidget(self.related)
        layout.addWidget(self.editor)
        # Scroll-area viewports otherwise paint an opaque palette Base over the
        # inspector surface, even when the surrounding widget is transparent.
        for content in (self.editor, self.formatted, self.events, self.related):
            content.viewport().setAutoFillBackground(False)

    def toPlainText(self):
        return self.editor.toPlainText()

    def show_row(self, row):
        if row is None:
            self.clear_row()
            return
        self._secret_row = row
        self._secret_data = getattr(row, "secret_data", {})
        self._set_identity(row)
        meta = (row.raw.get("metadata") or {}) if isinstance(row.raw, dict) else {}
        for name, (button, value) in self.metadata_controls.items():
            pairs = meta.get(name.lower()) or {}
            if not isinstance(pairs, dict):
                pairs = {}
            button.setChecked(False)
            button.setText(f"{name} ({len(pairs)})")
            button.setAccessibleName(f"{name}, {len(pairs)} entries")
            button.setVisible(bool(pairs))
            value.setPlainText("\n".join(f"{k} = {v}" for k, v in sorted(pairs.items())))
            value.hide()
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
        self._related_ok = getattr(row, "table_kind", None) in RELATED_KINDS and isinstance(getattr(row, "raw", None), dict)
        self._related_row = None
        self._render_formatted(row)

    def clear_row(self):
        """Return Details to its empty state and discard object-specific actions and secret values."""
        self._secret_row = None
        self._secret_data = {}
        self._decoded_keys.clear()
        self.decode_list.clear()
        self.decode_filter.clear()
        self._selection_changed()
        self.decode_menu.hide()
        self.decode_button.hide()
        self.hide_values_button.hide()
        self._events_ok = self._related_ok = self._formatted_ok = False
        self._summary_fallback = False
        self._events_row = self._related_row = None
        self.events.clear()
        self.related.clear()
        self.editor.clear()
        self.set_object_actions([])
        self.identity_header.hide()
        self.view_bar.hide()
        self.metadata_area.hide()
        self.yaml_tools.hide()
        self.formatted.hide()
        self.events.hide()
        self.related.hide()
        self.editor.show()
        self.formatted_button.setChecked(True)

    def show_related(self, row, relations=None, error=None):
        """Fill the Related view with the answer for `row` (ignored when another row is selected by now)."""
        if row is not self._related_row:
            return
        if error:
            self.related.setHtml(f"<p style='color:{theme.BAD}'>Cannot read the related objects: {events_view._e(error)}</p>")
        else:
            self.related.setHtml(related_view.render(relations or [], getattr(row, "name", "")))

    def _related_clicked(self, url):
        target = related_view.parse_link(url.toString())
        if target is not None:
            self.goto_object.emit(*target)

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
        self._formatted_ok = False
        if getattr(row, "table_kind", None) == "Node" or hasattr(row, "is_ready"):
            try:
                self.formatted.setHtml(node_view.render(row.raw, include_header=False, include_metadata=False))
                self._formatted_ok = True
            except Exception as e:  # noqa: BLE001
                self.formatted.setPlainText(f"Cannot format this Node: {type(e).__name__}: {e}")
                self.formatted.append("No formatted summary is available for this resource kind.")
            self._sync_view_choices()
            self._show_mode()
            return
        kind, view = getattr(row, "rbac_kind", None), rbac_view
        if kind not in rbac_view.RBAC_KINDS:
            kind = karpenter_view.kind_of(row.raw, getattr(row, "custom_kind", None),
                                          getattr(row, "custom_group", None))
            view = karpenter_view
            if kind is None:
                kind = network_view.kind_of(row.raw, getattr(row, "table_kind", None))
                view = network_view
                if kind is None:
                    kind, view = workload_view.kind_of(row.raw), workload_view
        if kind is not None:
            try:
                self.formatted.setHtml(view.render(kind, row.raw, include_header=False, **({"include_metadata": False} if view in (rbac_view, workload_view) else {})))
                self._formatted_ok = True
            except Exception as e:  # a odd manifest must never break Details; the YAML is still there
                self.formatted.setPlainText(f"Cannot format this {kind}: {type(e).__name__}: {e}")
        if not self._formatted_ok:
            self.formatted.setHtml(f"<p style='color:{theme.MUTED}'>No formatted summary is available for this resource kind. Use YAML to inspect its fields.</p>")
        self._sync_view_choices()
        self._show_mode()

    def set_object_actions(self, actions):
        """Keep short action lists visible and always expose Logs beside longer lists."""
        while self.action_layout.count():
            item = self.action_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        actions = list(actions or [])
        primary = actions if len(actions) <= 3 else [action for action in actions if action.primary][:2]
        logs = next((action for action in actions if action.key in ("logs", "container-logs", "multi-logs")), None)
        if len(actions) > 3 and logs is not None and logs not in primary:
            primary = primary[:1] + [logs]
        overflow = [action for action in actions if action not in primary]
        for action in primary:
            button = QToolButton(text=action.title)
            button.setToolTip(action.title)
            button.setAccessibleName(action.title.replace("…", ""))
            button.clicked.connect(lambda _checked=False, callback=action.callback: callback())
            self.action_layout.addWidget(button)
        if overflow:
            menu = QMenu(self.action_bar)
            dangerous_started = False
            for action in overflow:
                if action.dangerous and not dangerous_started and not menu.isEmpty():
                    menu.addSeparator()
                    dangerous_started = True
                item = menu.addAction(action.title)
                item.setToolTip(action.title)
                item.triggered.connect(action.callback)
            button = QToolButton(text="More…")
            button.setAccessibleName("More object actions")
            button.setPopupMode(QToolButton.InstantPopup)
            button.setMenu(menu)
            self.action_layout.addWidget(button)
        self.action_layout.addStretch(1)
        self.action_bar.setVisible(bool(actions))

    def _sync_view_choices(self):
        """Keep the four detail destinations in one stable place and disable only unavailable views."""
        self.view_bar.setVisible(self._secret_row is not None)
        self.formatted_button.setVisible(True)
        self.formatted_button.setEnabled(self._formatted_ok)
        self.formatted_button.setToolTip("A readable summary of the selected resource" if self._formatted_ok else
                                        "No formatted summary is available; inspect this resource in YAML")
        if self._formatted_ok and self._summary_fallback and self.yaml_button.isChecked():
            self._summary_fallback = False
            self.formatted_button.setChecked(True)
        self.yaml_button.setVisible(True)
        self.yaml_button.setEnabled(True)
        self.events_button.setVisible(True)
        self.events_button.setEnabled(self._events_ok)
        self.events_button.setToolTip("Events for this object" if self._events_ok else "Events are unavailable for this object")
        self.related_button.setVisible(True)
        self.related_button.setEnabled(self._related_ok)
        self.related_button.setToolTip("Related objects" if self._related_ok else "Related objects are unavailable for this kind")

    def _set_identity(self, row):
        raw = getattr(row, "raw", None)
        raw = raw if isinstance(raw, dict) else {}
        metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
        kind = (getattr(row, "table_kind", None) or raw.get("kind") or getattr(row, "rbac_kind", None)
                or ("Node" if hasattr(row, "is_ready") else "Resource"))
        name = metadata.get("name") or getattr(row, "name", None) or "(unnamed)"
        namespace = metadata.get("namespace") or getattr(row, "namespace", "") or ""
        self._identity_text = f"{namespace}/{name}" if namespace else str(name)
        self._identity_uid = str(metadata.get("uid") or "")
        provider = (raw.get("spec") or {}).get("providerID") if isinstance(raw.get("spec"), dict) else ""
        self._identity_provider = str(provider or "")
        status = self._identity_status_text(row, kind, raw)
        self.identity_title.setText(f"{kind} · {name}")
        self.identity_scope.setText(f"Namespace: {namespace}" if namespace else "Cluster-scoped")
        self.identity_uid.setText(f"UID: {self._identity_uid}" if self._identity_uid else "")
        self.identity_provider.setText(f"Provider ID: {self._identity_provider}" if self._identity_provider else "")
        self.copy_uid_button.setVisible(bool(self._identity_uid))
        self.identity_uid.setVisible(bool(self._identity_uid))
        self.copy_provider_button.setVisible(bool(self._identity_provider))
        self.identity_provider.setVisible(bool(self._identity_provider))
        self.identity_status.setText(status)
        folded = status.casefold()
        color = theme.BAD if any(word in folded for word in ("failed", "not ready", "error", "crash")) else (
            theme.WARN if any(word in folded for word in ("pending", "unknown", "terminating", "suspend", "warning")) else theme.OK)
        self.identity_status.setStyleSheet(f"color: {color}; font-weight: 600")
        self.identity_header.show()

    @staticmethod
    def _identity_status_text(row, kind, raw):
        if kind == "Event":
            return str(raw.get("type") or getattr(row, "type", None) or "Unknown")
        if kind in workload_view.WORKLOAD_KINDS:
            return workload_view.status_line(kind, raw)[0]
        if kind == "Node":
            ready = next((item.get("status") for item in (raw.get("status") or {}).get("conditions") or []
                          if item.get("type") == "Ready"), "Unknown")
            return "Ready" if ready == "True" else "Not Ready" if ready == "False" else "Unknown"
        raw_status = raw.get("status") if isinstance(raw.get("status"), dict) else {}
        value = getattr(row, "status", None) or raw_status.get("phase")
        if value:
            return str(value)
        conditions = raw_status.get("conditions") or []
        for condition in conditions:
            if condition.get("type") in ("Ready", "Available", "Complete", "Failed"):
                return f"{condition.get('type')}: {condition.get('status', 'Unknown')}"
        return "Unknown"

    def _copy_identity(self):
        self._copy_text(self._identity_text)

    @staticmethod
    def _copy_text(value):
        if value:
            QGuiApplication.clipboard().setText(value)

    def _show_mode(self, *_):
        if self._secret_row is not None and self.formatted_button.isChecked() and not self._formatted_ok:
            self._summary_fallback = True
            self.yaml_button.setChecked(True)
            return
        if self.related_button.isChecked() and not self._related_ok:
            (self.formatted_button if self._formatted_ok else self.yaml_button).setChecked(True)
            return
        if self.events_button.isChecked() and not self._events_ok:
            (self.formatted_button if self._formatted_ok else self.yaml_button).setChecked(True)
            return
        related = self._related_ok and self.related_button.isChecked()
        events = not related and self._events_ok and self.events_button.isChecked()
        formatted = not events and not related and self.formatted_button.isChecked()
        self.formatted.setVisible(formatted)
        self.metadata_area.setVisible(formatted and self._formatted_ok and any(not b.isHidden() for b, _ in self.metadata_controls.values()))
        self.events.setVisible(events)
        self.related.setVisible(related)
        self.editor.setVisible(not formatted and not events and not related)
        self.yaml_tools.setVisible(not formatted and not events and not related and self._secret_row is not None)
        if not related:
            self._related_row = None  # opening Related again asks again
        elif self._related_row is not self._secret_row:
            self._related_row = self._secret_row
            self.related.setHtml(f"<p style='color:{theme.MUTED}'>Looking for related objects…</p>")
            self.related_requested.emit(self._secret_row)
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
        QTimer.singleShot(1200, self, lambda: self.copy_button.setText("Copy YAML"))  # tied to this panel: gone with it, never late

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
