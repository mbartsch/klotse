"""The core of Add clusters from the cloud: kubeconfig paths, merging a cluster's entry into the target file, and the runner.
The cloud-specific listing and credentials are tested with each plugin (tests/test_cluster_sources.py)."""
import json
import os
from pathlib import Path

import pytest
import yaml

from klotse.k8s import cloud_clusters as cloud
from klotse.plugins.api import CloudError

pytestmark = pytest.mark.security


def kubeconfig(path, names, current=""):
    data = {"apiVersion": "v1", "kind": "Config", "current-context": current, "preferences": {},
            "clusters": [{"name": n, "cluster": {"server": f"https://{n}.example.test"}} for n in names],
            "users": [{"name": f"user-{n}", "user": {"exec": {"command": "helper"}}} for n in names],
            "contexts": [{"name": n, "context": {"cluster": n, "user": f"user-{n}"}} for n in names]}
    Path(path).write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return Path(path)


def test_default_paths_are_in_dot_kube_and_the_main_file_follows_kubeconfig(tmp_path):
    assert cloud.default_generated_path(tmp_path) == tmp_path / ".kube" / "klotse-cloud.klotse"
    two = os.pathsep.join([str(tmp_path / "a"), str(tmp_path / "b")])
    assert cloud.main_kubeconfig_path({"KUBECONFIG": two}) == tmp_path / "a"
    assert cloud.main_kubeconfig_path({}, tmp_path) == tmp_path / ".kube" / "config"


def test_merge_creates_the_target_and_adds_the_new_entries(tmp_path):
    source = kubeconfig(tmp_path / "piece.yaml", ["demo-prod"])
    target = tmp_path / ".kube" / "klotse-cloud.klotse"
    added, skipped = cloud.merge_into(target, source)
    assert added == ["demo-prod"] and skipped == []
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert [c["name"] for c in data["contexts"]] == ["demo-prod"] and data["current-context"] == "demo-prod"


def test_merge_keeps_existing_entries_and_backs_the_file_up(tmp_path):
    target = kubeconfig(tmp_path / "klotse-cloud.klotse", ["demo-old"], current="demo-old")
    source = kubeconfig(tmp_path / "piece.yaml", ["demo-old", "demo-new"])
    added, skipped = cloud.merge_into(target, source)
    assert added == ["demo-new"] and skipped == ["demo-old"]
    assert (tmp_path / "klotse-cloud.klotse.bak").exists()
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert [c["name"] for c in data["contexts"]] == ["demo-old", "demo-new"]
    assert data["current-context"] == "demo-old"  # an existing current context is not replaced


def test_merge_without_backup_writes_no_copy(tmp_path):
    target = kubeconfig(tmp_path / "config", ["demo-a"])
    cloud.merge_into(target, kubeconfig(tmp_path / "piece.yaml", ["demo-b"]), backup=False)
    assert not (tmp_path / "config.bak").exists()


def test_a_file_that_is_not_a_kubeconfig_is_refused_and_left_alone(tmp_path):
    target = tmp_path / "config"
    target.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(cloud.CloudError):
        cloud.merge_into(target, kubeconfig(tmp_path / "piece.yaml", ["demo-a"]))
    assert target.read_text(encoding="utf-8") == "- just\n- a list\n"


def test_a_missing_cli_is_a_clear_error_not_a_traceback():
    with pytest.raises(cloud.CloudError, match="not installed or not on the PATH"):
        cloud.run(["klotse-no-such-cli-for-tests", "list"])


def test_an_oci_error_block_gives_its_code_and_message_not_the_closing_brace():
    stderr = ('ServiceError:\n{\n    "client_version": "3.95.1",\n    "code": "NotAuthorizedOrNotFound",\n'
              '    "message": "Authorization failed or requested resource not found: compartment ocid1.c",\n    "status": 404\n}\n')
    assert cloud.failure_reason(stderr, 1) == ("NotAuthorizedOrNotFound: Authorization failed or requested resource not found: "
                                               "compartment ocid1.c")
    assert cloud.failure_reason("plain failure\nlast line\n", 2) == "last line"
    assert cloud.failure_reason("", 3) == "exit status 3"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions; Windows ACLs are a separate boundary")
def test_credentials_and_backup_are_private_before_first_write_under_permissive_umask(tmp_path, monkeypatch):
    import stat

    target = kubeconfig(tmp_path / "config", ["old"])
    source = kubeconfig(tmp_path / "piece.yaml", ["new"])
    target.chmod(0o644)
    backup = Path(f"{target}.bak")
    backup.write_text("old backup", encoding="utf-8")
    backup.chmod(0o644)
    original = cloud.os.fdopen
    before_write = []

    def checked(descriptor, *args, **kwargs):
        info = os.fstat(descriptor)
        before_write.append((stat.S_IMODE(info.st_mode), info.st_size))
        return original(descriptor, *args, **kwargs)

    monkeypatch.setattr(cloud.os, "fdopen", checked)
    previous = os.umask(0o022)
    try:
        cloud.merge_into(target, source)
    finally:
        os.umask(previous)
    assert before_write == [(0o600, 0), (0o600, 0)]
    assert stat.S_IMODE(target.stat().st_mode) == stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert yaml.safe_load(backup.read_text(encoding="utf-8"))["contexts"][0]["name"] == "old"


@pytest.mark.skipif(os.name != "posix", reason="symlink creation requires additional privileges on Windows")
def test_predictable_temporary_and_backup_symlinks_do_not_clobber_other_files(tmp_path):
    target = kubeconfig(tmp_path / "config", ["old"])
    source = kubeconfig(tmp_path / "piece.yaml", ["new"])
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("leave me alone", encoding="utf-8")
    predictable = Path(f"{target}.tmp")
    predictable.symlink_to(sentinel)
    backup = Path(f"{target}.bak")
    backup.symlink_to(sentinel)
    cloud.merge_into(target, source)
    assert sentinel.read_text(encoding="utf-8") == "leave me alone"
    assert predictable.is_symlink() and not backup.is_symlink()
    assert yaml.safe_load(backup.read_text(encoding="utf-8"))["contexts"][0]["name"] == "old"


@pytest.mark.parametrize("failure", ["write", "replace", "backup_replace"])
def test_failed_private_write_preserves_target_and_removes_temporary_files(tmp_path, monkeypatch, failure):
    target = kubeconfig(tmp_path / "config", ["old"])
    source = kubeconfig(tmp_path / "piece.yaml", ["new"])
    original_bytes = target.read_bytes()
    original_open, original_replace = cloud.os.fdopen, cloud.os.replace

    class BrokenWrite:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def write(self, content):
            self.handle.write(content[:10])
            raise OSError("synthetic disk failure")

    def replace(src, dest):
        if Path(dest) == (Path(f"{target}.bak") if failure == "backup_replace" else target):
            raise OSError("synthetic replace failure")
        return original_replace(src, dest)

    if failure == "write":
        monkeypatch.setattr(cloud.os, "fdopen", lambda *args, **kwargs: BrokenWrite(original_open(*args, **kwargs)))
    else:
        monkeypatch.setattr(cloud.os, "replace", replace)
    with pytest.raises(OSError, match="synthetic"):
        cloud.merge_into(target, source, backup=failure == "backup_replace")
    assert target.read_bytes() == original_bytes
    assert not list(tmp_path.glob(".*.tmp"))


def test_failed_descriptor_wrap_closes_descriptor_and_removes_private_file(tmp_path, monkeypatch):
    descriptors = []

    def fail(descriptor, *args, **kwargs):
        descriptors.append(descriptor)
        raise OSError("synthetic descriptor failure")

    monkeypatch.setattr(cloud.os, "fdopen", fail)
    with pytest.raises(OSError, match="descriptor failure"):
        cloud._private_replace(tmp_path / "config", b"synthetic credentials")
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
    assert not list(tmp_path.iterdir())
