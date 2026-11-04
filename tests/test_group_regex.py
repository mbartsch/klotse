import re

import pytest
from PySide6.QtCore import QSettings, Qt

from klotse.ui.cluster_groups import ClusterGroups
from klotse.ui.group_suggest import DEFAULT_SEPARATORS, SEPARATOR_CHOICES, SuggestGroupsDialog, propose_regex

NAMES = ["eks-prod-eu-1", "eks-prod-eu-2", "eks-prod-us-1", "eks-stg-eu-1", "gke_prod_europe", "lab", "other"]


def dialog_for(qtbot, names=NAMES, groups=None):
    dialog = SuggestGroupsDialog(names, groups or ClusterGroups(QSettings()))
    qtbot.addWidget(dialog)
    return dialog


# ---- the function ----------------------------------------------------------------
def test_each_capture_group_becomes_a_level():
    chains = propose_regex(NAMES, r"^([a-z]+)-([a-z]+)-([a-z]+)")
    assert chains["eks-prod-eu-1"] == ["eks", "eks/prod", "eks/prod/eu"]
    assert chains["eks-prod-us-1"] == ["eks", "eks/prod"]  # prod/us is only one cluster
    assert chains["eks-stg-eu-1"] == ["eks"]
    assert "gke_prod_europe" not in chains and "lab" not in chains  # no match, or nothing shared


def test_a_pattern_without_capture_groups_uses_the_whole_match():
    chains = propose_regex(NAMES, r"^eks-(?:prod|stg)")
    assert chains["eks-prod-eu-1"] == ["eks-prod"] and chains["eks-prod-us-1"] == ["eks-prod"]
    assert "eks-stg-eu-1" not in chains  # "eks-stg" is a single cluster


def test_a_template_decides_the_path_and_may_reorder_the_groups():
    chains = propose_regex(NAMES, r"^([a-z]+)-([a-z]+)-([a-z]+)", template=r"\2/\3")
    assert chains["eks-prod-eu-1"] == ["prod", "prod/eu"]
    named = propose_regex(NAMES, r"^eks-(?P<env>[a-z]+)", template=r"\g<env>")
    assert named["eks-prod-eu-1"] == ["prod"]


def test_the_minimum_size_drops_levels_shared_by_too_few_clusters():
    assert "eks/prod/eu" not in sum(propose_regex(NAMES, r"^([a-z]+)-([a-z]+)-([a-z]+)", min_size=3).values(), [])
    assert propose_regex(NAMES, r"^([a-z]+)-([a-z]+)", min_size=10) == {}


def test_case_is_ignored_unless_asked():
    assert propose_regex(["EKS-A-1", "EKS-A-2"], r"^(eks)-")["EKS-A-1"] == ["EKS"]  # the name's own spelling is kept
    assert propose_regex(["EKS-A-1", "EKS-A-2"], r"^(eks)-", ignore_case=False) == {}


def test_a_slash_inside_a_capture_never_adds_a_level_by_accident():
    assert propose_regex(["a/b-1", "a/b-2"], r"^(.+)-\d")["a/b-1"] == ["a-b"]


def test_an_empty_pattern_suggests_nothing_and_a_bad_one_raises():
    assert propose_regex(NAMES, "") == {}
    with pytest.raises(re.error):
        propose_regex(NAMES, "([unclosed")
    with pytest.raises(re.error):
        propose_regex(["a-1", "a-2"], r"^(a)", template=r"\5")  # names that match, so the template is expanded
    with pytest.raises(IndexError):
        propose_regex(["a-1", "a-2"], r"^(a)", template=r"\g<missing>")


# ---- the dialog ------------------------------------------------------------------
def test_the_separators_are_listed_one_under_the_other_with_their_names(qtbot):
    dialog = dialog_for(qtbot)
    dialog.show()
    boxes = [dialog.separators[char] for char in SEPARATOR_CHOICES]
    tops = [box.mapTo(dialog, box.rect().topLeft()).y() for box in boxes]
    lefts = {box.mapTo(dialog, box.rect().topLeft()).x() for box in boxes}
    assert tops == sorted(tops) and len(set(tops)) == len(tops) and len(lefts) == 1  # a column, not a row
    assert "dot" in boxes[0].text() and "hyphen" in boxes[-1].text()
    assert [char for char, box in dialog.separators.items() if box.isChecked()] == list(DEFAULT_SEPARATORS)


def test_the_two_modes_show_their_own_settings(qtbot):
    dialog = dialog_for(qtbot)
    dialog.show()
    assert dialog.prefix_box.isVisible() and not dialog.regex_box.isVisible()
    dialog.by_regex.setChecked(True)
    assert dialog.regex_box.isVisible() and not dialog.prefix_box.isVisible()


def test_typing_a_regex_lists_the_groups_it_would_make(qtbot):
    dialog = dialog_for(qtbot)
    dialog.by_regex.setChecked(True)
    assert dialog.found.count() == 0 and "Type a regular expression" in dialog.summary.text()
    dialog.pattern.setText(r"^([a-z]+)-([a-z]+)-([a-z]+)")
    listed = [dialog.found.item(i).data(Qt.UserRole) for i in range(dialog.found.count())]
    assert listed == ["eks", "eks/prod", "eks/prod/eu"]
    assert dialog.assignments()["eks-prod-eu-2"] == "eks/prod/eu" and dialog.apply_button.isEnabled()
    dialog.template.setText(r"\2")
    assert [dialog.found.item(i).data(Qt.UserRole) for i in range(dialog.found.count())] == ["prod"]


def test_an_invalid_regex_is_explained_and_creates_nothing(qtbot):
    dialog = dialog_for(qtbot)
    dialog.by_regex.setChecked(True)
    dialog.pattern.setText("([unclosed")
    assert dialog.regex_error.text().startswith("Invalid expression") and dialog.found.count() == 0
    assert not dialog.apply_button.isEnabled()
    dialog.pattern.setText(r"^(eks)-")
    assert dialog.regex_error.text() == "" and dialog.found.count() == 1


def test_regex_groups_respect_the_ticks_and_existing_groups(qtbot):
    groups = ClusterGroups(QSettings())
    groups.assign("eks-prod-eu-2", "mine")
    dialog = dialog_for(qtbot, groups=groups)
    dialog.by_regex.setChecked(True)
    dialog.pattern.setText(r"^([a-z]+)-([a-z]+)-([a-z]+)")
    assert "eks-prod-eu-2" not in dialog.assignments()  # already grouped: left alone
    dialog.regroup.setChecked(True)
    assert dialog.assignments()["eks-prod-eu-2"] == "eks/prod/eu"
    for i in range(dialog.found.count()):
        if dialog.found.item(i).data(Qt.UserRole).count("/") >= 1:
            dialog.found.item(i).setCheckState(Qt.Unchecked)  # untick the deeper levels
    assert dialog.assignments()["eks-prod-eu-2"] == "eks"
