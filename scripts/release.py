"""Release rules for Klotse, used by .github/workflows/release.yml, the package/PyPI workflows and the tests.

The version lives in src/klotse/_version.py and changes only in a *release PR*:

  * branch  release/vX.Y.Z
  * title   Release vX.Y.Z   (squash-merged it becomes "Release vX.Y.Z (#N)" on master)
  * changes only the version file (and CHANGELOG.md when there is one), to X.Y.Z, a version higher than the latest tag

The commit that merges it is then tagged vX.Y.Z, so a tagged commit always contains its own version.

    python scripts/release.py prepare 0.2.0               write the version file (after checking it)
    python scripts/release.py check-pr TITLE BRANCH FILES...   validate a pull request (release or not)
    python scripts/release.py tag-for-commit "SUBJECT"    print X.Y.Z when the commit is a release merge, else nothing
    python scripts/release.py check-tag v0.2.0            the file must say 0.2.0 (builds from a tag)
    python scripts/release.py latest                      print the latest version tag
    python scripts/release.py display-version             X.Y.Z, or X.Y.Z+g<commit> when HEAD is not the tagged release
    python scripts/release.py write-build-info            write src/klotse/_build_info.py (git-ignored) for a build

Tags are read with `git tag --list v*` in the current directory. Standard library only.
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = "src/klotse/_version.py"
ALLOWED_RELEASE_FILES = {VERSION_FILE, "CHANGELOG.md"}
VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
TITLE = re.compile(r"Release v(\d+\.\d+\.\d+)")
MERGE_SUBJECT = re.compile(r"Release v(\d+\.\d+\.\d+)(?: \(#\d+\))?")  # GitHub's squash merge appends " (#N)"
LINE = re.compile(r'^__version__ = "([^"]*)"$', re.MULTILINE)


class ReleaseError(Exception):
    """A rule was broken; the message says which and what to do."""


def parse(version):
    """(major, minor, patch) for a plain X.Y.Z, else ReleaseError. No pre-releases or suffixes."""
    match = VERSION.fullmatch(str(version).strip())
    if not match:
        raise ReleaseError(f"{version!r} is not a plain version like 1.2.3")
    return tuple(int(part) for part in match.groups())


def read_version(root=ROOT):
    text = (Path(root) / VERSION_FILE).read_text(encoding="utf-8")
    match = LINE.search(text)
    if not match:
        raise ReleaseError(f'{VERSION_FILE} has no line __version__ = "X.Y.Z"')
    return match.group(1)


def write_version(version, root=ROOT):
    path = Path(root) / VERSION_FILE
    text = path.read_text(encoding="utf-8")
    if not LINE.search(text):
        raise ReleaseError(f'{VERSION_FILE} has no line __version__ = "X.Y.Z"')
    path.write_text(LINE.sub(f'__version__ = "{version}"', text, count=1), encoding="utf-8", newline="")


def tags(cwd=None):
    out = subprocess.run(["git", "tag", "--list", "v*"], capture_output=True, text=True, check=True, cwd=cwd).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def latest(tag_names):
    """The highest vX.Y.Z among the tags (None when there is none); other tags are ignored."""
    found = [parse(t[1:]) for t in tag_names if t.startswith("v") and VERSION.fullmatch(t[1:])]
    return ".".join(map(str, max(found))) if found else None


def check_new_version(version, tag_names):
    """The version must be a plain X.Y.Z, higher than every existing release, and not tagged yet."""
    wanted = parse(version)
    if f"v{version}" in tag_names:
        raise ReleaseError(f"the tag v{version} already exists; a tag is never moved or reused")
    last = latest(tag_names)
    if last is not None and wanted <= parse(last):
        raise ReleaseError(f"{version} is not higher than the latest release {last}")


def check_pr(title, branch, files, version_in_file, tag_names):
    """Validate a pull request. A release PR must follow the whole definition; any other PR must leave the version alone."""
    release_title = TITLE.fullmatch(title.strip())
    release_branch = re.fullmatch(r"release/v(\d+\.\d+\.\d+)", branch.strip())
    files = {f.replace("\\", "/") for f in files if f}
    if not release_title and not release_branch:
        if VERSION_FILE in files:
            raise ReleaseError(f"{VERSION_FILE} changes only in a release PR (branch release/vX.Y.Z, title 'Release vX.Y.Z')")
        return None
    if not release_title:
        raise ReleaseError(f"a release/ branch needs the title 'Release vX.Y.Z' (the title is {title!r})")
    if not release_branch:
        raise ReleaseError(f"a PR titled {title!r} must come from the branch release/v{release_title.group(1)}")
    version = release_title.group(1)
    if release_branch.group(1) != version:
        raise ReleaseError(f"the branch says {release_branch.group(1)} but the title says {version}")
    if VERSION_FILE not in files:
        raise ReleaseError(f"a release PR must change {VERSION_FILE}")
    extra = sorted(files - ALLOWED_RELEASE_FILES)
    if extra:
        raise ReleaseError("a release PR changes nothing but the version" + " (and CHANGELOG.md)"
                           + f"; also changed: {', '.join(extra)}")
    if version_in_file != version:
        raise ReleaseError(f"{VERSION_FILE} says {version_in_file}, the title says {version}")
    check_new_version(version, tag_names)
    return version


def tag_for_commit(subject, version_in_file, tag_names):
    """The version to tag for a commit on master: None for an ordinary commit, X.Y.Z for a merged release PR."""
    match = MERGE_SUBJECT.fullmatch(subject.strip())
    if not match:
        return None
    version = match.group(1)
    if version_in_file != version:
        raise ReleaseError(f"the commit says Release v{version} but {VERSION_FILE} says {version_in_file}")
    check_new_version(version, tag_names)
    return version


def display_version(version_in_file, head_tags, commit):
    """What a build reports: the plain version when HEAD carries the tag v<version>, otherwise <version>+g<commit> (a build
    from master or a branch, which has the last release's number and would otherwise look like that release). Without git
    data (no commit) it is the plain version."""
    if f"v{version_in_file}" in head_tags or not commit:
        return version_in_file
    return f"{version_in_file}+g{commit}"


def git_output(*args, cwd=None):
    try:
        done = subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd)
    except OSError:
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def current_display_version(root=ROOT):
    head_tags = git_output("tag", "--points-at", "HEAD", cwd=root).split()
    return display_version(read_version(root), head_tags, git_output("rev-parse", "--short=9", "HEAD", cwd=root))


def write_build_info(root=ROOT):
    version = current_display_version(root)
    text = ('"""Generated by scripts/release.py write-build-info for a build; not tracked in git."""\n'
            f'display_version = "{version}"\n')
    (Path(root) / "src/klotse/_build_info.py").write_text(text, encoding="utf-8", newline="")
    return version


def check_tag(tag, version_in_file):
    if not tag.startswith("v") or tag[1:] != version_in_file:
        raise ReleaseError(f"the tag {tag} does not match the version {version_in_file} in {VERSION_FILE}; a release is "
                           f"made with a release PR, then tagged on its merge commit")
    parse(version_in_file)


def main(argv):
    command, args = (argv[1], argv[2:]) if len(argv) > 1 else ("", [])
    try:
        if command == "prepare" and len(args) == 1:
            check_new_version(args[0], tags())
            write_version(args[0])
            print(args[0])
        elif command == "check-pr" and len(args) >= 2:
            version = check_pr(args[0], args[1], args[2:], read_version(), tags())
            print(f"release PR for {version} is valid" if version else "not a release PR; the version is untouched")
        elif command == "tag-for-commit" and len(args) == 1:
            version = tag_for_commit(args[0], read_version(), tags())
            if version:
                print(version)
        elif command == "check-tag" and len(args) == 1:
            check_tag(args[0], read_version())
            print(f"{args[0]} matches {VERSION_FILE}")
        elif command == "latest" and not args:
            print(latest(tags()) or "")
        elif command == "display-version" and not args:
            print(current_display_version())
        elif command == "write-build-info" and not args:
            print(write_build_info())
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except ReleaseError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
