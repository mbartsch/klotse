import re
from collections import Counter

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QRadioButton, QSpinBox,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from .cluster_groups import SEPARATOR, clean_path, lineage
from . import theme

DEFAULT_SEPARATORS = "./:_@"  # a "-" is usually inside a word (group-of-cluster), so it is opt-in
SEPARATOR_CHOICES = "./:_@-"
SEPARATOR_NAMES = {".": "dot", "/": "slash", ":": "colon", "_": "underscore", "@": "at sign",
                   "-": "hyphen (usually part of a word, e.g. group-of-cluster)"}


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


def regex_paths(names, pattern, template="", ignore_case=True):
    """What a pattern makes of each name: {context: group path}, with None for a name the pattern does not match
    and "" for a match that gives no path. An empty pattern matches nothing. Raises like propose_regex."""
    if not pattern:
        return {name: None for name in names}
    compiled = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    paths = {}
    for name in names:
        match = compiled.search(name)
        if match is None:
            paths[name] = None
            continue
        if template:
            raw = match.expand(template)
        elif compiled.groups:
            raw = SEPARATOR.join((part or "").replace(SEPARATOR, "-") for part in match.groups())
        else:
            raw = match.group(0).replace(SEPARATOR, "-")
        paths[name] = clean_path(raw)
    return paths


def propose_regex(names, pattern, template="", min_size=2, ignore_case=True):
    """Group suggestions from a regular expression: {context: [group paths, outermost first]}.

    The pattern is searched in each name. Its capture groups become the levels of the group path, in order
    ("^([a-z]+)-(\\w+)-" on "eks-prod-eu1" gives "eks/prod"); a pattern without capture groups uses the whole match
    as one level. `template` replaces that rule with an expansion such as "\\2/\\1" or "\\g<env>" (use "/" for
    levels). Names that do not match stay ungrouped, and a level shared by fewer than `min_size` names is dropped.
    Raises re.error (or IndexError for a template naming a missing group) when the pattern or template is bad."""
    paths = {name: path for name, path in regex_paths(names, pattern, template, ignore_case).items() if path}
    counts = Counter(level for path in paths.values() for level in lineage(path))
    chains = {}
    for name, path in paths.items():
        chain = []
        for level in lineage(path):
            if counts[level] < min_size:
                break
            chain.append(level)
        if chain:
            chains[name] = chain
    return chains


class SuggestGroupsDialog(QDialog):
    """Propose groups from the context names, by shared prefixes or by a regular expression; the user ticks the
    ones to create."""

    def __init__(self, contexts, groups, parent=None):
        super().__init__(parent)
        self.contexts, self.groups = list(contexts), groups
        self.setWindowTitle("Suggest groups")
        self.resize(660, 880)
        info = QLabel("Clusters can share a group when their names start the same way, or when a regular "
                      "expression picks the group out of the name. Tick the suggestions to create.")
        info.setWordWrap(True)

        self.by_prefix = QRadioButton("Shared name prefixes")
        self.by_regex = QRadioButton("Regular expression")
        self.by_prefix.setChecked(True)
        mode = QButtonGroup(self)
        mode.addButton(self.by_prefix)
        mode.addButton(self.by_regex)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.by_prefix)
        mode_row.addWidget(self.by_regex)
        mode_row.addStretch(1)

        # one under the other, each with its name: single characters side by side were hard to tell apart
        self.separators = {}
        self.prefix_box = QGroupBox("Split names at")
        split = QVBoxLayout(self.prefix_box)
        for char in SEPARATOR_CHOICES:
            box = QCheckBox(f"{char}     {SEPARATOR_NAMES[char]}")
            box.setChecked(char in DEFAULT_SEPARATORS)
            self.separators[char] = box
            split.addWidget(box)
        self.split_numbers = QCheckBox("Split a trailing number (us1 and us2 share the group us)")
        self.split_numbers.setChecked(True)
        self.depth = QSpinBox()
        self.depth.setRange(1, 10)
        self.depth.setValue(5)
        split.addWidget(self.split_numbers)
        depth_row = QFormLayout()
        depth_row.addRow("Levels at most", self.depth)
        split.addLayout(depth_row)

        self.regex_box = QGroupBox("Regular expression")
        regex = QFormLayout(self.regex_box)
        self.pattern = QLineEdit()
        self.pattern.setPlaceholderText(r"^([^.]+)\.([^.]+)\.   (each capture group becomes a level)")
        self.template = QLineEdit()
        self.template.setPlaceholderText(r"optional, e.g. \2/\1 or \g<env>   (use / for levels)")
        self.ignore_case = QCheckBox("Ignore case")
        self.ignore_case.setChecked(True)
        self.regex_error = QLabel()
        self.regex_error.setStyleSheet("color: #e5484d;")
        self.regex_error.setWordWrap(True)
        hint = QLabel(r"Example: eks-prod-eu-1 with ^([a-z]+)-([a-z]+)-([a-z]+) gives the groups eks, eks/prod and "
                      r"eks/prod/eu. Names that do not match stay ungrouped.")
        hint.setWordWrap(True)
        regex.addRow("Pattern", self.pattern)
        regex.addRow("Group path", self.template)
        regex.addRow(self.ignore_case)
        regex.addRow(hint)
        regex.addRow(self.regex_error)
        # Every context with what the pattern makes of it, to build the expression against the real names
        self.context_view = QTreeWidget()
        self.context_view.setHeaderLabels(["Context", "Group it would get"])
        self.context_view.setRootIsDecorated(False)
        self.context_view.setMinimumHeight(170)
        self.show_filter = QComboBox()
        self.show_filter.addItems(["All contexts", "Matching only", "Not matching only"])
        self.show_filter.currentIndexChanged.connect(self._apply_filter)
        self.match_summary = QLabel()
        regex.addRow("Show", self.show_filter)
        regex.addRow(self.match_summary)
        regex.addRow(self.context_view)

        self.min_size = QSpinBox()
        self.min_size.setRange(2, 50)
        self.min_size.setValue(2)
        self.regroup = QCheckBox("Also move clusters that already have a group")
        common = QFormLayout()
        common.addRow("Clusters needed for a group", self.min_size)
        common.addRow(self.regroup)

        for button in (self.by_prefix, self.by_regex, self.split_numbers, self.regroup, self.ignore_case,
                       *self.separators.values()):
            button.toggled.connect(self.refresh)
        for spin in (self.min_size, self.depth):
            spin.valueChanged.connect(self.refresh)
        for line in (self.pattern, self.template):
            line.textChanged.connect(self.refresh)
        self.found = QListWidget()
        self.found.itemChanged.connect(self._update_apply)
        self.summary = QLabel()
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.apply_button = buttons.button(QDialogButtonBox.Ok)
        self.apply_button.setText("Create groups")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(info)
        layout.addLayout(mode_row)
        layout.addWidget(self.prefix_box)
        layout.addWidget(self.regex_box)
        layout.addLayout(common)
        layout.addWidget(self.found, 1)
        layout.addWidget(self.summary)
        layout.addWidget(buttons)
        self.refresh()

    def _chains(self):
        if self.by_regex.isChecked():
            try:
                chains = propose_regex(self.contexts, self.pattern.text(), self.template.text(), self.min_size.value(),
                                       self.ignore_case.isChecked())
                self.regex_error.clear()
            except (re.error, IndexError) as error:
                self.regex_error.setText(f"Invalid expression: {error}")
                chains = {}
        else:
            chosen = "".join(char for char, box in self.separators.items() if box.isChecked())
            chains = propose(self.contexts, chosen, self.min_size.value(), self.depth.value(),
                             self.split_numbers.isChecked())
        if not self.regroup.isChecked():
            chains = {ctx: chain for ctx, chain in chains.items() if not self.groups.group_of(ctx)}
        return chains

    def refresh(self, *_):
        regex = self.by_regex.isChecked()
        self.prefix_box.setVisible(not regex)
        self.regex_box.setVisible(regex)
        if regex:
            self._refresh_contexts()
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

    def _refresh_contexts(self):
        """List every context with the group the pattern gives it: matches normally, the rest dimmed."""
        try:
            paths = regex_paths(self.contexts, self.pattern.text(), self.template.text(), self.ignore_case.isChecked())
        except (re.error, IndexError):  # the error is already shown; list the names untested
            paths = {name: None for name in self.contexts}
            tested = False
        else:
            tested = bool(self.pattern.text())
        self.context_view.clear()
        muted = QBrush(QColor(theme.MUTED))
        matching = 0
        for name in self.contexts:
            path = paths[name]
            if not tested:
                state, group = "untested", ""
            elif path is None:
                state, group = "no match", "no match"
            else:
                state, group = "match", path or "(no group from this match)"
                matching += 1
            item = QTreeWidgetItem([name, group])
            item.setData(0, Qt.UserRole, state)
            if state == "no match":
                item.setForeground(0, muted)
                item.setForeground(1, muted)
            self.context_view.addTopLevelItem(item)
        self.context_view.resizeColumnToContents(0)
        self.match_summary.setText(f"{matching} of {len(self.contexts)} contexts match" if tested
                                   else "Type a pattern to see which contexts match.")
        self._apply_filter()

    def _apply_filter(self, *_):
        wanted = {0: None, 1: "match", 2: "no match"}[self.show_filter.currentIndex()]
        for i in range(self.context_view.topLevelItemCount()):
            item = self.context_view.topLevelItem(i)
            state = item.data(0, Qt.UserRole)
            item.setHidden(wanted is not None and state != "untested" and state != wanted)

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
        if self.found.count():
            self.summary.setText(f"{count} cluster(s) would be put in a group.")
        elif self.by_regex.isChecked() and not self.pattern.text():
            self.summary.setText("Type a regular expression to see the groups it would make.")
        else:
            self.summary.setText("No groups to suggest with these settings.")
