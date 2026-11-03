"""Readable, round-trip-safe YAML for resource details and editing."""

import yaml


class _ManifestDumper(yaml.SafeDumper):
    pass


def _represent_string(dumper, value):
    # Literal blocks keep embedded config files readable. Let PyYAML choose
    # the chomping indicator so trailing newlines survive a load/save cycle.
    return dumper.represent_scalar(
        "tag:yaml.org,2002:str", value, style="|" if "\n" in value else None
    )


_ManifestDumper.add_representer(str, _represent_string)


def dump_manifest(body):
    return yaml.dump(body, Dumper=_ManifestDumper, sort_keys=False, allow_unicode=True)
