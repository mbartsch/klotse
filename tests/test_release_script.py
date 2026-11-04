import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import release  # noqa: E402
from release import ReleaseError, VERSION_FILE  # noqa: E402

TAGS = ["v0.1.0", "v0.1.1", "v0.10.0", "not-a-version"]


def test_the_version_file_is_tracked_valid_and_what_the_package_reports():
    import klotse
    assert release.parse(release.read_version()) and klotse.__version__ == release.read_version()
    tracked = subprocess.run(["git", "ls-files", VERSION_FILE], capture_output=True, text=True, cwd=ROOT).stdout
    assert tracked.strip() in ("", VERSION_FILE)  # untracked only before this file is first committed
    assert VERSION_FILE not in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_versions_are_plain_and_compared_as_numbers():
    assert release.parse("1.2.3") == (1, 2, 3)
    for bad in ("1.2", "v1.2.3", "1.2.3rc1", "1.2.3.dev4", "", "a.b.c"):
        with pytest.raises(ReleaseError):
            release.parse(bad)
    assert release.latest(TAGS) == "0.10.0"  # 0.10.0 is higher than 0.1.1
    assert release.latest([]) is None and release.latest(["foo"]) is None


def test_a_new_version_must_be_higher_and_not_tagged():
    release.check_new_version("0.11.0", TAGS)
    release.check_new_version("1.0.0", [])
    for bad in ("0.10.0", "0.9.9", "0.2.0"):
        with pytest.raises(ReleaseError):
            release.check_new_version(bad, TAGS)
    with pytest.raises(ReleaseError, match="not a plain"):
        release.check_new_version("0.11", TAGS)


def test_writing_the_version_changes_only_that_line(tmp_path):
    (tmp_path / "src/klotse").mkdir(parents=True)
    path = tmp_path / VERSION_FILE
    path.write_text('"""doc"""\n__version__ = "0.1.0"\n', encoding="utf-8")
    release.write_version("0.2.0", tmp_path)
    assert path.read_text(encoding="utf-8") == '"""doc"""\n__version__ = "0.2.0"\n'
    assert release.read_version(tmp_path) == "0.2.0"
    path.write_text("nothing here\n", encoding="utf-8")
    with pytest.raises(ReleaseError):
        release.write_version("0.3.0", tmp_path)
    with pytest.raises(ReleaseError):
        release.read_version(tmp_path)


def check(title="Release v0.11.0", branch="release/v0.11.0", files=(VERSION_FILE,), in_file="0.11.0", tags=TAGS):
    return release.check_pr(title, branch, list(files), in_file, tags)


def test_a_valid_release_pr_passes():
    assert check() == "0.11.0"
    assert check(files=(VERSION_FILE, "CHANGELOG.md")) == "0.11.0"
    assert check(files=(VERSION_FILE.replace("/", "\\"),)) == "0.11.0"  # Windows separators


@pytest.mark.parametrize("kwargs, message", [
    ({"title": "Release 0.11.0"}, "title"),                         # branch says release, title is wrong
    ({"branch": "feature/x"}, "must come from the branch"),         # title says release, branch is not
    ({"branch": "release/v0.12.0"}, "branch says"),
    ({"files": (VERSION_FILE, "src/klotse/ui/workspace.py")}, "nothing but the version"),
    ({"files": ("CHANGELOG.md",)}, "must change"),
    ({"in_file": "0.10.1"}, "title says"),
    ({"title": "Release v0.10.0", "branch": "release/v0.10.0", "in_file": "0.10.0"}, "not higher|already exists"),
])
def test_a_release_pr_that_breaks_the_definition_is_refused(kwargs, message):
    with pytest.raises(ReleaseError, match=message):
        check(**kwargs)


def test_other_prs_may_not_touch_the_version():
    assert release.check_pr("Add a thing", "feature/thing", ["src/a.py", "README.md"], "0.1.0", TAGS) is None
    with pytest.raises(ReleaseError, match="only in a release PR"):
        release.check_pr("Add a thing", "feature/thing", [VERSION_FILE], "0.9.0", TAGS)


def test_only_a_merged_release_commit_is_tagged():
    assert release.tag_for_commit("Release v0.11.0 (#41)", "0.11.0", TAGS) == "0.11.0"
    assert release.tag_for_commit("Release v0.11.0", "0.11.0", TAGS) == "0.11.0"
    for subject in ("AWS nodes: a plugin (#29)", "Revert \"Release v0.11.0\"", "release v0.11.0", "Release v0.11.0 hotfix"):
        assert release.tag_for_commit(subject, "0.11.0", TAGS) is None
    with pytest.raises(ReleaseError, match="says 0.10.9"):
        release.tag_for_commit("Release v0.11.0 (#41)", "0.10.9", TAGS)
    with pytest.raises(ReleaseError, match="already exists"):
        release.tag_for_commit("Release v0.1.1 (#9)", "0.1.1", TAGS)


def test_a_build_from_a_tag_needs_the_tag_to_match_the_source():
    release.check_tag("v0.11.0", "0.11.0")
    for tag in ("v0.11.1", "0.11.0", "release-0.11.0"):
        with pytest.raises(ReleaseError):
            release.check_tag(tag, "0.11.0")


def test_the_command_line_reports_errors_with_a_status(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(release, "tags", lambda cwd=None: TAGS)
    assert release.main(["release.py", "check-tag", "v9.9.9"]) == 1
    assert "does not match" in capsys.readouterr().err
    assert release.main(["release.py", "tag-for-commit", "ordinary commit"]) == 0 and capsys.readouterr().out == ""
    assert release.main(["release.py", "latest"]) == 0 and capsys.readouterr().out.strip() == "0.10.0"
    assert release.main(["release.py", "bogus"]) == 2


def test_a_build_reports_the_commit_unless_it_is_the_tagged_release():
    assert release.display_version("0.2.0", ["v0.2.0"], "abc123def") == "0.2.0"
    assert release.display_version("0.2.0", ["v0.2.0", "latest"], "abc123def") == "0.2.0"
    assert release.display_version("0.2.0", [], "abc123def") == "0.2.0+gabc123def"
    assert release.display_version("0.2.0", ["v0.1.0"], "abc123def") == "0.2.0+gabc123def"  # an older tag is not this release
    assert release.display_version("0.2.0", [], "") == "0.2.0"  # no git data: nothing to add


def test_the_build_info_is_written_for_the_build_only(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for config in (["user.name", "t"], ["user.email", "t@example.com"]):
        subprocess.run(["git", "config", *config], cwd=tmp_path, check=True)
    (tmp_path / "src/klotse").mkdir(parents=True)
    (tmp_path / VERSION_FILE).write_text('__version__ = "0.2.0"\n', encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=tmp_path, check=True)
    sha = subprocess.run(["git", "rev-parse", "--short=9", "HEAD"], capture_output=True, text=True, cwd=tmp_path).stdout.strip()
    assert release.write_build_info(tmp_path) == f"0.2.0+g{sha}"
    assert (tmp_path / "src/klotse/_build_info.py").read_text(encoding="utf-8").endswith(f'display_version = "0.2.0+g{sha}"\n')
    subprocess.run(["git", "tag", "v0.2.0"], cwd=tmp_path, check=True)
    assert release.write_build_info(tmp_path) == "0.2.0"  # the tagged commit reports the plain version
    assert "_build_info" in (ROOT / ".gitignore").read_text(encoding="utf-8")
