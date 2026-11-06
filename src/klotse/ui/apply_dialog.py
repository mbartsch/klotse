"""File > Apply YAML…: paste or open one or more manifests, check them with the server (dry run) and apply them. The dialog
only reads and confirms; the workspace sends the documents to the cluster on a worker and hands the outcome back."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton,
                               QVBoxLayout)

from . import theme
from .apply_model import MAX_TEXT, ApplyProblem, label, parse_documents
from .detail_panel import YamlHighlighter


def outcome_line(result):
    """'✔ Deployment shop/web: applied' / '✖ ...: the server's reason' for one (kind, ns, name, ok, message)."""
    kind, ns, name, ok, message = result
    return f"{'✔' if ok else '✖'} {kind} {ns + '/' if ns else ''}{name}: {message}"


class ApplyDialog(QDialog):
    run_requested = Signal(list, bool)  # documents, dry run
    applied = Signal()  # something was really applied (the lists may have changed)

    def __init__(self, context, namespace, parent=None):
        super().__init__(parent)
        self.context, self.namespace = context, namespace
        self.setWindowTitle("Apply YAML")
        self.resize(760, 640)
        self.editor = QPlainTextEdit()
        self.editor.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.editor.setPlaceholderText("Paste one or more manifests, separated by ---, or open a file.")
        self.highlighter = YamlHighlighter(self.editor.document())
        self.result = QPlainTextEdit(readOnly=True)
        self.result.setMaximumHeight(180)
        self.result.setPlaceholderText("The outcome of Check and Apply shows here.")
        self.open_button = QPushButton("Open file…")
        self.check_button = QPushButton("Check (dry run)")
        self.check_button.setToolTip("Ask the server to validate the manifests without storing anything")
        self.apply_button = QPushButton("Apply…")
        self.close_button = QPushButton("Close")
        self.open_button.clicked.connect(self._open_file)
        self.check_button.clicked.connect(lambda: self._start(dry_run=True))
        self.apply_button.clicked.connect(lambda: self._start(dry_run=False))
        self.close_button.clicked.connect(self.accept)
        note = QLabel(f"<b>{_plain(context)}</b> · objects without a namespace go to <b>{_plain(namespace)}</b>. "
                      "Existing objects are updated to match the YAML (server-side apply); fields another tool owns are "
                      "reported as conflicts, not overwritten.")
        note.setWordWrap(True)
        note.setTextFormat(Qt.RichText)
        theme.muted(note)
        buttons = QHBoxLayout()
        for button in (self.open_button, self.check_button, self.apply_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(note)
        layout.addWidget(self.editor, 1)
        layout.addLayout(buttons)
        layout.addWidget(self.result)
        self._busy = False
        self._read_only = False

    def set_read_only(self, on):
        """A read-only cluster still allows Check (a dry run stores nothing) but not Apply."""
        self._read_only = bool(on)
        self.apply_button.setEnabled(not on and not self._busy)
        self.apply_button.setToolTip("This cluster is in read-only mode: Check works, Apply does not" if on else "")

    # ---- input ---------------------------------------------------------------------------------------------------
    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open YAML", "", "YAML (*.yaml *.yml *.json);;All files (*)")
        if path:
            self.load_file(path)

    def load_file(self, path):
        """Put a file's text in the editor; a file that is too big or not text is refused with a message."""
        try:
            with open(path, "rb") as handle:
                data = handle.read(MAX_TEXT + 1)
            if len(data) > MAX_TEXT:
                raise ValueError(f"The file is bigger than {MAX_TEXT:,} bytes.")
            self.editor.setPlainText(data.decode("utf-8"))
            self.result.clear()
        except (OSError, ValueError) as e:
            self.result.setPlainText(f"Cannot open the file: {e}")

    # ---- running -------------------------------------------------------------------------------------------------
    def _start(self, dry_run):
        if self._busy:
            return
        try:
            docs = parse_documents(self.editor.toPlainText())
        except ApplyProblem as e:
            self.result.setPlainText(f"✖ {e}")
            return
        if not dry_run and self._read_only:
            return
        if not dry_run and not self._confirm(docs):
            return
        self.set_busy(True, "Checking with the server…" if dry_run else "Applying…")
        self._dry_run = dry_run
        self.run_requested.emit(docs, dry_run)

    def _confirm(self, docs):
        names = [label(d, self.namespace) for d in docs]
        shown = "\n".join(names[:10] + ([f"…and {len(names) - 10} more"] if len(names) > 10 else []))
        return QMessageBox.question(
            self, "Apply YAML", f"Cluster: {self.context}\nApply {len(docs)} object{'s' if len(docs) != 1 else ''}?\n\n{shown}\n\n"
            "Existing objects are changed to match the YAML. Check (dry run) first if you are not sure.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def set_busy(self, busy, text=""):
        self._busy = busy
        for button in (self.open_button, self.check_button, self.apply_button):
            button.setEnabled(not busy and not (button is self.apply_button and self._read_only))
        if busy:
            self.result.setPlainText(text)

    def show_results(self, results, dry_run):
        """The outcome per document (the service's tuples), with a summary line first."""
        self.set_busy(False)
        failed = sum(1 for r in results if not r[3])
        verb = "valid" if dry_run else "applied"
        head = (f"✖ {failed} of {len(results)} failed." if failed else f"✔ All {len(results)} {verb}.") \
            + (" Nothing was stored (dry run)." if dry_run else "")
        self.result.setPlainText("\n".join([head, *map(outcome_line, results)]))
        if not dry_run and failed < len(results):
            self.applied.emit()

    def show_error(self, message):
        self.set_busy(False)
        self.result.setPlainText(f"✖ {message}")


def _plain(text):
    from html import escape
    return escape(str(text))
