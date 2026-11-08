import subprocess

import pytest

from klotse._display import display_version_for


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def test_outside_a_git_checkout_the_plain_version_is_reported(tmp_path):
    assert display_version_for("0.3.0", root=tmp_path) == "0.3.0"


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-q", "--allow-empty", "-m", "first")
    return tmp_path


def test_an_untagged_commit_reports_the_version_and_the_commit(repo):
    text = display_version_for("0.3.0", root=repo)
    head = subprocess.run(["git", "rev-parse", "--short=9", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
    assert text == f"0.3.0+g{head}"


def test_a_commit_carrying_the_release_tag_reports_the_plain_version(repo):
    git(repo, "tag", "v0.3.0")
    assert display_version_for("0.3.0", root=repo) == "0.3.0"


def test_a_tag_of_another_version_does_not_make_it_the_release(repo):
    git(repo, "tag", "v0.2.0")
    assert display_version_for("0.3.0", root=repo).startswith("0.3.0+g")
