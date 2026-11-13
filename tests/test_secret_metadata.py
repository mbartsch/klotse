"""Secrets are listed from their metadata only; a Secret's data is read when it is opened, and never edited from metadata alone."""
import json
from types import SimpleNamespace

from klotse.k8s.client import KubeService
from klotse.k8s.resources import map_secret, map_secret_metadata
from klotse.ui import workspace as ws_module

ITEM = {"metadata": {"name": "db-pass", "namespace": "payments", "creationTimestamp": "2026-11-01T10:00:00Z",
                     "labels": {"app": "db"}}}


class FakeCore:
    def __init__(self):
        self.calls = []

    def list_namespaced_secret(self, namespace, **kwargs):
        self.calls.append(("list", namespace, kwargs))
        return SimpleNamespace(data=json.dumps({"items": [ITEM], "metadata": {}}).encode())


def test_the_secret_list_asks_for_metadata_only_and_never_full_objects():
    service = KubeService()
    core = FakeCore()
    service._core = core
    rows = service.list_resource("secrets", "payments")
    [(kind, namespace, kwargs)] = core.calls
    assert kind == "list" and namespace == "payments"
    assert "PartialObjectMetadataList" in kwargs["_headers"]["Accept"]
    assert len(rows) == 1 and rows[0].name == "db-pass" and rows[0].namespace == "payments"
    assert rows[0].secret_loaded is False and rows[0].secret_data == {}
    assert "data" not in rows[0].raw and rows[0].type == "--" and rows[0].keys == "--"


def test_a_metadata_row_has_name_namespace_and_age_and_no_data():
    row = map_secret_metadata(ITEM)
    assert (row.name, row.namespace) == ("db-pass", "payments")
    assert row.age and row.raw["metadata"]["labels"] == {"app": "db"}
    assert row.secret_loaded is False


def test_a_full_secret_is_marked_loaded_and_still_masked():
    full = SimpleNamespace(metadata=SimpleNamespace(name="db-pass", namespace="payments", creation_timestamp=None),
                           type="Opaque", data={"password": "c2VjcmV0"}, string_data=None)
    row = map_secret(full)
    assert row.secret_loaded is True and row.secret_data == {"password": "c2VjcmV0"}
    assert row.raw["data"] == {"password": "<hidden>"}


def test_editing_a_metadata_secret_reads_it_first(monkeypatch):
    calls = []

    class Window:
        def _load_secret(self, row, then):
            calls.append("load")
            row.secret_loaded = True
            then(row)

        def _edit_flow(self, *args, **kwargs):
            calls.append("edit")

        edit_resource = ws_module.ClusterWorkspace.edit_resource

    metadata_row = map_secret_metadata(ITEM)
    Window().edit_resource("Secret", metadata_row)
    assert calls == ["load", "edit"]
