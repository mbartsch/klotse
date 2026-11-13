"""The environment for the kubectl, helm and terminal processes Klotse starts, carrying the app's proxy (Preferences > Network).

kubectl and helm read HTTPS_PROXY / HTTP_PROXY / NO_PROXY from their environment, so the app's proxy is passed to each process
that way: nothing is written to the kubeconfig or to disk, and the app's own environment is not changed. A kubeconfig proxy-url
is not replaced here. Call set_app_proxy when the setting changes; with no proxy set, every environment is the unchanged one."""
import os

_PROXY = {"url": None, "no_proxy": ""}


def set_app_proxy(url, no_proxy_items=()):
    _PROXY["url"] = url or None
    _PROXY["no_proxy"] = ",".join(str(item) for item in no_proxy_items if str(item).strip())


def proxy_variables():
    """The proxy variables to add to a process's environment, {} when no app proxy is set."""
    url = _PROXY["url"]
    if not url:
        return {}
    variables = {"HTTPS_PROXY": url, "https_proxy": url, "HTTP_PROXY": url, "http_proxy": url}
    if _PROXY["no_proxy"]:
        variables.update(NO_PROXY=_PROXY["no_proxy"], no_proxy=_PROXY["no_proxy"])
    return variables


def environment():
    """A full environment for subprocess calls: the app's own, plus the proxy variables when one is set."""
    env = dict(os.environ)
    env.update(proxy_variables())
    return env


def qt_environment():
    """The same environment as a QProcessEnvironment, for QProcess (imported here so the rest of this module needs no Qt)."""
    from PySide6.QtCore import QProcessEnvironment
    env = QProcessEnvironment.systemEnvironment()
    for key, value in proxy_variables().items():
        env.insert(key, value)
    return env
