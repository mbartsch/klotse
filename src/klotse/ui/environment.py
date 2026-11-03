"""Make command-line tools findable when the app is started from the macOS Finder or Dock.

Apps launched that way get a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin) instead of the one from the user's
shell, so `kubectl` (Homebrew) and kubeconfig auth helpers such as `aws`, `gcloud` or `kubelogin` would not be found.
"""
import os
import re
import subprocess
import sys

# Where Homebrew, pip --user, krew and the Google Cloud SDK usually put executables.
MAC_TOOL_DIRS = ("/opt/homebrew/bin", "/opt/homebrew/sbin", "/usr/local/bin", "/usr/local/sbin",
                 "~/.local/bin", "~/bin", "~/.krew/bin", "~/google-cloud-sdk/bin")
_MARKER = "__KLOTSE_PATH__"


def merged_path(current, extra, exists=os.path.isdir):
    """`current` followed by those `extra` directories that exist and are not already in it (order kept, no repeats)."""
    seen, parts = set(), []
    for entry in [*current.split(os.pathsep), *extra]:
        entry = os.path.expanduser(entry)
        if entry and entry not in seen and (entry in current.split(os.pathsep) or exists(entry)):
            seen.add(entry)
            parts.append(entry)
    return os.pathsep.join(parts)


def login_shell_path(shell=None, timeout=3, run=subprocess.run):
    """The PATH an interactive login shell would have (picks up asdf, nix, Cloud SDK ...), or "" if unavailable."""
    shell = shell or os.environ.get("SHELL") or "/bin/zsh"
    try:
        result = run([shell, "-ilc", f'printf "{_MARKER}%s{_MARKER}" "$PATH"'], capture_output=True, text=True,
                     timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""  # a missing shell, a hung rc file or a timeout must never stop the app from starting
    match = re.search(f"{_MARKER}(.*?){_MARKER}", result.stdout or "", re.S)  # rc files may print banners around it
    return match[1].strip() if match else ""


def prepare_environment(platform=None, env=None, interactive=None, shell_path=login_shell_path):
    """Extend PATH on macOS when there is no terminal to inherit it from. Does nothing on other platforms."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    if platform != "darwin":
        return
    if interactive is None:
        interactive = bool(sys.stdin) and sys.stdin.isatty()
    extra = [] if interactive else shell_path().split(os.pathsep)  # started from a terminal: PATH is already right
    env["PATH"] = merged_path(env.get("PATH", ""), [*extra, *MAC_TOOL_DIRS])
