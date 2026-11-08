"""Review changes before saving: what the server says to an edit (a server-side dry run, nothing stored) and what would change in the
object. Opened by the edit flow after Save in a form or the YAML editor; the user answers Save, Back to editing, or Cancel."""
import html

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout

from ..k8s import compare
from . import theme
from .manifest import dump_manifest

SAVE, EDIT, CANCEL = "save", "edit", "cancel"


def diff_text(kind, name, live, stored, creating):
    """The unified diff lines of the live object against the object the server would store (both without the fields the cluster fills in),
    or for a new object its whole manifest. Returns (lines, summary)."""
    after = dump_manifest(compare.cleaned(stored, True, False, False))
    if creating:
        return ["+" + line for line in after.splitlines()], f"Would create {kind} {name}"
    before = dump_manifest(compare.cleaned(live, True, False, False))
    lines = compare.diff_lines(before, after, "live", "after saving")
    added, removed = compare.stats(lines)
    return lines, (f"{added} line(s) added, {removed} removed" if lines else "No change: the server would store the same object.")


class ReviewDialog(QDialog):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(820, 560)
        self.verdict = CANCEL
        self.status = QLabel("Asking the server (nothing is stored)…")
        self.status.setWordWrap(True)
        self.summary = QLabel("")
        theme.muted(self.summary)
        self.view = QTextEdit(readOnly=True)
        self.view.setLineWrapMode(QTextEdit.NoWrap)
        self.save_button = QPushButton("Save")
        self.save_button.setEnabled(False)
        self.back_button = QPushButton("Back to editing")
        cancel = QPushButton("Cancel")
        self.save_button.clicked.connect(lambda: self._finish(SAVE))
        self.back_button.clicked.connect(lambda: self._finish(EDIT))
        cancel.clicked.connect(lambda: self._finish(CANCEL))
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        for button in (self.save_button, self.back_button, cancel):
            buttons.addWidget(button)
        lay = QVBoxLayout(self)
        lay.addWidget(self.status)
        lay.addWidget(self.summary)
        lay.addWidget(self.view, 1)
        lay.addLayout(buttons)
        self.save_button.setDefault(False)
        self.back_button.setDefault(True)

    def _finish(self, verdict):
        self.verdict = verdict  # closing the window or Esc leaves the initial verdict, Cancel
        self.accept()

    def show_result(self, result, lines=(), summary=""):
        """Fill the window: `result` is what `KubeService.dry_run_save` returned. A refusal disables Save (the real save would be refused
        the same way) and shows the server's sentence."""
        if result.get("ok"):
            self.status.setText("✔ The server accepts this (a dry run: nothing was stored).")
            self.save_button.setEnabled(True)
            self.save_button.setDefault(True)
            self.back_button.setDefault(False)
        else:
            self.status.setText("✖ The server refuses this: " + str(result.get("message", "")))
            self.save_button.setEnabled(False)
        self.summary.setText(summary)
        self.view.setHtml("<pre style='margin:0'>" + "\n".join(self._colour(line) for line in lines) + "</pre>" if lines else "")

    @staticmethod
    def _colour(line):
        text = html.escape(line)
        if line.startswith("+") and not line.startswith("+++"):
            return f"<span style='color:{theme.OK}'>{text}</span>"
        if line.startswith("-") and not line.startswith("---"):
            return f"<span style='color:{theme.BAD}'>{text}</span>"
        return f"<span style='color:{theme.MUTED}'>{text}</span>" if line.startswith(("@@", "---", "+++")) else text
