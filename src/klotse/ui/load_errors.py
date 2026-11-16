"""Plain wording for a list that could not be loaded (pure, no Qt)."""
import re

_STATUS = re.compile(r"\((\d{3})\)")
_BODY_MESSAGE = re.compile(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"')


def explain(message, what):
    """`message` is the text a worker reported (`ApiException: (403) Reason: Forbidden ...`); `what` names the list ("Pods").
    A refusal by RBAC or by authentication is explained as such, with the server's own sentence; anything else is returned
    unchanged."""
    text = str(message)
    found = _STATUS.search(text)
    code = found.group(1) if found else ""
    if code not in ("401", "403") and "Forbidden" not in text.split("\n", 1)[0]:
        return text
    said = _BODY_MESSAGE.search(text)
    server = said.group(1).replace('\\"', '"') if said else ""
    if code == "401":
        head = f"Not signed in to the cluster: {what} could not be listed (401)."
        hint = "Check the credentials of this context in your kubeconfig, then press Refresh."
    else:
        head = f"Refused: you may not list {what} on this cluster (403 Forbidden). This is not an empty list."
        hint = "Your account lacks permission (RBAC). View > My permissions… shows what you can do in this namespace."
    return "\n".join(part for part in (head, f"Server: {server}" if server else "", hint) if part)


_REASON = re.compile(r"Reason:\s*(.+)")
_PREFIX = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Problem|Refused|Timeout))\s*:\s*", re.S)
_LOCAL = {"ValueError", "RuntimeError", "HelmError", "ReadOnlyError", "ApplyProblem", "FilterError", "OSError",
          "FileNotFoundError", "PermissionError", "NotADirectoryError", "IsADirectoryError"}
_UNREACHABLE = ("MaxRetryError", "ConnectTimeout", "ReadTimeout", "NewConnectionError", "ConnectionError", "ConnectionRefused",
                "ConnectionReset", "ProtocolError", "SSLError", "getaddrinfo failed", "Name or service not known",
                "timed out", "TimeoutError")
_HINTS = {
    "400": "The server did not accept the request as it was sent.",
    "401": "Not signed in to the cluster. Check the credentials of this context in your kubeconfig, then try again.",
    "403": "Your account lacks permission for this (RBAC). View > My permissions… shows what you can do.",
    "404": "It is no longer there. Refresh the list.",
    "409": "It changed on the server or already exists. Refresh, then try again.",
    "410": "The server no longer has that version. Refresh the list.",
    "422": "The server found the object invalid; its sentence above says which field.",
    "429": "The server is limiting requests. Wait a moment, then try again.",
    "500": "The API server reported a problem of its own. Try again shortly.",
    "503": "The API server is not ready. Try again shortly.",
    "504": "The API server did not answer in time. Try again shortly.",
}
KUBERNETES, UNREACHABLE, LOCAL, UNEXPECTED = "Kubernetes error", "Cannot reach the cluster", "Cannot do that", "Unexpected error"


def describe(message):
    """(title, text) for an error a worker or a local check reported. An `ApiException` becomes the status, the server's own
    sentence and a hint, without the headers and the JSON body; a connection problem says so; a local refusal loses its exception
    name. The caller keeps the raw text where it can be copied (Logs & activity > Activity)."""
    raw = str(message).strip()
    first = raw.split("\n", 1)[0]
    if first.startswith("ApiException") or (_STATUS.search(first) and "Reason:" in raw):
        found = _STATUS.search(raw)
        code = found.group(1) if found else ""
        reason = _REASON.search(raw)
        said = _BODY_MESSAGE.search(raw)
        server = said.group(1).replace('\\"', '"').replace("\n", " ") if said else ""
        head = "The cluster refused this" + (f" ({code}{' ' + reason.group(1).strip() if reason else ''})" if code else "") + "."
        return KUBERNETES, "\n".join(part for part in (head, f"Server: {server}" if server else "", _HINTS.get(code, "")) if part)
    prefix = _PREFIX.match(raw)
    name = prefix.group(1).rsplit(".", 1)[-1] if prefix else ""
    if name in _LOCAL:  # before the connection words: "helm did not finish ... timed out" is a local message
        return LOCAL, raw[prefix.end():].strip() or raw
    if any(word in raw for word in _UNREACHABLE):
        detail = _PREFIX.sub("", first)
        return UNREACHABLE, ("The API server did not answer. Check the network, the VPN and that the cluster is up, then try again."
                             + (f"\n{detail[:300]}" if detail else ""))
    if prefix is None:
        return LOCAL, raw  # a plain sentence from a local check
    return UNEXPECTED, raw


def brief(message):
    """One line for lists and log panes: the first two lines of `describe`, joined."""
    return " ".join(describe(message)[1].splitlines()[:2])
