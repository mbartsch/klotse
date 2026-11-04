import re
from collections import Counter

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QSpinBox, QVBoxLayout)

from .cluster_groups import SEPARATOR

DEFAULT_SEPARATORS = "./:_@"  # a "-" is usually inside a word (group-of-cluster), so it is opt-in
SEPARATOR_CHOICES = "./:_@-"


def tokens(name, separators=DEFAULT_SEPARATORS, split_numbers=True):
    """Split a context name into its parts: "grp.api.prod.us1" -> ["grp", "api", "prod", "us", "1"].

    split_numbers also cuts a trailing number off the last part, so us1 and us2 share the prefix "us"."""
    parts = [p for p in re.split("[" + re.escape(separators) + "]", name) if p] if separators else [name]
    if split_numbers and parts:
        match = re.fullmatch(r"(\D.*?)(\d+)", parts[-1])
        if match:
            parts[-1:] = [match.group(1).rstrip("-_. ") or match.group(1), match.group(2)]  # "a-b-1" -> "a-b", "1"
    return [part.replace(SEPARATOR, "-") for part in parts] or [name]


def propose(names, separators=DEFAULT_SEPARATORS, min_size=2, depth=5, split_numbers=True):
    """Group suggestions from shared name prefixes: {context: [group paths, outermost first]}.

    A prefix becomes a group when at least `min_size` contexts share it and a part is left over for the cluster
    itself; `depth` limits how many levels deep a path may go. Contexts without a shared prefix are left out."""
    split = {name: tokens(name, separators, split_numbers) for name in names}
    shared = Counter()
    for parts in split.values():
        for k in range(1, min(depth, len(parts) - 1) + 1):
            shared[tuple(parts[:k])] += 1
    chains = {}
    for name, parts in split.items():
        chain = []
        for k in range(1, min(depth, len(parts) - 1) + 1):
            if shared[tuple(parts[:k])] < min_size:
                break  # a longer prefix is shared by even fewer contexts
            chain.append(SEPARATOR.join(parts[:k]))
        if chain:
            chains[name] = chain
    return chains


class SuggestGroupsDialog(QDialog):
    """Propose groups from the context names; the user ticks the ones to create."""

    def __init__(self, contexts, groups, parent=None):
        super().__init__(parent)
        self.contexts, self.groups = list(contexts), groups
        self.setWindowTitle("Suggest groups")
        self.resize(560, 520)
        info = QLabel("Clusters whose names start the same way can share a group. The name is split at the "
                      "separators below; a group is suggested when enough clusters share the beginning.")
        info.setWordWrap(True)
        self.separators = {}
        row = QHBoxLayout()
        for char in SEPARATOR_CHOICES:
            box = QCheckBox(char)
            box.setChecked(char in DEFAULT_SEPARATORS)
            box.toggled.connect(self.refresh)
            self.separators[char] = box
            row.addWidget(box)
        row.addStretch(1)
        self.split_numbers = QCheckBox("Split a trailing number (us1, us2 share the group us)")
        self.split_numbers.setChecked(True)
        self.regroup = QCheckBox("Also move clusters that already have a group")
        self.min_size = QSpinBox()
        self.min_size.setRange(2, 50)
        self.min_size.setValue(2)
        self.depth = QSpinBox()
        self.depth.setRange(1, 10)
        self.depth.setValue(5)
        for widget in (self.split_numbers, self.regroup):
            widget.toggled.connect(self.refresh)
        for spin in (self.min_size, self.depth):
            spin.valueChanged.connect(self.refresh)
        self.found = QListWidget()
        self.found.itemChanged.connect(self._update_apply)
        form = QFormLayout()
        form.addRow("Separators", row)
        form.addRow(self.split_numbers)
        form.addRow("Clusters needed for a group", self.min_size)
        form.addRow("Levels at most", self.depth)
        form.addRow(self.regroup)
        self.summary = QLabel()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.apply_button = buttons.button(QDialogButtonBox.Ok)
        self.apply_button.setText("Create groups")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addLayout(form)
        layout.addWidget(self.found, 1)
        layout.addWidget(self.summary)
        layout.addWidget(buttons)
        self.refresh()

    def _chains(self):
        chosen = "".join(char for char, box in self.separators.items() if box.isChecked())
        chains = propose(self.contexts, chosen, self.min_size.value(), self.depth.value(), self.split_numbers.isChecked())
        if not self.regroup.isChecked():
            chains = {ctx: chain for ctx, chain in chains.items() if not self.groups.group_of(ctx)}
        return chains

    def refresh(self, *_):
        self.found.blockSignals(True)
        self.found.clear()
        counts = Counter(path for chain in self._chains().values() for path in chain)
        for path in sorted(counts, key=str.casefold):
            item = QListWidgetItem(f"{path}    ({counts[path]} clusters)")
            item.setData(Qt.UserRole, path)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self.found.addItem(item)
        self.found.blockSignals(False)
        self._update_apply()

    def checked_groups(self):
        return {self.found.item(i).data(Qt.UserRole) for i in range(self.found.count())
                if self.found.item(i).checkState() == Qt.Checked}

    def assignments(self):
        """{context: group path}: each cluster goes to the deepest ticked group in its chain."""
        ticked, result = self.checked_groups(), {}
        for ctx, chain in self._chains().items():
            deepest = next((path for path in reversed(chain) if path in ticked), None)
            if deepest:
                result[ctx] = deepest
        return result

    def _update_apply(self, *_):
        count = len(self.assignments())
        self.apply_button.setEnabled(count > 0)
        self.summary.setText(f"{count} cluster(s) would be put in a group." if self.found.count()
                             else "No groups to suggest with these settings.")
