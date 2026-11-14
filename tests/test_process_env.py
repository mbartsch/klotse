"""kubectl, helm and terminal processes get the app's proxy as environment variables; with none set the environment is unchanged."""
import os

from klotse.k8s import process_env


def teardown_function(_):
    process_env.set_app_proxy(None)


def test_no_app_proxy_leaves_the_environment_unchanged():
    process_env.set_app_proxy(None)
    assert process_env.proxy_variables() == {}
    assert process_env.environment() == dict(os.environ)


def test_the_app_proxy_and_bypass_list_are_added_for_child_processes():
    process_env.set_app_proxy("http://proxy.corp:3128", ["localhost", "10.0.0.0/8"])
    variables = process_env.proxy_variables()
    assert variables["HTTPS_PROXY"] == variables["https_proxy"] == "http://proxy.corp:3128"
    assert variables["NO_PROXY"] == "localhost,10.0.0.0/8"
    env = process_env.environment()
    assert env["HTTP_PROXY"] == "http://proxy.corp:3128" and "PATH" in env


def test_a_socks5h_proxy_reaches_go_processes_as_socks5(monkeypatch):
    process_env.set_app_proxy("socks5h://proxy.corp:1080")
    assert process_env.proxy_variables()["HTTPS_PROXY"] == "socks5://proxy.corp:1080"
