"""What a kubeconfig says about one context, as plain lines for a read-only view (pure, no cluster call).

Shows the names, the server, the proxy, how the certificate authority is given, and which kind of sign-in the user entry uses.
Secret material is never returned: token values, client keys and certificate data are reported as present or absent only, and
a file path is shown as a path. Anything missing or unreadable is reported as such rather than guessed."""
import os

import yaml


def _load(paths):
    contexts, clusters, users = {}, {}, {}
    for path in paths:
        try:
            with open(path, encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict):
            continue
        for key, table in (("contexts", contexts), ("clusters", clusters), ("users", users)):
            for item in data.get(key) or []:
                if isinstance(item, dict) and item.get("name") not in table:
                    table[item.get("name")] = item.get(key[:-1]) or {}
    return contexts, clusters, users


def _paths(kubeconfig):
    if kubeconfig:
        return [kubeconfig]
    env = [p for p in os.environ.get("KUBECONFIG", "").split(os.pathsep) if p]
    return env or [os.path.join(os.path.expanduser("~"), ".kube", "config")]


def _authentication(user):
    if not isinstance(user, dict) or not user:
        return "none in the kubeconfig"
    lines = []
    helper = user.get("exec")
    if isinstance(helper, dict):
        args = " ".join(str(a) for a in helper.get("args") or [])
        lines.append(f"exec helper: {helper.get('command') or '(no command)'}" + (f" {args}" if args else ""))
    if user.get("token") or user.get("tokenFile"):
        lines.append("bearer token: present (not shown)")
    if user.get("client-certificate-data") or user.get("client-key-data"):
        lines.append("client certificate: embedded (not shown)")
    elif user.get("client-certificate") or user.get("client-key"):
        lines.append(f"client certificate file: {user.get('client-certificate') or user.get('client-key')}")
    provider = user.get("auth-provider")
    if isinstance(provider, dict):
        lines.append(f"auth provider: {provider.get('name') or '(unnamed)'}")
    if user.get("username") or user.get("password"):
        lines.append("basic auth: username and password present (not shown)")
    return "; ".join(lines) if lines else "none in the kubeconfig"


def _certificate_authority(cluster):
    if cluster.get("insecure-skip-tls-verify"):
        return "not checked (insecure-skip-tls-verify is set)"
    if cluster.get("certificate-authority-data"):
        return "embedded (not shown)"
    if cluster.get("certificate-authority"):
        return f"file: {cluster.get('certificate-authority')}"
    return "system roots (none in the kubeconfig)"


def entry_lines(kubeconfig, context):
    """Lines describing `context` from the kubeconfig file(s) (the explicit path, else KUBECONFIG, else ~/.kube/config)."""
    contexts, clusters, users = _load(_paths(kubeconfig))
    ctx = contexts.get(context)
    if not isinstance(ctx, dict):
        return [f"Context {context}: not found in the kubeconfig."]
    cluster = clusters.get(ctx.get("cluster")) or {}
    user = users.get(ctx.get("user")) or {}
    return [
        f"Context: {context}",
        f"Cluster entry: {ctx.get('cluster') or '(none)'}",
        f"User entry: {ctx.get('user') or '(none)'}",
        f"Namespace: {ctx.get('namespace') or 'default (none in the kubeconfig)'}",
        f"Server: {cluster.get('server') or '(none in the kubeconfig)'}",
        f"Proxy: {cluster.get('proxy-url') or 'none'}",
        f"Certificate authority: {_certificate_authority(cluster)}",
        f"Sign-in: {_authentication(user)}",
    ]
