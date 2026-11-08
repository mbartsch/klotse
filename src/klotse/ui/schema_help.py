"""Schema help for a YAML editor (see k8s/schema.py): a docs panel that follows the cursor, Ctrl+Space completion of field names and enum values,
and a status line naming fields the cluster's schema does not know. All of it comes from the cluster's own OpenAPI document, which the owner
of the editor loads and hands over with `set_schema`; until then the panel says so, and the editor works as it always did."""
import html
import re

import yaml
from PySide6.QtCore import QEvent, QObject, QStringListModel, Qt, QTimer
from PySide6.QtWidgets import QCompleter, QLabel, QPlainTextEdit, QTextBrowser

from ..k8s import schema as sc

KEY_BEFORE = re.compile(r"^(?P<lead>\s*(?:- +)*)(?P<partial>[A-Za-z0-9_.\-/]*)$")
VALUE_BEFORE = re.compile(r"^\s*(?:- +)*(?P<key>[A-Za-z0-9_.\-/]+):\s+(?P<partial>[A-Za-z0-9_.\-]*)$")
MAX_CHILDREN = 14


class SchemaHelper(QObject):
    def __init__(self, editor: QPlainTextEdit, browser: QTextBrowser, status: QLabel, parent=None):
        super().__init__(parent or editor)
        self.editor, self.browser, self.status = editor, browser, status
        self.schema, self.root, self.kind, self.state = None, {}, "", "Schema help: waiting for the cluster's OpenAPI…"
        self.completer = QCompleter(self)
        self.completer.setWidget(editor)
        self.completer.setCompletionMode(QCompleter.PopupCompletion)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.model = QStringListModel(self)
        self.completer.setModel(self.model)
        self.completer.activated[str].connect(self._insert)
        self.inserts = {}  # popup text -> text to insert
        self._partial = ""
        self.docs_timer = QTimer(self, singleShot=True, interval=120)
        self.docs_timer.timeout.connect(self.refresh_docs)
        self.check_timer = QTimer(self, singleShot=True, interval=600)
        self.check_timer.timeout.connect(self.check_fields)
        editor.cursorPositionChanged.connect(self.docs_timer.start)
        editor.textChanged.connect(self.check_timer.start)
        editor.installEventFilter(self)
        self.browser.setHtml(f"<i>{html.escape(self.state)}</i>")

    # ---- the schema ---------------------------------------------------------------------------------------------
    def set_loading(self):
        self.state = "Schema help: loading the cluster's OpenAPI…"
        self.browser.setHtml(f"<i>{html.escape(self.state)}</i>")

    def set_unavailable(self, reason):
        self.schema, self.root = None, {}
        self.state = f"Schema help is not available: {reason}"
        self.browser.setHtml(f"<i>{html.escape(self.state)}</i>")
        self.status.setText("")

    def set_schema(self, document, kind, api_version=""):
        """The cluster's OpenAPI v3 document for the kind's group and version."""
        self.schema = sc.Schema(document)
        self.kind = kind
        self.root = self.schema.root_for(kind, api_version or self.api_version())
        if not self.root:
            self.set_unavailable(f"this cluster's OpenAPI has no schema for {kind}")
            return
        self.state = f"Schema help from this cluster's OpenAPI for {kind}. Ctrl+Space completes."
        self.refresh_docs()
        self.check_fields()

    def api_version(self):
        match = re.search(r"^apiVersion:\s*(\S+)", self.editor.toPlainText(), re.M)
        return match.group(1).strip("\"'") if match else ""

    # ---- docs under the cursor -------------------------------------------------------------------------------------
    def lines(self):
        return self.editor.toPlainText().split("\n")

    def cursor_line(self):
        return self.editor.textCursor().blockNumber()

    def docs_html(self):
        """The documentation of the field the cursor is on (or, on a blank or half-typed line, of the object it would go into)."""
        if self.schema is None or not self.root:
            return f"<i>{html.escape(self.state)}</i>"
        lines, number = self.lines(), self.cursor_line()
        text = lines[number] if number < len(lines) else ""
        path, indent, _item = sc.yaml_path(lines, number)
        key = sc._split(text)[2] if text.strip() else None
        if key is None:
            path = sc.parent_path(lines, number, len(text) - len(text.lstrip(" -")) if text.strip() else len(text))
        info = self.schema.describe(self.root, path)
        if info is None:
            where = ".".join(path) if path else self.kind
            return f"<b>{html.escape(where)}</b><br><i>Not in this cluster's schema for {html.escape(self.kind)}.</i>"
        title = html.escape(".".join(path).replace(".*", "[]") or self.kind)
        facts = [info["type"] or "any"] + ([info["format"]] if info["format"] else []) + (["required"] if info["required"] else [])
        out = [f"<b>{title}</b> <span>· {html.escape(', '.join(facts))}</span>"]
        if info["description"]:
            out.append("<p>" + html.escape(info["description"]).replace("\n\n", "</p><p>").replace("\n", " ") + "</p>")
        if info["enum"]:
            out.append("<p>Values: " + ", ".join(f"<code>{html.escape(str(v))}</code>" for v in info["enum"]) + "</p>")
        if info["default"] not in (None, {}, []):
            out.append(f"<p>Default: <code>{html.escape(str(info['default']))}</code></p>")
        if info["children"]:
            shown = info["children"][:MAX_CHILDREN]
            out.append("<p>Fields: " + ", ".join(f"<code>{html.escape(n)}</code>{'*' if req else ''}" for n, _t, req in shown)
                       + (f" and {len(info['children']) - MAX_CHILDREN} more" if len(info["children"]) > MAX_CHILDREN else "") + " <small>(* required)</small></p>")
        return "".join(out)

    def refresh_docs(self):
        self.browser.setHtml(self.docs_html())

    def check_fields(self):
        """Name the fields in the text that the schema does not know (only when the text parses as YAML)."""
        if self.schema is None or not self.root:
            self.status.setText("")
            return
        try:
            data = yaml.safe_load(self.editor.toPlainText())
        except yaml.YAMLError:
            self.status.setText("")
            return
        unknown = self.schema.unknown_fields(data, self.root) if isinstance(data, dict) else []
        self.status.setText(("Not in this cluster's schema: " + ", ".join(unknown[:6]) + (" …" if len(unknown) > 6 else "")) if unknown else "")

    # ---- completion --------------------------------------------------------------------------------------------------
    def candidates(self):
        """(partial text before the cursor, {popup text: text to insert}) for the cursor position: field names where a key goes, the values of an
        enum (or true and false) after `key: `. Empty when there is nothing to offer."""
        if self.schema is None or not self.root:
            return "", {}
        lines, number = self.lines(), self.cursor_line()
        before = lines[number][: self.editor.textCursor().positionInBlock()] if number < len(lines) else ""
        value = VALUE_BEFORE.match(before)
        if value:
            path = sc.yaml_path(lines[:number] + [before[: before.rfind(":") + 1]], number)[0]
            info = self.schema.describe(self.root, path)
            options = (info or {}).get("enum") or (["true", "false"] if (info or {}).get("type") == "boolean" else [])
            return value.group("partial"), {str(o): str(o) for o in options}
        match = KEY_BEFORE.match(before)
        if not match:
            return "", {}
        lead, partial = match.group("lead"), match.group("partial")
        indent = len(lead)
        taken = sc.sibling_keys(lines, number, indent)
        out = {}
        for name, kind, required, _desc in self.schema.fields(self.root, sc.parent_path(lines, number, indent)):
            if name in taken:
                continue
            pad = " " * (indent + 2)
            tail = ": " if kind not in ("object", "array") else (":\n" + pad + "- " if kind == "array" else ":\n" + pad)
            out[f"{name}  ({kind or 'any'}{', required' if required else ''})"] = name + tail
        return partial, out

    def complete(self):
        """Ctrl+Space: pop up what can be typed here."""
        partial, inserts = self.candidates()
        shown = [label for label in inserts if label.lower().startswith(partial.lower())]
        if not shown:
            return False
        self.inserts, self._partial = inserts, partial
        self.model.setStringList(shown)
        self.completer.setCompletionPrefix("")
        rect = self.editor.cursorRect()
        rect.setWidth(self.completer.popup().sizeHintForColumn(0) + self.completer.popup().verticalScrollBar().sizeHint().width() + 20)
        self.completer.complete(rect)
        return True

    def _insert(self, label):
        text = self.inserts.get(label, "")
        cursor = self.editor.textCursor()
        for _ in range(len(self._partial)):
            cursor.deletePreviousChar()
        cursor.insertText(text)
        self.editor.setTextCursor(cursor)

    def eventFilter(self, watched, event):
        if watched is self.editor and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Space and event.modifiers() & Qt.ControlModifier:
                self.complete()
                return True
            if self.completer.popup().isVisible() and event.key() in (Qt.Key_Enter, Qt.Key_Return, Qt.Key_Escape, Qt.Key_Tab, Qt.Key_Backtab):
                event.ignore()  # the popup takes these
                return True
        return super().eventFilter(watched, event)
