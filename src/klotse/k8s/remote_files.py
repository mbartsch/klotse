"""Listing a directory inside a container with `kubectl exec ... ls`, and reading the answer (no Qt).

Nothing is changed in the cluster: it only runs `ls` in an existing container. The path is a separate argument after `--`, never
part of a shell command line, so a path cannot run anything. `ls -1AF` is understood by GNU, BusyBox and BSD ls: one name per line,
hidden files included (but not . and ..), and a suffix marks directories (/), links (@), programs (*), sockets (=) and pipes (|).
A container without `ls` (distroless images) cannot be browsed."""
import posixpath
import subprocess
import sys

LIMIT = 5000  # entries shown at most; a bigger directory says so
TIMEOUT_S = 30
DIR, LINK, FILE = "dir", "link", "file"


class RemoteError(Exception):
    """The listing could not be made; the message is meant for the user."""


def clean_path(path):
    """An absolute, normalised path ('/' for the root); ValueError for an empty path, a relative one or a control character."""
    text = str(path).strip()
    if not text.startswith("/"):
        raise ValueError("Type an absolute path starting with /.")
    if any(ord(ch) < 32 for ch in text):
        raise ValueError("The path has a control character.")
    return posixpath.normpath(text) if text != "/" else "/"


def parent(path):
    return posixpath.dirname(path.rstrip("/")) or "/"


def join(directory, name):
    return posixpath.join(directory, name)


def list_argv(base, namespace, pod, container, path):
    """argv for the listing. `base` is `kubectl_base(context, kubeconfig)`; the path comes last, after `--`."""
    argv = list(base) + ["-n", namespace, "exec", pod]
    if container:
        argv += ["-c", container]
    return argv + ["--", "ls", "-1AF", "--", clean_path(path) + ("/" if clean_path(path) != "/" else "")]


def parse_listing(text):
    """[(name, kind)] from `ls -1AF` output: directories first, then by name; at most LIMIT entries. Returns (entries, cut)."""
    entries = []
    for line in str(text).splitlines():
        name = line.rstrip("\r")
        if not name:
            continue
        kind = FILE
        if name.endswith("/"):
            kind, name = DIR, name[:-1]
        elif name.endswith("@"):
            kind, name = LINK, name[:-1]
        elif name.endswith(("*", "=", "|")):
            name = name[:-1]
        if name:
            entries.append((name, kind))
    entries.sort(key=lambda e: (e[1] == FILE, e[0].lower()))
    return entries[:LIMIT], len(entries) > LIMIT


def explain_failure(stderr):
    """Plain words for the common reasons a listing fails."""
    text = (stderr or "").strip()
    last = text.splitlines()[-1] if text else "kubectl failed"
    lowered = text.lower()
    if "executable file not found" in lowered or "no such file or directory: unknown" in lowered or ('"ls"' in lowered and "not found" in lowered):
        return "This container has no ls command (a minimal image), so it cannot be browsed. Type the path instead."
    if "no such file or directory" in lowered:
        return "No such directory in the container."
    if "not a directory" in lowered:
        return "That is a file, not a directory."
    if "permission denied" in lowered or "forbidden" in lowered:
        return "Permission denied: " + last
    return last


def list_directory(base, namespace, pod, container, path, run=subprocess.run):
    """([(name, kind)], cut) for a directory of a container. RemoteError with a user-ready message on failure."""
    try:
        argv = list_argv(base, namespace, pod, container, path)
    except ValueError as error:
        raise RemoteError(str(error)) from None
    flags = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # no console window flashing up
    try:
        # stdin must be given: the Windows launcher has no console (pythonw), so the inherited standard input is an invalid
        # handle and subprocess.run fails with "WinError 6: The handle is invalid" without it
        done = run(argv, capture_output=True, text=True, timeout=TIMEOUT_S, errors="replace", stdin=subprocess.DEVNULL, **flags)
    except FileNotFoundError:
        raise RemoteError("kubectl was not found. Install kubectl and make sure it is on the PATH.") from None
    except subprocess.TimeoutExpired:
        raise RemoteError(f"The listing did not finish within {TIMEOUT_S} seconds.") from None
    except OSError as error:  # for example "WinError 6" when the process has no usable standard handles
        raise RemoteError(f"Could not run kubectl: {error}") from None
    if done.returncode != 0:
        raise RemoteError(explain_failure(done.stderr or done.stdout))
    return parse_listing(done.stdout)


def claim_users(pods, claim):
    """[(pod name, container name, mount path)] for the running pods of a namespace that mount the PersistentVolumeClaim
    `claim`, from their manifests. A claim no pod uses cannot be browsed without starting a pod, which this does not do."""
    found = []
    for pod in pods:
        spec = pod.get("spec") or {}
        if ((pod.get("status") or {}).get("phase")) != "Running":
            continue
        volumes = {v.get("name") for v in spec.get("volumes") or []
                   if (v.get("persistentVolumeClaim") or {}).get("claimName") == claim}
        if not volumes:
            continue
        for container in spec.get("containers") or []:
            for mount in container.get("volumeMounts") or []:
                if mount.get("name") in volumes and mount.get("mountPath"):
                    found.append(((pod.get("metadata") or {}).get("name", ""), container.get("name", ""), mount["mountPath"]))
    return found
