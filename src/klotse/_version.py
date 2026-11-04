"""The version of Klotse: the only place it is written.

It changes in one kind of pull request only, a release PR ("Release v0.2.0", see scripts/release.py and AGENTS.md), and
the git tag of that release is made on the commit that merges it, so a tagged commit always contains its own version.
Between releases this is the version of the last release.
"""
__version__ = "0.1.0"
