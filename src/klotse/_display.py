"""What the app reports as its version when it runs from a git checkout (no build file written by the build scripts).

The same rule as scripts/release.py `display_version`: the plain version when HEAD carries the tag v<version>, otherwise
<version>+g<commit>. So a run from master shows the commit, and a tagged release shows the plain number. Outside a git
checkout (a pip install, a packaged app) it is the plain version. Pure apart from the git calls."""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]  # src/klotse/_display.py -> the repository root


def display_version_for(version, root=REPO_ROOT):
    if not (Path(root) / ".git").exists():
        return version

    def git(*args):
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0  # pythonw has no console to hand git
        try:
            done = subprocess.run(["git", *args], capture_output=True, text=True, cwd=root, timeout=5,  # nosec B603 B607 - fixed git arguments
                                  creationflags=flags)
        except (OSError, subprocess.SubprocessError):
            return ""
        return done.stdout.strip() if done.returncode == 0 else ""

    tags = git("tag", "--points-at", "HEAD").split()
    commit = git("rev-parse", "--short=9", "HEAD")
    if f"v{version}" in tags or not commit:
        return version
    return f"{version}+g{commit}"
