"""The Prometheus / VictoriaMetrics source the user chose for a cluster, kept in the settings file (`[metrics_sources]`, numbered `ctx.N`, `ref.N`:
the text as typed, `namespace/service:port[/path prefix]`, or a URL `https://host[:port][/path prefix]`). For a port-forward: `via.N` = `forward`,
`local.N` the local port asked for, and `tls.N` / `insecure.N` = `on` / `off` for a per-cluster override (absent = the Preferences default).
A URL source can carry an authentication: `auth.N` = `bearer` or `basic`, `authenv.N` the NAME of the environment variable that holds the token
or password (the secret itself is never saved), `authuser.N` the user name for basic. No entry means: look for one automatically.
The default for all clusters is in `[metrics_default]` with the same keys. `ForwardDefaults` holds the Preferences values (`[metrics_forward]`).
Nothing here talks to a cluster."""
import re

from PySide6.QtCore import QSettings

from ..k8s.prometheus import AUTH_KINDS
from .settings_store import default_store

METRICS_SERVER = "metrics-server"  # a source value meaning: no Prometheus for this cluster; metrics-server only


def _on_off(value):
    """"on" / "off" for a saved override; "" (use the default) for anything else."""
    text = str(value or "").strip().lower()
    return text if text in ("on", "off") else ""


def _is_url(text):
    return bool(re.match(r"\s*https?://", str(text or ""), re.I))


def plain_http_with_auth(text, kind):
    """True when a URL source is plain http:// and carries authentication: the token or password would travel unencrypted."""
    return bool(kind) and str(text or "").strip().lower().startswith("http://")


def _auth(value, env, user):
    """(auth, env, user) as saved: the kind must be one of AUTH_KINDS (""= none); a name only when there is a kind."""
    kind = str(value or "").strip().lower()
    if kind not in AUTH_KINDS or not kind:
        return "", "", ""
    return kind, str(env or "").strip(), str(user or "").strip()


class ForwardDefaults:
    """Preferences > Usage history through a port-forward: use SSL (https) and ignore certificate errors. Off by default."""

    def __init__(self, settings=None):
        self.store = settings if settings is not None else default_store()

    def _flag(self, key):
        return str(self.store.value(key, "false")).lower() in ("true", "1")

    @property
    def tls(self):
        return self._flag("metrics_forward/tls")

    @property
    def insecure(self):
        return self._flag("metrics_forward/insecure")

    def set(self, tls, insecure):
        self.store.setValue("metrics_forward/tls", bool(tls))
        self.store.setValue("metrics_forward/insecure", bool(insecure))
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save preferences to {self.store.fileName()}")


class MetricsSources:
    GROUP = "metrics_sources"
    DEFAULT_GROUP = "metrics_default"  # Preferences > Metrics: one default for every cluster without its own entry

    def __init__(self, settings=None):
        self.store = settings if settings is not None else default_store()

    # --- the default for all clusters ---------------------------------------------------------------------------------

    def get_default(self):
        """(ref text, via, local) of the default for all clusters; ("", "proxy", 0) means look for one automatically."""
        ref = str(self.store.value(f"{self.DEFAULT_GROUP}/ref", "") or "").strip()
        via = "forward" if str(self.store.value(f"{self.DEFAULT_GROUP}/via", "")) == "forward" else "proxy"
        local = str(self.store.value(f"{self.DEFAULT_GROUP}/local", "0"))
        return ref, via, int(local) if local.isdigit() and int(local) < 65536 else 0

    def get_default_auth(self):
        """(auth, env, user) of the default URL source; ("", "", "") when it has none."""
        return _auth(self.store.value(f"{self.DEFAULT_GROUP}/auth", ""), self.store.value(f"{self.DEFAULT_GROUP}/authenv", ""),
                     self.store.value(f"{self.DEFAULT_GROUP}/authuser", ""))

    def get_default_ack(self):
        """True when the default's plain-http authentication was acknowledged in Preferences."""
        return str(self.store.value(f"{self.DEFAULT_GROUP}/ackhttp", "")) == "on"

    def set_default(self, text, via="proxy", local=0, auth="", auth_env="", auth_user="", ack_http=False):
        """Save the default for all clusters; "" means look for one automatically. A URL is always reached directly (not a forward).
        Raises OSError when it cannot be written."""
        text = str(text or "").strip()
        self.store.remove(self.DEFAULT_GROUP)
        if text:
            self.store.setValue(f"{self.DEFAULT_GROUP}/ref", text)
            if via == "forward" and not _is_url(text):
                self.store.setValue(f"{self.DEFAULT_GROUP}/via", "forward")
                self.store.setValue(f"{self.DEFAULT_GROUP}/local", int(local or 0))
            kind, env, user = _auth(auth, auth_env, auth_user) if _is_url(text) else ("", "", "")
            if kind:
                self.store.setValue(f"{self.DEFAULT_GROUP}/auth", kind)
                self.store.setValue(f"{self.DEFAULT_GROUP}/authenv", env)
                if ack_http and plain_http_with_auth(text, kind):
                    self.store.setValue(f"{self.DEFAULT_GROUP}/ackhttp", "on")
                if user:
                    self.store.setValue(f"{self.DEFAULT_GROUP}/authuser", user)
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save the setting to {self.store.fileName()}")

    # --- each cluster's own entry -------------------------------------------------------------------------------------

    def entries(self):
        """{context: {ref, via, local, tls, insecure, auth, auth_env, auth_user}} for every cluster with its own entry."""
        return self._entries()

    def own(self, context):
        """The entry saved for `context` itself, or None (the default does not count)."""
        return self._entries().get(context)

    def own_ref(self, context):
        entry = self.own(context)
        return entry["ref"] if entry else ""

    def stale(self, live_contexts):
        """The contexts with a saved entry that are not in `live_contexts` (the kubeconfig's list)."""
        live = set(live_contexts)
        return sorted(name for name in self._entries() if name not in live)

    def _entries(self):
        """{context: {ref, via, local, tls, insecure, auth, auth_env, auth_user}}; via is "proxy" or "forward"; tls and insecure are
        "on", "off" or "" (the default)."""
        self.store.beginGroup(self.GROUP)
        try:
            numbers = sorted(int(k.split(".", 1)[1]) for k in self.store.childKeys() if k.startswith("ctx.") and k.split(".", 1)[1].isdigit())
            found = {}
            for n in numbers:
                name = self.store.value(f"ctx.{n}")
                if not name:
                    continue
                local = str(self.store.value(f"local.{n}", "0"))
                auth, env, user = _auth(self.store.value(f"auth.{n}", ""), self.store.value(f"authenv.{n}", ""),
                                        self.store.value(f"authuser.{n}", ""))
                found[str(name)] = {
                    "ref": str(self.store.value(f"ref.{n}", "")),
                    "via": "forward" if str(self.store.value(f"via.{n}", "")) == "forward" else "proxy",
                    "local": int(local) if local.isdigit() and int(local) < 65536 else 0,
                    "tls": _on_off(self.store.value(f"tls.{n}", "")),
                    "insecure": _on_off(self.store.value(f"insecure.{n}", "")),
                    "auth": auth, "auth_env": env, "auth_user": user,
                    "ack_http": str(self.store.value(f"ackhttp.{n}", "")) == "on",
                }
            return found
        finally:
            self.store.endGroup()

    def _get(self, context, field, default):
        return self._entries().get(context, {}).get(field, default)

    def get(self, context):
        """The source text for `context`: its own entry, else the default for all clusters, else "" (look for one)."""
        own = self._get(context, "ref", "")
        return own or self.get_default()[0]

    def get_via(self, context):
        """"forward" when `context`'s service is reached through a port-forward, else "proxy" (own entry first, then the default)."""
        if self._get(context, "ref", ""):
            return self._get(context, "via", "proxy")
        return self.get_default()[1]

    def get_local(self, context):
        """The local port asked for the forward (0 = a free one)."""
        if self._get(context, "ref", ""):
            return self._get(context, "local", 0)
        return self.get_default()[2]

    def get_auth(self, context):
        """(auth, env, user) for `context`'s URL source: its own entry, else the default's. Empty when there is no authentication."""
        if self._get(context, "ref", ""):
            return (self._get(context, "auth", ""), self._get(context, "auth_env", ""), self._get(context, "auth_user", ""))
        return self.get_default_auth()

    def get_tls(self, context):
        """"on", "off" or "" (use the Preferences setting) for SSL through the forward."""
        return self._get(context, "tls", "")

    def get_insecure(self, context):
        """"on", "off" or "" (use the Preferences setting) for ignoring certificate errors through the forward."""
        return self._get(context, "insecure", "")

    def set(self, context, text, via="proxy", local=0, tls="", insecure="", auth="", auth_env="", auth_user="", ack_http=False):
        """Keep `text` for `context`; "" forgets it. A URL is always reached directly. Raises OSError when the settings file cannot be written."""
        entries = self._entries()
        text = str(text).strip()
        if text:
            url = _is_url(text)
            kind, env, user = _auth(auth, auth_env, auth_user) if url else ("", "", "")
            entries[context] = {"ref": text, "via": "forward" if via == "forward" and not url else "proxy", "local": int(local or 0),
                                "tls": _on_off(tls), "insecure": _on_off(insecure), "auth": kind, "auth_env": env, "auth_user": user,
                                "ack_http": bool(ack_http) and plain_http_with_auth(text, kind)}
        else:
            entries.pop(context, None)
        self.store.remove(self.GROUP)
        for number, (name, entry) in enumerate(entries.items()):
            self.store.setValue(f"{self.GROUP}/ctx.{number}", name)
            self.store.setValue(f"{self.GROUP}/ref.{number}", entry["ref"])
            if entry["via"] == "forward":
                self.store.setValue(f"{self.GROUP}/via.{number}", "forward")
                self.store.setValue(f"{self.GROUP}/local.{number}", entry["local"])
                if entry["tls"]:
                    self.store.setValue(f"{self.GROUP}/tls.{number}", entry["tls"])
                if entry["insecure"]:
                    self.store.setValue(f"{self.GROUP}/insecure.{number}", entry["insecure"])
            if entry["auth"]:
                self.store.setValue(f"{self.GROUP}/auth.{number}", entry["auth"])
                self.store.setValue(f"{self.GROUP}/authenv.{number}", entry["auth_env"])
                if entry["auth_user"]:
                    self.store.setValue(f"{self.GROUP}/authuser.{number}", entry["auth_user"])
                if entry["ack_http"]:
                    self.store.setValue(f"{self.GROUP}/ackhttp.{number}", "on")
        self.store.sync()
        if self.store.status() != QSettings.NoError:
            raise OSError(f"Could not save the setting to {self.store.fileName()}")
