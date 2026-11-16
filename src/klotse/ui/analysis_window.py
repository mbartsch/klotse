"""What the View-menu analysis windows share (#499): a title that names the cluster, one loading text, one error wording, a line
that says when nothing was found or rows are hidden, sortable table cells that remember their source row, and Copy."""
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTableWidgetItem

from .load_errors import _HINTS, describe, explain

LOADING = "Loading…"
NOTHING = "Nothing found."
SOURCE = Qt.UserRole + 40  # the index of a table row's entry in the window's own list, so sorting cannot mix them up


def title(window, cluster=""):
    """'Health check — prod': two windows for two clusters can be told apart in the taskbar."""
    return f"{window} — {cluster}" if cluster else window


def heading(cluster, scope=""):
    """The bold cluster name and the scope of the load being shown, escaped for a rich-text label."""
    return f"<b>{escape(str(cluster))}</b>" + (f" · {escape(str(scope))}" if scope else "")


def error_text(message, what="it", doing="load", hints=True):
    """One wording for a load (or a check, a search) that failed: a 401/403 is explained as a refusal (not an empty list), anything
    else is the plain description of `load_errors.describe` after "Could not load:" ("Could not check:", ...). With `hints` False
    (My permissions itself) the failure is described without the advice, which would point at View > My permissions…."""
    raw = str(message)
    if not hints:
        return f"Could not {doing}: " + "\n".join(line for line in describe(raw)[1].splitlines() if line not in _HINTS.values())
    explained = explain(raw, what)
    if explained != raw:
        return explained
    return f"Could not {doing}: {describe(raw)[1]}"


def found_text(total, hidden, nothing=NOTHING):
    """'Nothing found.' for an empty answer, 'N of M hidden by the filter or tick boxes.' when some rows are hidden, else ''."""
    if not total:
        return nothing
    if hidden:
        return f"{hidden} of {total} hidden by the filter or tick boxes."
    return ""


def hidden_count(table):
    return sum(1 for r in range(table.rowCount()) if table.isRowHidden(r))


class Cell(QTableWidgetItem):
    """A read-only cell that sorts by `key` (a number sorts as a number), not by its text."""

    def __init__(self, text, key=None):
        super().__init__(str(text))
        self._key = str(text).lower() if key is None else key
        self.setFlags(self.flags() & ~Qt.ItemIsEditable)
        if not isinstance(self._key, str):
            self.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def __lt__(self, other):
        mine, theirs = self._key, getattr(other, "_key", None)
        try:
            return mine < theirs
        except TypeError:
            return str(mine) < str(theirs)


def sortable(table):
    """Clicking a heading sorts by it (again: the other way). The rows keep the order they came in until a heading is clicked, and
    `fill` keeps the chosen sort for the next answer. (`setSortingEnabled` would sort by the first column at once.)"""
    if table.property("klotse_sortable"):
        return
    table.setProperty("klotse_sortable", True)
    header = table.horizontalHeader()
    header.setSectionsClickable(True)
    header.setSortIndicatorShown(False)

    def clicked(section):
        down = table.property("klotse_sort") == section and not table.property("klotse_down")
        table.setProperty("klotse_sort", section)
        table.setProperty("klotse_down", down)
        _sort(table)
    header.sectionClicked.connect(clicked)


def _sort(table):
    section = table.property("klotse_sort")
    if section is None:
        return
    order = Qt.DescendingOrder if table.property("klotse_down") else Qt.AscendingOrder
    header = table.horizontalHeader()
    header.setSortIndicatorShown(True)
    header.setSortIndicator(int(section), order)
    table.sortItems(int(section), order)


def fill(table, rows, source=True):
    """Fill `table` with `rows` (lists of text or (text, key) pairs); its headings sort (see `sortable`). With `source`, the first
    cell of each table row keeps the row's index in `rows`, read back with `source_index`."""
    sortable(table)
    table.clearContents()
    table.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, value in enumerate(row):
            text, key = value if isinstance(value, tuple) else (value, None)
            item = Cell(text, key)
            if source and j == 0:
                item.setData(SOURCE, i)
            table.setItem(i, j, item)
    _sort(table)
    table.resizeColumnsToContents()


def source_index(table, row):
    """The index in the window's list of the entry shown in table row `row` (the table may be sorted)."""
    item = table.item(row, 0)
    value = item.data(SOURCE) if item is not None else None
    return row if value is None else int(value)


def copy_table(table):
    """The visible rows of `table`, headings first, as tab-separated text on the clipboard. Returns the number of rows copied."""
    columns = range(table.columnCount())
    head = [table.horizontalHeaderItem(c).text() if table.horizontalHeaderItem(c) else "" for c in columns]
    lines = ["\t".join(head)]
    for r in range(table.rowCount()):
        if not table.isRowHidden(r):
            lines.append("\t".join(table.item(r, c).text() if table.item(r, c) else "" for c in columns))
    QApplication.clipboard().setText("\n".join(lines))
    return len(lines) - 1
