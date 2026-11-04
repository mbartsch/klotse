"""Application-menu entry for pip / pipx installs on Linux.

`pipx install klotse` puts a `klotse` command on the PATH but cannot register a launcher, so Klotse would not show up
in the application menu. `klotse --install-desktop-entry` writes a .desktop file and the icon under the user's XDG
data directory (no root needed); `klotse --uninstall-desktop-entry` removes them again. Native packages (Windows
installer, macOS app) do not need this. No Qt is imported at module level.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_ID = "klotse"  # the desktop file is klotse.desktop; the app tells Wayland the same name (see __main__)
_SPECIAL = set(' \t\n"\'\\<>~|&;$*?#()`')
ICON_RELATIVE = Path("icons") / "hicolor" / "512x512" / "apps" / f"{APP_ID}.png"
DESKTOP_RELATIVE = Path("applications") / f"{APP_ID}.desktop"


def data_home(env=None):
    env = os.environ if env is None else env
    return Path(env.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def exec_command(which=shutil.which):
    """The command a launcher should run: the installed `klotse` script, else this Python running the module."""
    found = which("klotse")
    return [found] if found else [sys.executable, "-m", "klotse"]


def _quote(argument):
    """One Exec argument as the Desktop Entry spec wants it: quoted when it has special characters, `%` doubled."""
    argument = argument.replace("%", "%%")
    if not (set(argument) & _SPECIAL):
        return argument
    escaped = argument.replace("\\", "\\\\\\\\").replace('"', '\\\\"').replace("$", "\\\\$").replace("`", "\\\\`")
    return f'"{escaped}"'


def desktop_entry_text(command):
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.0",
        "Name=Klotse",
        "GenericName=Kubernetes manager",
        "Comment=A native Kubernetes desktop manager",
        "Exec=" + " ".join(_quote(part) for part in command),
        f"Icon={APP_ID}",
        "Terminal=false",
        "Categories=Development;System;",
        "Keywords=kubernetes;k8s;kubectl;cluster;",
        "StartupNotify=true",
        "",
    ])


def _refresh(applications_dir, run=subprocess.run):
    """Ask the desktop to re-read its launchers, when the tool exists. Never fails the install."""
    tool = shutil.which("update-desktop-database")
    if tool:
        try:
            run([tool, str(applications_dir)], capture_output=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            pass


def install(platform=None, data=None, command=None, logo=None, refresh=True):
    """Write the .desktop file and the icon; returns the paths written. Linux only."""
    platform = platform or sys.platform
    if not platform.startswith("linux"):
        raise RuntimeError("The desktop entry is only needed (and only works) on Linux.")
    data = Path(data) if data else data_home()
    if logo is None:
        from .ui import branding
        logo = branding.logo_path()
    written = []
    if logo is not None and Path(logo).suffix.lower() == ".png":
        icon = data / ICON_RELATIVE
        icon.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(logo, icon)
        written.append(icon)
    desktop = data / DESKTOP_RELATIVE
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(desktop_entry_text(command or exec_command()), encoding="utf-8")
    written.append(desktop)
    if refresh:
        _refresh(desktop.parent)
    return written


def uninstall(data=None, refresh=True):
    """Remove what install() wrote; returns the paths removed (none if nothing was installed)."""
    data = Path(data) if data else data_home()
    removed = []
    for relative in (DESKTOP_RELATIVE, ICON_RELATIVE):
        path = data / relative
        if path.is_file():
            path.unlink()
            removed.append(path)
    if refresh and removed:
        _refresh(data / DESKTOP_RELATIVE.parent)
    return removed
