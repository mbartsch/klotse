"""Bounded reads and sensitive-data boundaries; all credentials and processes are synthetic."""
import json
import os
import subprocess
import sys
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from kubernetes.client.exceptions import ApiException

from klotse.k8s import diagnostics, process_env
from klotse.k8s.client import KubeService, ReadOnlyError
from klotse.ui import terminal, terminal_session

pytestmark = pytest.mark.security


@pytest.mark.parametrize("failure", [False, True])
def test_diagnostics_transport_is_bounded_before_allocating_and_always_closed(failure):
    asked = []

    class Response:
        closed = released = False

        def read(self, amount=None):
            assert amount == 17  # an unbounded read fails before any payload is allocated
            if failure:
                raise OSError("synthetic interrupted response")
            return b"x" * amount

        def close(self):
            self.closed = True

        def release_conn(self):
            self.released = True

    response = Response()
    service = object.__new__(KubeService)
    service._core = SimpleNamespace(read_namespaced_pod_log=lambda *args, **kwargs: (asked.append((args, kwargs)) or response))
    if failure:
        with pytest.raises(OSError, match="interrupted"):
            service.read_pod_log("ns", "pod", "container", limit_bytes=16)
    else:
        text = service.read_pod_log("ns", "pod", "container", limit_bytes=16)
        assert text.startswith("x" * 16) and "server exceeded" in text
    assert response.closed and response.released
    assert asked[0][1]["limit_bytes"] == 16 and asked[0][1]["_preload_content"] is False


def test_bounded_log_decodes_a_partial_utf8_character_without_expanding_the_bound():
    class Response:
        def read(self, amount):
            assert amount == 3
            return b"a\xc3\xa9"

        def close(self):
            pass

        def release_conn(self):
            pass

    service = object.__new__(KubeService)
    service._core = SimpleNamespace(read_namespaced_pod_log=lambda *args, **kwargs: Response())
    assert service.read_pod_log("ns", "pod", "c", limit_bytes=2).startswith("a\ufffd")


def test_uid_conflict_never_retries_a_generic_delete_by_name():
    calls = []

    def delete(*args, **kwargs):
        calls.append(kwargs)
        raise ApiException(status=409, reason="synthetic identity conflict")

    service = object.__new__(KubeService)
    service.read_only = False
    service._core = SimpleNamespace(delete_node=delete)
    with pytest.raises(ApiException) as error:
        service.delete_node("node", "selected-uid")
    assert error.value.status == 409 and len(calls) == 1
    assert calls[0]["body"].preconditions.uid == "selected-uid"
    service.read_only = True
    with pytest.raises(ReadOnlyError):
        service.delete_node("node", "selected-uid")
    assert len(calls) == 1


def test_cluster_process_variables_do_not_escape_to_parent_or_other_clusters(tmp_path, monkeypatch, caplog):
    sentinel = "KLOTSE-SYNTHETIC-PROCESS-SENTINEL"
    monkeypatch.delenv("KLOTSE_SCOPED_SECRET", raising=False)
    program = tmp_path / "capture.py"
    program.write_text("import json, os, sys\nprint(json.dumps([sys.argv[1:], os.getenv('KLOTSE_SCOPED_SECRET')]))\n", encoding="utf-8")
    arguments = ["", "two words", "quote'\"", "$(echo synthetic)", "unicode-\u00e9"]
    command = [sys.executable, str(program), *arguments]
    first = subprocess.run(command, env=process_env.environment({"KLOTSE_SCOPED_SECRET": sentinel}),
                           capture_output=True, text=True, check=True, timeout=5)
    second = subprocess.run(command, env=process_env.environment(), capture_output=True, text=True, check=True, timeout=5)
    assert json.loads(first.stdout) == [arguments, sentinel]
    assert json.loads(second.stdout) == [arguments, None]
    assert "KLOTSE_SCOPED_SECRET" not in os.environ and sentinel not in repr(command) + caplog.text


def test_diagnostics_preserves_its_documented_exclusions_instead_of_promising_universal_redaction():
    secret = "KLOTSE-SYNTHETIC-EXPORT-SENTINEL"
    manifest = {"metadata": {"labels": {"example": secret}}, "spec": {"containers": [
        {"name": "app", "args": [secret], "env": [{"name": "PASSWORD", "value": secret}]}]}}
    cleaned = diagnostics.clean(manifest, "pods")
    assert cleaned["spec"]["containers"][0]["env"][0]["value"] == diagnostics.REDACTED
    assert cleaned["spec"]["containers"][0]["args"] == [secret]
    assert cleaned["metadata"]["labels"]["example"] == secret


@pytest.mark.skipif(os.name != "posix", reason="POSIX session-file modes and shell lifecycle")
def test_private_session_script_cleans_up_after_a_failing_child(tmp_path, monkeypatch):
    secret = "KLOTSE-SYNTHETIC-SESSION-SENTINEL"
    monkeypatch.setattr(terminal.tempfile, "tempdir", str(tmp_path))
    program = tmp_path / "child.py"
    capture = tmp_path / "captured.json"
    program.write_text("import json, os, sys\nfrom pathlib import Path\n"
                       "Path(sys.argv[1]).write_text(json.dumps(os.getenv('KLOTSE_SCOPED_SECRET')))\nsys.exit(7)\n", encoding="utf-8")
    script = Path(terminal._external_session_script([sys.executable, str(program), str(capture)],
                                                    {"KLOTSE_SCOPED_SECRET": secret}))
    assert stat.S_IMODE(script.stat().st_mode) == 0o600
    command = ["/bin/sh", str(script)]
    result = subprocess.run(command, input="\n", capture_output=True, text=True, timeout=5)
    assert result.returncode == 7 and not script.exists()
    assert json.loads(capture.read_text()) == secret
    assert secret not in result.stdout + result.stderr + repr(command)


def test_windows_session_command_file_is_removed_even_when_start_fails(tmp_path, monkeypatch):
    command = tmp_path / "command.json"
    command.write_text(json.dumps(["synthetic-not-a-real-command"]), encoding="utf-8")

    def fail(*args, **kwargs):
        assert not command.exists()  # removed before executing anything
        raise OSError("synthetic launch failure")

    monkeypatch.setattr(terminal_session.subprocess, "call", fail)
    monkeypatch.setattr("builtins.input", lambda: "")
    assert terminal_session.run_windows_session(command) == 127
    assert not command.exists()
