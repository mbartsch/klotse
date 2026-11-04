import re

import pytest
from PySide6.QtCore import QSettings

from klotse.ui.cluster_groups import ClusterGroups
from klotse.ui.group_suggest import SuggestGroupsDialog, regex_paths

NAMES = ["eks-prod-eu-1", "eks-prod-us-1", "eks-stg-eu-1", "gke_prod_europe", "lab"]
PATTERN = r"^([a-z]+)-([a-z]+)-([a-z]+)"


def regex_dialog(qtbot, names=NAMES):
    dialog = SuggestGroupsDialog(names, ClusterGroups(QSettings()))
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.by_regex.setChecked(True)
    return dialog


def rows(dialog):
    view = dialog.context_view
    return [(view.topLevelItem(i).text(0), view.topLevelItem(i).text(1), view.topLevelItem(i).isHidden())
            for i in range(view.topLevelItemCount())]


# ---- the function --------------------------------------------------------------
def test_regex_paths_says_what_the_pattern_makes_of_every_name():
    paths = regex_paths(NAMES, PATTERN)
    assert paths == {"eks-prod-eu-1": "eks/prod/eu", "eks-prod-us-1": "eks/prod/us", "eks-stg-eu-1": "eks/stg/eu",
                     "gke_prod_europe": None, "lab": None}
    assert regex_paths(NAMES, "") == {name: None for name in NAMES}  # an empty pattern matches nothing
    assert regex_paths(["abc"], r"b")["abc"] == "b"  # no capture group: the whole match
    assert regex_paths(["abc"], r"(x)?b")["abc"] == ""  # matched, but nothing to group by
    with pytest.raises(re.error):
        regex_paths(NAMES, "([bad")


# ---- the preview ---------------------------------------------------------------
def test_every_context_is_listed_with_its_group_or_no_match(qtbot):
    dialog = regex_dialog(qtbot)
    assert rows(dialog) == [(name, "", False) for name in NAMES]  # untested until a pattern is typed
    assert "Type a pattern" in dialog.match_summary.text()
    dialog.pattern.setText(PATTERN)
    assert rows(dialog) == [("eks-prod-eu-1", "eks/prod/eu", False), ("eks-prod-us-1", "eks/prod/us", False),
                            ("eks-stg-eu-1", "eks/stg/eu", False), ("gke_prod_europe", "no match", False),
                            ("lab", "no match", False)]
    assert dialog.match_summary.text() == "3 of 5 contexts match"


def test_the_preview_follows_the_pattern_template_and_case_option(qtbot):
    dialog = regex_dialog(qtbot, ["EKS-A-1", "eks-b-1"])
    dialog.pattern.setText(r"^(eks)-([a-z])")
    assert [r[1] for r in rows(dialog)] == ["EKS/A", "eks/b"]  # case ignored by default
    dialog.ignore_case.setChecked(False)
    assert [r[1] for r in rows(dialog)] == ["no match", "eks/b"]
    dialog.template.setText(r"\2")
    assert [r[1] for r in rows(dialog)] == ["no match", "b"]


def test_the_show_filter_narrows_the_list_to_matching_or_not_matching(qtbot):
    dialog = regex_dialog(qtbot)
    dialog.pattern.setText(PATTERN)
    dialog.show_filter.setCurrentIndex(1)  # matching only
    assert [name for name, _, hidden in rows(dialog) if not hidden] == ["eks-prod-eu-1", "eks-prod-us-1", "eks-stg-eu-1"]
    dialog.show_filter.setCurrentIndex(2)  # not matching only: the ones to work on
    assert [name for name, _, hidden in rows(dialog) if not hidden] == ["gke_prod_europe", "lab"]
    dialog.pattern.setText(r"^([a-z]+)_")  # the filter survives editing the pattern
    assert [name for name, _, hidden in rows(dialog) if not hidden][:3] == ["eks-prod-eu-1", "eks-prod-us-1", "eks-stg-eu-1"]
    dialog.show_filter.setCurrentIndex(0)
    assert not any(hidden for _, _, hidden in rows(dialog))


def test_an_invalid_pattern_lists_the_names_untested_with_the_error_shown(qtbot):
    dialog = regex_dialog(qtbot)
    dialog.pattern.setText("([bad")
    assert dialog.regex_error.text().startswith("Invalid expression")
    assert rows(dialog) == [(name, "", False) for name in NAMES]
    dialog.show_filter.setCurrentIndex(2)
    assert not any(hidden for _, _, hidden in rows(dialog))  # nothing was tested, so nothing is filtered out


def test_the_preview_is_only_shown_in_regex_mode(qtbot):
    dialog = regex_dialog(qtbot)
    assert dialog.context_view.isVisible()
    dialog.by_prefix.setChecked(True)
    assert not dialog.context_view.isVisible() and not dialog.regex_box.isVisible()
