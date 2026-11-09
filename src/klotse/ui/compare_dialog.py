"""Select two objects of one kind > Compare these two…: their YAML as a unified diff, noise left out by default. Looking only."""
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout

from ..k8s import compare
from . import theme
from .manifest import dump_manifest


def label(row):
    name = f"{row.namespace}/{row.name}" if getattr(row, "namespace", "") else row.name
    cluster = getattr(row, "cluster", "")
    return f"{cluster}/{name}" if cluster else name


def render(lines):
    """HTML for diff lines: added lines green, removed red, headers muted. The +/- sign is in the text, so colour is never
    the only cue. Every cluster-supplied character is escaped."""
    out = []
    for line in lines:
        style = ""
        if line.startswith("+++") or line.startswith("---"):
            style = f"color:{theme.MUTED}"
        elif line.startswith("@@"):
            style = f"color:{theme.ACCENT}"
        elif line.startswith("+"):
            style = f"color:{theme.OK}"
        elif line.startswith("-"):
            style = f"color:{theme.BAD}"
        out.append(f'<span style="{style}">{escape(line) or "&nbsp;"}</span>')
    return "<pre style=\"margin:0\">" + "<br>".join(out) + "</pre>"


class CompareDialog(QDialog):
    def __init__(self, context, kind, left, right, parent=None):
        super().__init__(parent)
        self.left, self.right = left, right
        self.setWindowTitle(f"Compare {kind}s")
        self.resize(900, 620)
        self.header = QLabel(f"<b>{escape(context)}</b> · {escape(label(left))} (−) against {escape(label(right))} (+)")
        self.header.setTextFormat(Qt.RichText)
        self.noise = QCheckBox("Leave out fields the cluster fills in (uid, resourceVersion, managedFields, timestamps…)")
        self.noise.setChecked(True)
        self.status = QCheckBox("Include status")
        self.identity = QCheckBox("Ignore name and namespace")
        self.identity.setChecked(True)
        self.summary = QLabel()
        theme.muted(self.summary)
        self.view = QTextEdit(readOnly=True)
        self.view.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.view.setLineWrapMode(QTextEdit.NoWrap)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        options = QHBoxLayout()
        for box in (self.noise, self.status, self.identity):
            options.addWidget(box)
            box.toggled.connect(lambda _=None: self.update_view())
        options.addStretch(1)
        lay = QVBoxLayout(self)
        lay.addWidget(self.header)
        lay.addLayout(options)
        lay.addWidget(self.summary)
        lay.addWidget(self.view, 1)
        lay.addWidget(close)
        self.update_view()

    def lines(self):
        options = dict(noise=self.noise.isChecked(), status=self.status.isChecked(), identity=self.identity.isChecked())
        texts = [dump_manifest(compare.cleaned(row.raw, **options)) for row in (self.left, self.right)]
        return compare.diff_lines(texts[0], texts[1], label(self.left), label(self.right))

    def update_view(self):
        lines = self.lines()
        if not lines:
            self.summary.setText("No differences with these options.")
            self.view.setPlainText("")
            return
        added, removed = compare.stats(lines)
        self.summary.setText(f"{removed} line{'s' if removed != 1 else ''} only on the left (−), "
                             f"{added} only on the right (+).")
        self.view.setHtml(render(lines))
