"""Plain wording for a connection that failed, with what the kubeconfig says about the sign-in helper and the proxy (pure).

The Kubernetes Python client runs an `exec` credential helper (`aws eks get-token`, `aws-iam-authenticator`, `kubelogin`, `gke-gcloud-auth-plugin`...)
without a terminal, so a helper that needs a browser, an MFA code or an expired SSO session fails with its own message. `explain` puts
that into words and names the command to run in a terminal. Nothing here runs the helper."""
import os
import re

import yaml

SIGN_IN = {  # helper (file name without .exe) -> what usually fixes it
    "aws": "Run `aws sso login` (add `--profile NAME` if the cluster uses a profile), or refresh your AWS credentials, then connect again.",
    "aws-iam-authenticator": "Check your AWS credentials (`aws sts get-caller-identity`); a helper that asks for an MFA code cannot ask here, "
                             "so sign in first in a terminal.",
    "kubelogin": "Run `kubelogin convert-kubeconfig` / `az login` (Azure) or the sign-in your OIDC provider needs, then connect again.",
    "gke-gcloud-auth-plugin": "Run `gcloud auth login` (and `gcloud auth application-default login` if needed), then connect again.",
    "gcloud": "Run `gcloud auth login`, then connect again.",
    "oidc-login": "Run `kubectl oidc-login get-token ...` once in a terminal to sign in; the token is cached for the next connection.",
    "kubectl-oidc_login": "Run `kubectl oidc-login get-token ...` once in a terminal to sign in; the token is cached for the next connection.",
}
_PROCESS = re.compile(r"exec: process returned (\d+)(?:\.\s*(.*))?", re.S)


def _helper_name(command):
    name = os.path.basename(str(command or "")).lower()
    return re.sub(r"\.(exe|cmd|bat)$", "", name)


def auth_info(kubeconfig, context):
    """What the kubeconfig says about `context`: {"server", "exec" (the helper command or ""), "args", "proxy" (the cluster's `proxy-url` or "")}.
    Reads the file(s) again (cheap); returns empty values for anything it cannot find or read. Token values are never returned."""
    info = {"server": "", "exec": "", "args": [], "proxy": ""}
    paths = [kubeconfig] if kubeconfig else [p for p in os.environ.get("KUBECONFIG", "").split(os.pathsep) if p] or \
        [os.path.join(os.path.expanduser("~"), ".kube", "config")]
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
    ctx = contexts.get(context) or {}
    cluster = clusters.get(ctx.get("cluster")) or {}
    user = users.get(ctx.get("user")) or {}
    info["server"] = str(cluster.get("server") or "")
    info["proxy"] = str(cluster.get("proxy-url") or "")
    helper = user.get("exec") if isinstance(user, dict) else None
    if isinstance(helper, dict):
        info["exec"] = str(helper.get("command") or "")
        info["args"] = [str(a) for a in helper.get("args") or []]
    return info


def explain(message, info=None):
    """The text to show for a failed connection. `message` is what the client raised; `info` is `auth_info`. A message that is not about the
    sign-in helper, the proxy or an unreachable server is returned unchanged (with the proxy named when one is in use and the server
    could not be reached)."""
    text = str(message)
    info = info or {}
    helper = info.get("exec") or ""
    name = _helper_name(helper)
    found = _PROCESS.search(text)
    lines = []
    if found or "exec:" in text:
        if found:
            code, said = found.group(1), (found.group(2) or "").strip()
            lines.append(f"The sign-in helper `{helper or 'exec'}` failed (exit code {code}).")
            if said:
                lines.append(f"It said: {said[:500]}")
        else:
            lines.append(f"The sign-in helper `{helper or 'exec'}` gave an answer Klotse cannot use.")
            lines.append(text.strip()[:500])
        lines.append("Klotse runs the helper without a terminal, so it cannot show a browser sign-in, an MFA prompt or ask a question.")
        lines.append(SIGN_IN.get(name, "Sign in with the helper in a terminal first (run the same command your kubeconfig names), then connect again."))
        return "\n".join(lines)
    if isinstance(message, FileNotFoundError) or "No such file or directory" in text or "WinError 2" in text or "cannot find the file" in text.lower():
        if helper:
            return (f"The sign-in helper `{helper}` was not found. Install it or put it on the PATH (a program started from the desktop may "
                    f"have a shorter PATH than your terminal), then connect again.\n{text.strip()[:300]}")
    if "Cannot reach the API server" in text or "MaxRetryError" in text or "ProxyError" in text:
        extra = []
        if info.get("proxy"):
            extra.append(f"This context uses the proxy {info['proxy']} (from `proxy-url` in the kubeconfig); check that it is running and reachable.")
        elif info.get("server"):
            extra.append(f"Server: {info['server']}. Check the network or VPN.")
        return "\n".join([text.strip()] + extra)
    return text


def guard_helper_stdin():
    """Make the client's sign-in helpers run with an empty standard input. Started without a console (pythonw, a packaged app) the
    process has no valid stdin handle and `subprocess.Popen(stdin=None)` fails with `[WinError 6] The handle is invalid`; the
    client passes `stdin=None` when it is not interactive. Idempotent; returns True when the guard is (now) installed."""
    import subprocess
    try:
        from kubernetes.config import exec_provider
    except ImportError:  # pragma: no cover - the client always has it
        return False
    popen = exec_provider.subprocess.Popen
    if getattr(popen, "_klotse_guard", False):
        return True

    def guarded(*args, **kwargs):
        if kwargs.get("stdin") is None:
            kwargs["stdin"] = subprocess.DEVNULL
        return popen(*args, **kwargs)

    guarded._klotse_guard = True
    exec_provider.subprocess = type("_Subprocess", (), {**{k: getattr(subprocess, k) for k in dir(subprocess) if not k.startswith("__")},
                                                        "Popen": staticmethod(guarded)})
    return True
