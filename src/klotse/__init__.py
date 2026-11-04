from ._version import __version__  # noqa: F401  (the single, tracked source of the version; see _version.py)

try:  # written by the build scripts (scripts/release.py write-build-info): the version plus +g<commit> for a build that is not a tagged release
    from ._build_info import display_version as __display_version__  # noqa: F401
except ImportError:
    __display_version__ = __version__
