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
