"""The app proxy reaches the kubectl processes Klotse starts: as environment for Qt and subprocess calls, and as a prefix on POSIX terminals."""
import sys

from klotse.k8s import process_env
from klotse.ui import terminal


def teardown_function(_):
    process_env.set_app_proxy(None)


def test_a_posix_terminal_gets_the_proxy_in_front_of_the_command(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    process_env.set_app_proxy("http://proxy.corp:3128", ["localhost"])
    argv = terminal._with_proxy(["kubectl", "exec", "pod"])
    assert argv[:2] == ["env", "HTTPS_PROXY=http://proxy.corp:3128"]
    assert "NO_PROXY=localhost" in argv and argv[-3:] == ["kubectl", "exec", "pod"]


def test_windows_terminals_keep_the_argv_and_get_the_environment_instead(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    process_env.set_app_proxy("http://proxy.corp:3128")
    assert terminal._with_proxy(["kubectl", "get", "pods"]) == ["kubectl", "get", "pods"]


def test_with_no_app_proxy_the_terminal_command_is_unchanged(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    process_env.set_app_proxy(None)
    assert terminal._with_proxy(["kubectl", "get", "pods"]) == ["kubectl", "get", "pods"]


def test_qt_processes_get_the_proxy_in_their_environment():
    from PySide6.QtCore import QProcessEnvironment
    process_env.set_app_proxy("http://proxy.corp:3128", ["localhost"])
    env = process_env.qt_environment()
    assert isinstance(env, QProcessEnvironment)
    assert env.value("HTTPS_PROXY") == "http://proxy.corp:3128" and env.value("NO_PROXY") == "localhost"


def test_subprocess_calls_get_the_proxy_too():
    process_env.set_app_proxy("http://proxy.corp:3128")
    assert process_env.environment()["https_proxy"] == "http://proxy.corp:3128"
