"""Application-menu entry for pip / pipx installs on Linux."""
import sys
from pathlib import Path

import pytest

from klotse import cli, desktop_entry


@pytest.fixture
def logo(tmp_path):
    path = tmp_path / "logo.png"
    path.write_bytes(b"\x89PNG fake image bytes")
    return path


def test_install_writes_the_desktop_file_and_the_icon_under_the_data_dir(tmp_path, logo):
    data = tmp_path / "share"
    written = desktop_entry.install("linux", data, ["/home/me/.local/bin/klotse"], logo, refresh=False)
    desktop, icon = data / "applications" / "klotse.desktop", data / "icons" / "hicolor" / "512x512" / "apps" / "klotse.png"
    assert set(written) == {desktop, icon}
    assert icon.read_bytes() == logo.read_bytes()
    lines = desktop.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "[Desktop Entry]"
    for expected in ("Type=Application", "Name=Klotse", "Exec=/home/me/.local/bin/klotse", "Icon=klotse",
                     "Terminal=false", "Categories=Development;System;"):
        assert expected in lines


def test_install_is_repeatable_and_uninstall_removes_exactly_what_was_written(tmp_path, logo):
    data = tmp_path / "share"
    desktop_entry.install("linux", data, ["klotse"], logo, refresh=False)
    desktop_entry.install("linux", data, ["/new/path/klotse"], logo, refresh=False)  # a reinstall updates the command
    assert "Exec=/new/path/klotse" in (data / "applications" / "klotse.desktop").read_text()
    (data / "applications" / "other.desktop").write_text("keep me")
    removed = desktop_entry.uninstall(data, refresh=False)
    assert len(removed) == 2 and not (data / "applications" / "klotse.desktop").exists()
    assert (data / "applications" / "other.desktop").read_text() == "keep me"  # nothing else is touched
    assert desktop_entry.uninstall(data, refresh=False) == []  # nothing left to remove


def test_a_missing_or_non_png_logo_still_gives_a_working_entry(tmp_path):
    data = tmp_path / "share"
    jpeg = tmp_path / "logo.jpg"
    jpeg.write_bytes(b"jpeg")
    written = desktop_entry.install("linux", data, ["klotse"], jpeg, refresh=False)
    assert written == [data / "applications" / "klotse.desktop"]  # no icon copied, the launcher is still written
    assert not (data / "icons").exists()


def test_refuses_to_run_off_linux(tmp_path, logo):
    data = tmp_path / "share"
    for platform in ("win32", "darwin"):
        with pytest.raises(RuntimeError, match="Linux"):
            desktop_entry.install(platform, data, ["klotse"], logo, refresh=False)
    assert not data.exists()  # nothing was written


def test_exec_quoting_follows_the_desktop_entry_rules():
    quote = desktop_entry._quote
    assert quote("/home/me/.local/bin/klotse") == "/home/me/.local/bin/klotse"  # plain paths stay unquoted
    assert quote("/home/my name/bin/klotse") == '"/home/my name/bin/klotse"'  # spaces need quotes
    assert quote("100%") == "100%%"  # a literal % is doubled (it starts field codes like %f)
    assert quote('a"b') == '"a\\\\"b"' and quote("a$b") == '"a\\\\$b"'
    assert quote("a\\b") == '"a\\\\\\\\b"'  # a backslash inside quotes is written as four
    text = desktop_entry.desktop_entry_text(["/opt/my tools/python", "-m", "klotse"])
    assert 'Exec="/opt/my tools/python" -m klotse' in text.splitlines()


def test_exec_command_prefers_the_installed_script_and_falls_back_to_the_module():
    assert desktop_entry.exec_command(which=lambda name: "/usr/local/bin/klotse") == ["/usr/local/bin/klotse"]
    assert desktop_entry.exec_command(which=lambda name: None) == [sys.executable, "-m", "klotse"]


def test_data_home_honours_xdg_data_home(tmp_path):
    assert desktop_entry.data_home({"XDG_DATA_HOME": str(tmp_path)}) == tmp_path
    assert desktop_entry.data_home({}) == Path.home() / ".local" / "share"  # the XDG default
    assert desktop_entry.data_home({"XDG_DATA_HOME": ""}) == Path.home() / ".local" / "share"  # empty counts as unset


def test_refresh_failures_never_break_the_install(tmp_path, logo, monkeypatch):
    monkeypatch.setattr(desktop_entry.shutil, "which", lambda name: "/usr/bin/update-desktop-database")

    def broken(*args, **kwargs):
        raise OSError("no such tool")

    monkeypatch.setattr(desktop_entry.subprocess, "run", broken)
    assert desktop_entry.install("linux", tmp_path, ["klotse"], logo, refresh=True)  # still returns the written paths


def test_command_line_flags_install_and_remove(tmp_path, logo, monkeypatch, capsys):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(desktop_entry, "exec_command", lambda which=None: ["/x/klotse"])
    monkeypatch.setattr(desktop_entry, "_refresh", lambda *a, **k: None)
    assert cli.handle(["--install-desktop-entry"]) == 0
    out = capsys.readouterr().out
    assert "Installed:" in out and "klotse.desktop" in out
    assert (tmp_path / "applications" / "klotse.desktop").is_file()
    assert cli.handle(["--uninstall-desktop-entry"]) == 0
    assert "Removed:" in capsys.readouterr().out and not (tmp_path / "applications" / "klotse.desktop").exists()
    assert cli.handle(["--uninstall-desktop-entry"]) == 0 and "Nothing to remove." in capsys.readouterr().out


def test_command_line_flag_fails_cleanly_off_linux(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "platform", "win32")
    assert cli.handle(["--install-desktop-entry"]) == 1
    assert "only needed (and only works) on Linux" in capsys.readouterr().err
    assert not any(tmp_path.iterdir())
