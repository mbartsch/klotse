"""The application's HTTP(S) proxy for cluster connections, kept in the shared settings (pure: no Qt widgets).

Used only for a cluster whose kubeconfig sets no `proxy-url` of its own; the kubeconfig value always wins. A proxy address
must be http:// or https:// with a host name, and must not carry a user name or password (settings are plain text).
`network/no_proxy` is a list of host names or addresses that bypass the proxy."""
from urllib.parse import urlsplit


class ProxySettings:
    def __init__(self, store):
        self.store = store
        self.url = str(store.value("network/proxy", "") or "").strip() or None
        self.no_proxy = _as_list(store.value("network/no_proxy", []))

    def set(self, url, no_proxy):
        """Save the proxy address and the bypass list; raises ValueError for an address that cannot be used."""
        url = str(url or "").strip()
        bypass = [item.strip() for item in str(no_proxy or "").replace(";", ",").split(",") if item.strip()]
        if url:
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.hostname:
                raise ValueError("The proxy must be an http:// or https:// address with a host name, for example http://proxy.corp:3128.")
            if parts.username or parts.password or "@" in parts.netloc:
                raise ValueError("Leave the user name and password out of the proxy address.")
            self.store.setValue("network/proxy", url)
            self.store.setValue("network/no_proxy", bypass)
        else:
            self.store.remove("network/proxy")
            self.store.remove("network/no_proxy")
        self.store.sync()
        self.url = url or None
        self.no_proxy = bypass if url else []

    def no_proxy_text(self):
        return ", ".join(self.no_proxy)


def _as_list(value):
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    text = str(value or "").strip()
    return [text] if text else []
