"""Clusters from a cloud account, added to a kubeconfig file (no Qt, no provider logic).

A cloud plugin lists its clusters and writes each one's credentials (see klotse.plugins.api, ClusterSource). Core runs the CLI
commands the plugin asks for, with argv lists and no shell, into a temporary file that Klotse creates. The merge then copies
the new entries into the target file: the generated `klotse-cloud.klotse`, or the main kubeconfig when the user chose that in
Preferences. The main kubeconfig is never a CLI target.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from ..plugins.api import CloudError  # re-exported: plugins raise it, core shows it

GENERATED_FILE = "klotse-cloud.klotse"  # the file Klotse writes when the main kubeconfig is not chosen
CLI_TIMEOUT_S = 120


def run(argv, env=None, timeout=CLI_TIMEOUT_S):
    """Run one provider CLI and return its standard output. Errors are CloudError with a short reason, never the full output."""
    extra = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # no console window
    environ = dict(os.environ, **(env or {}))
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, env=environ, **extra)  # nosec B603 - argv list from this module, no shell
    except FileNotFoundError:
        raise CloudError(f"the {argv[0]} command is not installed or not on the PATH") from None
    except subprocess.TimeoutExpired:
        raise CloudError(f"{argv[0]} did not answer within {timeout} seconds") from None
    except OSError as error:
        raise CloudError(f"could not run {argv[0]} ({type(error).__name__})") from None
    if done.returncode != 0:
        raise CloudError(f"{argv[0]} failed: {failure_reason(done.stderr or '', done.returncode)}")
    return done.stdout


def failure_reason(stderr, returncode):
    """A short reason from a failed CLI's stderr. OCI ends its error with a JSON block, so the last line is only "}": the
    "code" and "message" inside that block are used when present."""
    fields = re.findall(r'^\s*"(code|message)":\s*"([^"]*)"', stderr, re.MULTILINE)
    if fields:
        return ": ".join(value for _, value in fields[:2])[:240]
    lines = stderr.strip().splitlines()
    return lines[-1][:240] if lines else f"exit status {returncode}"


def default_generated_path(home=None):
    """~/.kube/klotse-cloud.klotse: where clusters go unless the main kubeconfig was chosen."""
    return Path(home or Path.home()) / ".kube" / GENERATED_FILE


def main_kubeconfig_path(environ=None, home=None):
    """The main kubeconfig: the first KUBECONFIG entry, else ~/.kube/config."""
    environ = os.environ if environ is None else environ
    first = next((p for p in environ.get("KUBECONFIG", "").split(os.pathsep) if p), "")
    return Path(first) if first else Path(home or Path.home()) / ".kube" / "config"


def _load(path):
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise CloudError(f"{path} is not a readable kubeconfig") from None
    if data is not None and not isinstance(data, dict):
        raise CloudError(f"{path} is not a kubeconfig")
    return data or {}


def merge_into(target, source, backup=True):
    """Copy the clusters, contexts and users of the kubeconfig `source` into `target` (created when missing).

    An entry whose name the target already has is not copied (reported in `skipped`). When the target exists it is first copied
    to `<target>.bak`. The file is written through a temporary file and replaced in one step. Returns (added contexts, skipped)."""
    target, source = Path(target), Path(source)
    new = _load(source) or {}
    current = _load(target)
    existing = current is not None
    if current is None:
        current = {}
    merged = {"apiVersion": current.get("apiVersion") or new.get("apiVersion") or "v1",
              "kind": current.get("kind") or "Config", "preferences": current.get("preferences") or {}}
    added, skipped = [], []
    for section in ("clusters", "users", "contexts"):
        have = [item for item in (current.get(section) or []) if isinstance(item, dict)]
        names = {item.get("name") for item in have}
        for item in (new.get(section) or []):
            if not isinstance(item, dict) or not item.get("name"):
                continue
            if item["name"] in names:
                if section == "contexts":
                    skipped.append(item["name"])
                continue
            have.append(item)
            names.add(item["name"])
            if section == "contexts":
                added.append(item["name"])
        merged[section] = have
    merged["current-context"] = current.get("current-context") or (added[0] if added else "")
    target.parent.mkdir(parents=True, exist_ok=True)
    content = yaml.safe_dump(merged, sort_keys=False, allow_unicode=True).encode("utf-8")
    if backup and existing:
        _private_replace(Path(f"{target}.bak"), target.read_bytes())
    _private_replace(target, content)
    return added, skipped


def remove_contexts(target, names, backup=True):
    """Remove the contexts `names` from the kubeconfig `target` (#815), with the clusters and users that only those contexts
    used; entries another context still names stay. A removed current-context moves to the first remaining context (or "").
    Nothing is revoked anywhere: only the file changes. The file is first copied to `<target>.bak`, then written privately
    in one step, as merge_into does. Returns the removed context names (empty, and the file untouched, when none is there)."""
    target = Path(target)
    current = _load(target)
    if current is None:
        return []
    wanted = set(names)
    contexts = current.get("contexts") or []
    removed = [item for item in contexts if isinstance(item, dict) and item.get("name") in wanted]
    if not removed:
        return []
    kept = [item for item in contexts if item not in removed]

    def named(items, key):
        return {item["context"].get(key) for item in items if isinstance(item, dict) and isinstance(item.get("context"), dict)}

    for section, key in (("clusters", "cluster"), ("users", "user")):
        unused = named(removed, key) - named(kept, key)
        if section in current:
            current[section] = [item for item in (current[section] or [])
                                if not (isinstance(item, dict) and item.get("name") in unused)]
    current["contexts"] = kept
    if current.get("current-context") in wanted:
        current["current-context"] = next((item.get("name") for item in kept if isinstance(item, dict) and item.get("name")), "")
    content = yaml.safe_dump(current, sort_keys=False, allow_unicode=True).encode("utf-8")
    if backup:
        _private_replace(Path(f"{target}.bak"), target.read_bytes())
    _private_replace(target, content)
    return [item["name"] for item in removed]


def _private_replace(target, content):
    """Write credentials through an exclusive private file, then atomically replace the destination.

    mkstemp applies POSIX mode 0600 before the first byte, independently of the user's umask. Backups use the same path so
    an old broadly readable backup or a symlink is replaced rather than opened. Windows retains its usual directory ACLs."""
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        try:
            handle = os.fdopen(descriptor, "wb")
        except BaseException:
            os.close(descriptor)
            raise
        with handle:
            handle.write(content)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def scratch_dir():
    """A new private folder for the CLI's temporary kubeconfig; the caller removes it."""
    return Path(tempfile.mkdtemp(prefix="klotse-cloud-"))
