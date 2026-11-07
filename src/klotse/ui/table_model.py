import re

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor

from . import theme
from .status_tone import TONE_ATTRS, status_tone


ANNOTATION_CELL_LIMIT = 200  # annotation values can be kilobytes (last-applied-configuration): the cell shows the start
ANNOTATION_TIP_LIMIT = 2000
_AGE = re.compile(r"(\d+)(min|[dhms])")
_AGE_UNITS = {"d": 86400, "h": 3600, "m": 60, "min": 60, "s": 1}


def age_sort_value(value):
    match = _AGE.fullmatch(str(value))
    return int(match[1]) * _AGE_UNITS[match[2]] if match else -1


class TableModel(QAbstractTableModel):
    """Rows are dataclasses; columns is a list of (header, attribute) or (header, attribute, formatter) tuples.

    The formatter only affects display; sorting uses the raw attribute so numbers sort numerically.
    """

    SearchRole = Qt.UserRole + 1

    def __init__(self, columns, search_metadata=False):
        super().__init__()
        self.columns, self.rows = columns, []
        self.search_metadata = search_metadata
        self._metadata_text = []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = list(rows)
        self._metadata_text = []
        if self.search_metadata:
            for row in self.rows:
                metadata = (row.raw or {}).get("metadata") or {}
                self._metadata_text.append("\n".join(
                    f"{key}={value}" for field in ("labels", "annotations")
                    for key, value in (metadata.get(field) or {}).items()))
        self.endResetModel()

    def append_rows(self, rows):
        """Add rows at the end without resetting the model (views keep their selection and scroll position)."""
        rows = list(rows)
        if not rows:
            return
        first = len(self.rows)
        self.beginInsertRows(QModelIndex(), first, first + len(rows) - 1)
        self.rows.extend(rows)
        if self.search_metadata:
            for row in rows:
                metadata = (row.raw or {}).get("metadata") or {}
                self._metadata_text.append("\n".join(
                    f"{key}={value}" for field in ("labels", "annotations")
                    for key, value in (metadata.get(field) or {}).items()))
        self.endInsertRows()

    def refresh_plugin_columns(self):
        """Repaint every plugin column after the plugin values on the rows changed."""
        for i, col in enumerate(self.columns):
            if col[1].startswith("plugin:") and self.rows:
                self.dataChanged.emit(self.index(0, i), self.index(len(self.rows) - 1, i))

    def refresh_column(self, attr):
        """Repaint one column after its values were changed in place."""
        col = next((i for i, c in enumerate(self.columns) if c[1] == attr), -1)
        if col >= 0 and self.rows:
            self.dataChanged.emit(self.index(0, col), self.index(len(self.rows) - 1, col))

    def set_columns(self, columns):
        self.beginResetModel()
        self.columns = list(columns)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def _tone_brush(self, index):
        """Healthy / in progress / failing status words are drawn in the theme's ok / warn / bad colour."""
        col = self.columns[index.column()]
        if col[1] not in TONE_ATTRS:
            return None
        tone = status_tone(getattr(self.rows[index.row()], col[1], ""))
        color = {"ok": theme.OK, "warn": theme.WARN, "bad": theme.BAD}.get(tone)
        return QBrush(QColor(color)) if color else None

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.ForegroundRole:
            return self._tone_brush(index) if index.isValid() else None
        if not index.isValid() or role not in (Qt.DisplayRole, Qt.EditRole, Qt.ToolTipRole, self.SearchRole):
            return None
        col = self.columns[index.column()]
        if col[1].startswith("plugin:"):  # filled in by a plugin: absent until it has answered, never an error
            value = (getattr(self.rows[index.row()], "extra", None) or {}).get(col[1])
            if role == Qt.EditRole:
                return value  # None sorts before real values
            if value is None:
                return ""
            if len(col) > 2 and role != Qt.ToolTipRole:
                try:
                    return col[2](value)
                except Exception:  # a plugin's formatter must not break the table
                    return str(value)
            return str(value)
        is_note = col[1].startswith("annotation:")
        if is_note or col[1].startswith("label:"):
            metadata = (self.rows[index.row()].raw or {}).get("metadata") or {}
            field, _, key = col[1].partition(":")
            value = (metadata.get("annotations" if is_note else "labels") or {}).get(key, "")
        else:
            value = getattr(self.rows[index.row()], col[1])
        if is_note and role != Qt.EditRole:
            text = str(value)
            if role == Qt.ToolTipRole:
                return text[:ANNOTATION_TIP_LIMIT] + ("…" if len(text) > ANNOTATION_TIP_LIMIT else "")
            text = " ".join(text.split())  # one line: annotations are often multi-line JSON or YAML
            return text[:ANNOTATION_CELL_LIMIT - 1] + "…" if len(text) > ANNOTATION_CELL_LIMIT else text
        if role == Qt.ToolTipRole:
            if self.search_metadata and col[1] == "status":
                conditions = (self.rows[index.row()].raw.get("status") or {}).get("conditions") or []
                return "\n".join([str(value)] + [
                    f"{c.get('type', '')}: {c.get('status', 'Unknown')} "
                    f"{c.get('reason') or ''} {c.get('message') or ''}".rstrip() for c in conditions])
            return str(value)
        if role in (Qt.DisplayRole, self.SearchRole):
            text = col[2](value) if len(col) > 2 else str(value)
            if role == self.SearchRole and self.search_metadata and index.column() == 0:
                return text + "\n" + self._metadata_text[index.row()]
            return text
        if role == Qt.EditRole:  # sort role: keeps ints numeric
            if col[1] == "age":
                return age_sort_value(value)
            return value
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.columns[section][0]
        if role == Qt.ToolTipRole and orientation == Qt.Horizontal:
            attr = self.columns[section][1]
            if attr.startswith("label:"):
                return attr[6:]
            return attr[11:] if attr.startswith("annotation:") else self.columns[section][0]
        return None
