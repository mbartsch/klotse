# The version comes from the git tag (see [tool.setuptools_scm] in pyproject.toml). _version.py is generated when the
# package, a packaged app or `python -m setuptools_scm --force-write-version-files` is built; it is not committed.
try:
    from ._version import version as __version__
except ImportError:  # a plain source checkout where nothing generated it: ask the installed metadata, if any
    try:
        from importlib.metadata import version as _installed_version
        __version__ = _installed_version("klotse")
    except Exception:
        __version__ = "0+unknown"
