"""The app-wide proxy: validated when saved, kept in the shared settings, and used only when the kubeconfig sets no proxy-url."""
from types import SimpleNamespace

import pytest
from kubernetes import client as kube_client
from kubernetes import config as kube_config
from PySide6.QtCore import QSettings

from klotse.k8s.client import KubeService
from klotse.k8s.proxy_settings import ProxySettings

CONFIG = ("contexts:\n- name: plain\n  context: {cluster: c1, user: u}\n"
          "- name: viaproxy\n  context: {cluster: c2, user: u}\n"
          "clusters:\n- name: c1\n  cluster: {server: 'https://a.test'}\n"
          "- name: c2\n  cluster: {server: 'https://b.test', proxy-url: 'http://kube-proxy.test:8080'}\n"
          "users:\n- name: u\n  user: {token: x}\n")


def store(tmp_path):
    return QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)


def test_a_saved_proxy_and_bypass_list_come_back(tmp_path):
    settings = ProxySettings(store(tmp_path))
    settings.set("http://proxy.corp:3128", "localhost, 10.0.0.0/8 ,.internal.example")
    again = ProxySettings(store(tmp_path))
    assert again.url == "http://proxy.corp:3128"
    assert again.no_proxy == ["localhost", "10.0.0.0/8", ".internal.example"]


def test_clearing_removes_the_proxy(tmp_path):
    settings = ProxySettings(store(tmp_path))
    settings.set("https://proxy.corp:443", "localhost")
    settings.set("", "")
    again = ProxySettings(store(tmp_path))
    assert again.url is None and again.no_proxy == []


@pytest.mark.parametrize("url", ["proxy.corp:3128", "socks5://proxy.corp:1080", "http://", "http://user:secret@proxy.corp:3128"])
def test_an_unusable_proxy_address_is_refused_and_nothing_is_saved(tmp_path, url):
    settings = ProxySettings(store(tmp_path))
    with pytest.raises(ValueError):
        settings.set(url, "")
    assert ProxySettings(store(tmp_path)).url is None


def test_the_app_proxy_is_used_only_when_the_kubeconfig_names_no_proxy(tmp_path):
    kube = tmp_path / "config"
    kube.write_text(CONFIG, encoding="utf-8")
    service = KubeService(str(kube))
    service.proxy, service.no_proxy = "http://app-proxy.corp:3128", ["localhost", "10.0.0.0/8"]

    plain = kube_client.Configuration()
    kube_config.load_kube_config(config_file=str(kube), context="plain", client_configuration=plain)
    service.apply_proxy(plain, "plain")
    assert plain.proxy == "http://app-proxy.corp:3128" and plain.no_proxy == "localhost,10.0.0.0/8"

    proxied = kube_client.Configuration()
    kube_config.load_kube_config(config_file=str(kube), context="viaproxy", client_configuration=proxied)
    service.apply_proxy(proxied, "viaproxy")
    assert proxied.proxy == "http://kube-proxy.test:8080"


def test_without_an_app_proxy_nothing_changes(tmp_path):
    kube = tmp_path / "config"
    kube.write_text(CONFIG, encoding="utf-8")
    service = KubeService(str(kube))
    cfg = SimpleNamespace(proxy="env-proxy", no_proxy="env-bypass")
    service.apply_proxy(cfg, "plain")
    assert cfg.proxy == "env-proxy" and cfg.no_proxy == "env-bypass"
