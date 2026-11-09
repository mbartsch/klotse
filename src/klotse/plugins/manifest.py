"""Read plugin declarations without importing executable plugin modules."""
import json
from dataclasses import fields
from pathlib import Path

from .api import API_VERSION, Action, Column, OverviewCard, Plugin, Setting, valid_id

MANIFEST_VERSION = 1
GROUPS = {"columns": Column, "actions": Action, "overview_cards": OverviewCard,
          "settings": Setting, "cluster_settings": Setting}
METADATA = ("id", "name", "description", "version", "requires_api")


def declarations(plugin):
    """The static contract a module must match before it can be activated."""
    result = {key: getattr(plugin, key) for key in METADATA}
    for group in GROUPS:
        result[group] = [{field.name: getattr(item, field.name) for field in fields(item) if field.name != "format"}
                         for item in getattr(plugin, group)]
    return result


def read_manifest(path):
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"required plugin manifest is missing: {path}")
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("plugin manifest exceeds 1 MiB")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("plugin manifest must be a JSON object")
    if type(data.get("manifest_version")) is not int or data["manifest_version"] != MANIFEST_VERSION:
        raise ValueError(f"unsupported manifest_version (expected {MANIFEST_VERSION})")
    if set(data) - {"manifest_version", *METADATA, *GROUPS}:
        raise ValueError("unknown plugin manifest fields")
    if not valid_id(data.get("id")):
        raise ValueError("invalid plugin id in manifest")
    if type(data.get("requires_api")) is not int or not 1 <= data["requires_api"] <= API_VERSION:
        raise ValueError(f"manifest requires an unsupported plugin API (this Klotse provides {API_VERSION})")
    plugin = Plugin()
    for key in METADATA:
        value = data.get(key, getattr(plugin, key))
        if key != "requires_api" and not isinstance(value, str):
            raise ValueError(f"manifest {key} must be text")
        setattr(plugin, key, value)
    for group, cls in GROUPS.items():
        raw = data.get(group, [])
        if not isinstance(raw, list):
            raise ValueError(f"manifest {group} must be a list")
        items = []
        allowed = {f.name for f in fields(cls)} - {"format"}
        for entry in raw:
            if not isinstance(entry, dict) or set(entry) - allowed:
                raise ValueError(f"invalid declaration in manifest {group}")
            entry = dict(entry)
            if "kinds" in entry:
                if not isinstance(entry["kinds"], list) or not entry["kinds"] or not all(isinstance(k, str) and k for k in entry["kinds"]):
                    raise ValueError("manifest kinds must be a non-empty list of resource kinds")
                entry["kinds"] = tuple(entry["kinds"])
            item = cls(**entry)
            for key in ("key", "title", "label", "help", "pattern", "kind"):
                if hasattr(item, key) and not isinstance(getattr(item, key), str):
                    raise ValueError(f"manifest declaration {key} must be text")
            for key in ("multi", "dangerous"):
                if hasattr(item, key) and type(getattr(item, key)) is not bool:
                    raise ValueError(f"manifest {key} must be boolean")
            if hasattr(item, "confirm") and item.confirm is not None and not isinstance(item.confirm, str):
                raise ValueError("manifest confirm must be text or null")
            if isinstance(item, Setting):
                if item.kind not in ("text", "password", "multiline", "bool", "note"):
                    raise ValueError("unknown manifest setting kind")
                if (item.kind == "bool" and type(item.default) is not bool) or (item.kind != "bool" and not isinstance(item.default, str)):
                    raise ValueError("manifest setting default must match its kind")
            items.append(item)
        keys = [item.key for item in items]
        if not all(keys) or len(keys) != len(set(keys)):
            raise ValueError(f"manifest {group} keys must be non-empty and unique")
        setattr(plugin, group, tuple(items))
    return plugin
