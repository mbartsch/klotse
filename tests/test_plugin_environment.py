"""Plugins add variables (the AWS profile or keys, extra NAME=value lines) to the kubectl, helm and terminal processes of a cluster."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings

from klotse.k8s import process_env, remote_files
from klotse.plugins import API_VERSION, Plugin, Setting, parse_environment
from klotse.plugins.manager import Loaded, PluginManager
from klotse.ui import terminal
from klotse.ui.command_view import CommandView
from klotse.ui.file_copy import FileCopies
from klotse.ui.port_forward import PortForwards
from klotse.ui.terminal_settings import TerminalSettings
from klotse.ui.workspace import ClusterWorkspace
from klotse_plugins.aws import AwsPlugin
from klotse_plugins.azure import AzurePlugin
from klotse_plugins.gcp import GcpPlugin
from klotse_plugins.oci import OciPlugin

SECRET = "not-a-real-secret-value"  # invented: no real key is used anywhere in these tests


def manager_with(tmp_path, *plugins):
    manager = PluginManager(QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat), folder=None, bundled=(), entry_points=False)
    manager.loaded = [Loaded(p.id, "bundled", p.id, p) for p in plugins]
    for plugin in plugins:
        manager.set_enabled(plugin.id, True)
    return manager


# ---- the NAME=value setting ------------------------------------------------------------------------------------
def test_lines_of_name_value_become_variables():
    text = "AWS_REGION = eu-west-1\n\n# a comment\nNO_PROXY=a,b=c\nEMPTY=\n"
    assert parse_environment(text) == {"AWS_REGION": "eu-west-1", "NO_PROXY": "a,b=c", "EMPTY": ""}
    assert parse_environment("") == {} and parse_environment(None) == {}


@pytest.mark.parametrize("text", ["AWS_REGION", "1BAD=x", "A B=x", "=x", "GOOD=1\nBAD-NAME=" + SECRET])
def test_a_line_that_is_not_name_value_is_refused_without_repeating_it(text):
    with pytest.raises(ValueError) as caught:
        parse_environment(text)
    assert "NAME=value" in str(caught.value) and SECRET not in str(caught.value)


# ---- the AWS plugin --------------------------------------------------------------------------------------------
def aws(**settings):
    values = {s.key: s.default for s in AwsPlugin.settings}
    values.update({s.key: "" for s in AwsPlugin.cluster_settings})
    values.update(settings)
    return AwsPlugin().process_environment("prod", values)


def test_the_api_grew_and_passing_is_on_unless_turned_off():
    assert API_VERSION == 14 and AwsPlugin.requires_api == 14
    assert next(s for s in AwsPlugin.settings if s.key == "pass_environment").default is True
    assert aws() == {}  # nothing filled in: nothing is set
    assert aws(aws_profile="work") == {"AWS_PROFILE": "work"}
    assert aws(aws_profile="work", pass_environment=False) == {}


def test_the_profile_of_the_cluster_wins_over_the_default_one():
    assert aws(aws_profile="work", profile_map="prod = prod-admin\nother = x") == {"AWS_PROFILE": "prod-admin"}
    assert aws(aws_profile="work", profile_map="prod = prod-admin", cluster_profile="mine") == {"AWS_PROFILE": "mine"}


def test_a_typed_key_replaces_the_default_profile_and_an_inherited_session_token():
    found = aws(aws_profile="work", access_key_id="AKIAEXAMPLE", secret_access_key=SECRET)
    assert found == {"AWS_ACCESS_KEY_ID": "AKIAEXAMPLE", "AWS_SECRET_ACCESS_KEY": SECRET, "AWS_SESSION_TOKEN": ""}
    found = aws(access_key_id="ASIAEXAMPLE", secret_access_key=SECRET, session_token="token-text")
    assert found["AWS_SESSION_TOKEN"] == "token-text" and "AWS_PROFILE" not in found
    assert aws(aws_profile="work", access_key_id="AKIAEXAMPLE") == {"AWS_PROFILE": "work"}  # half a key is no key


@pytest.mark.parametrize("chosen", [dict(profile_map="prod = prod-admin"), dict(cluster_profile="prod-admin"),
                                    dict(profile_map="prod = other", cluster_profile="prod-admin")])
def test_a_cluster_with_its_own_profile_never_gets_the_typed_key(chosen):
    found = aws(aws_profile="work", access_key_id="AKIAEXAMPLE", secret_access_key=SECRET, session_token="token-text", **chosen)
    assert found == {"AWS_PROFILE": "prod-admin"}
    other = aws(aws_profile="work", access_key_id="AKIAEXAMPLE", secret_access_key=SECRET, profile_map="elsewhere = x")
    assert other["AWS_ACCESS_KEY_ID"] == "AKIAEXAMPLE" and "AWS_PROFILE" not in other  # no profile of its own: the key


def test_extra_variables_are_added_even_when_passing_is_off_and_can_replace_a_value():
    assert aws(pass_environment=False, extra_environment="AWS_REGION=eu-west-1") == {"AWS_REGION": "eu-west-1"}
    assert aws(aws_profile="work", extra_environment="AWS_PROFILE=other")["AWS_PROFILE"] == "other"


@pytest.mark.parametrize("cls", [GcpPlugin, OciPlugin, AzurePlugin])
def test_the_other_clouds_take_extra_variables_too(cls):
    assert cls.requires_api == 14 and [s.key for s in cls.settings][-1] == "extra_environment"
    assert cls().process_environment("ctx", {"extra_environment": "CLOUD_X=1"}) == {"CLOUD_X": "1"}
    assert cls().process_environment("ctx", {}) == {}


# ---- the manager ------------------------------------------------------------------------------------------------
class Vars(Plugin):
    id = "vars"
    name = "Vars"
    requires_api = 12
    settings = (Setting("extra", "Extra", "", kind="multiline"),)
    cluster_settings = (Setting("who", "Who", ""),)

    def process_environment(self, context, settings):
        return {"CTX": context, "WHO": settings["who"], **parse_environment(settings["extra"])}


class Broken(Plugin):
    id = "broken"
    name = "Broken"
    result = None

    def process_environment(self, context, settings):
        if self.result is None:
            raise RuntimeError("boom")
        return self.result


def test_the_manager_merges_what_enabled_plugins_return_for_the_cluster(qtbot, tmp_path):
    manager = manager_with(tmp_path, Vars(), Plugin.__new__(Plugin))
    manager.loaded[1].id = manager.loaded[1].plugin.id = "plain"  # a plugin without the hook adds nothing
    manager.set_enabled("plain", True)
    manager.set_setting("vars", "extra", "NO_PROXY=a,b\nTWO=2")  # a comma in a value survives the settings file
    manager.set_cluster_settings("vars", "prod", {"who": "me"})
    assert manager.process_environment("prod") == {"CTX": "prod", "WHO": "me", "NO_PROXY": "a,b", "TWO": "2"}
    assert manager.process_environment("dev")["WHO"] == ""
    manager.set_enabled("vars", False)
    assert manager.process_environment("prod") == {}


@pytest.mark.parametrize("result", [None, {"BAD NAME": "x"}, {"OK": 5}, {"OK": "two\nlines " + SECRET}, {1: "x"}])
def test_a_plugin_that_fails_adds_nothing_and_is_reported_without_values(qtbot, tmp_path, result):
    broken = Broken()
    broken.result = result
    manager = manager_with(tmp_path, broken, Vars())
    found = manager.process_environment("prod")
    assert found == {"CTX": "prod", "WHO": ""}  # the other plugin still counts
    assert "Variables for commands and terminals" in manager.errors["broken"] and SECRET not in manager.errors["broken"]
    broken.result = {"FINE": "1"}
    assert manager.process_environment("prod")["FINE"] == "1" and "broken" not in manager.errors  # working again: forgotten


# ---- the processes ----------------------------------------------------------------------------------------------
def test_subprocess_and_qt_environments_carry_the_variables_over_the_inherited_ones(monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "inherited")
    assert process_env.environment()["AWS_PROFILE"] == "inherited"
    env = process_env.environment({"AWS_PROFILE": "work", "AWS_SESSION_TOKEN": ""})
    assert env["AWS_PROFILE"] == "work" and env["AWS_SESSION_TOKEN"] == "" and "PATH" in env
    assert os.environ["AWS_PROFILE"] == "inherited"  # the app's own environment is not changed
    qt = process_env.qt_environment({"AWS_PROFILE": "work"})
    assert qt.value("AWS_PROFILE") == "work" and process_env.qt_environment().value("AWS_PROFILE") == "inherited"


def test_port_forward_file_copy_and_drain_processes_get_them(qtbot, tmp_path):
    asked = []

    def variables(context):
        asked.append(context)
        return {"AWS_PROFILE": f"for-{context}"}

    def base(context, kubeconfig):
        return ["definitely-not-a-program-klotse"]

    forwards = PortForwards(base=base, variables=variables)
    fw = forwards.start("prod", "shop", "Pod", "web", 80)
    assert fw.process.processEnvironment().value("AWS_PROFILE") == "for-prod"
    qtbot.waitUntil(lambda: fw.state == "failed", timeout=8000)
    copies = FileCopies(base=base, variables=variables)
    copy = copies.start("dev", "shop", "web", "", "download", str(tmp_path / "out.txt"), "/etc/hostname")
    assert copy.process.processEnvironment().value("AWS_PROFILE") == "for-dev" and asked == ["prod", "dev"]
    qtbot.waitUntil(lambda: copy.state != "running", timeout=8000)
    assert not PortForwards(base=base).variables  # none given: only the app's environment and proxy
    view = CommandView(["definitely-not-a-program-klotse"], variables={"AWS_PROFILE": "work"})
    qtbot.addWidget(view)
    assert view.process.processEnvironment().value("AWS_PROFILE") == "work"


def test_the_file_listing_runs_kubectl_with_them():
    seen = {}

    def run(argv, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    remote_files.list_directory(["kubectl"], "shop", "web", "", "/", run, {"AWS_PROFILE": "work"})
    assert seen["env"]["AWS_PROFILE"] == "work" and "PATH" in seen["env"]


def test_posix_terminal_scripts_export_them_and_keep_them_off_the_command_line(monkeypatch, tmp_path):
    variables = {"AWS_SECRET_ACCESS_KEY": "it's $HOME `x`", "AWS_PROFILE": "work", "BAD NAME": "x"}
    for make in (lambda: terminal._external_session_script(["kubectl", "exec"], variables),
                 lambda: terminal._mac_session_script(["kubectl", "exec"], "Terminal", variables)):
        path = Path(make())
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        finally:
            path.unlink()
        assert "export AWS_PROFILE=work" in lines
        assert "export AWS_SECRET_ACCESS_KEY='it'\"'\"'s $HOME `x`'" in lines  # quoted: the shell does not expand it
        assert not any("BAD NAME" in line for line in lines)
        command = next(i for i, line in enumerate(lines) if "kubectl" in line)
        assert "AWS_" not in lines[command] and command > lines.index("export AWS_PROFILE=work")
    plain = Path(terminal._external_session_script(["kubectl", "exec"]))
    try:
        assert "export" not in plain.read_text(encoding="utf-8")
    finally:
        plain.unlink()


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows console process gets the environment")
def test_the_windows_console_gets_them_as_its_environment(monkeypatch):
    started = {}

    def popen(argv, **kwargs):
        started.update(kwargs, argv=argv)

    monkeypatch.setattr(terminal.subprocess, "Popen", popen)
    terminal.open_terminal(["kubectl", "exec"], variables={"AWS_PROFILE": "KLOTSE-SYNTHETIC-PROFILE"})
    Path(started["argv"][-1]).unlink()
    assert started["env"]["AWS_PROFILE"] == "KLOTSE-SYNTHETIC-PROFILE"
    assert "KLOTSE-SYNTHETIC-PROFILE" not in " ".join(started["argv"])


# ---- the workspace ----------------------------------------------------------------------------------------------
def make_ws(qtbot, tmp_path, plugins):
    terminal_settings = TerminalSettings(QSettings(str(tmp_path / "t.ini"), QSettings.IniFormat))
    ws = ClusterWorkspace(SimpleNamespace(), "prod", terminal_settings=terminal_settings, plugins=plugins)
    qtbot.addWidget(ws)
    return ws


def test_a_terminal_of_the_cluster_is_opened_with_its_variables(qtbot, tmp_path, monkeypatch):
    manager = manager_with(tmp_path, AwsPlugin())
    manager.set_setting("aws", "profile_map", "prod = prod-admin")
    ws = make_ws(qtbot, tmp_path, manager)
    opened = []
    monkeypatch.setattr("klotse.ui.workspace.open_terminal", lambda *args: opened.append(args))
    assert ws.process_variables() == {"AWS_PROFILE": "prod-admin"}
    assert ws.port_forwards.variables("prod") == ws.file_copies.variables("prod") == {"AWS_PROFILE": "prod-admin"}
    ws._launch_terminal(["kubectl", "exec"], "Opened shell")
    qtbot.waitUntil(lambda: bool(opened), timeout=5000)
    assert opened == [(["kubectl", "exec"], ws.terminal_settings.mac_terminal, {"AWS_PROFILE": "prod-admin"})]
    manager.set_setting("aws", "pass_environment", False)  # turned off: the call is the plain one again
    ws._launch_terminal(["kubectl", "exec"], "Opened shell")
    qtbot.waitUntil(lambda: len(opened) == 2, timeout=5000)
    assert opened[1] == (["kubectl", "exec"], ws.terminal_settings.mac_terminal)
    ws.shutdown()


def test_a_workspace_without_plugins_adds_nothing(qtbot, tmp_path):
    ws = make_ws(qtbot, tmp_path, None)
    assert ws.process_variables() == {} and ws.port_forwards.variables("prod") == {}
    ws.shutdown()


def test_quoted_and_export_lines_are_read_as_typed():
    text = 'export AWS_ENDPOINT_URL="http://localhost:4566"\nAWS_DEFAULT_REGION=\'us-east-1\'\nQ="a=b"\nSINGLE="x\n'
    assert parse_environment(text) == {"AWS_ENDPOINT_URL": "http://localhost:4566", "AWS_DEFAULT_REGION": "us-east-1",
                                       "Q": "a=b", "SINGLE": '"x'}
