import shutil
from pathlib import Path

from PySide6.QtCore import QSettings

APP = "Klotse"
LEGACY_APP = "Kubeman"  # the application's name before it was renamed; its settings are copied once


def _open(name):
    return QSettings(QSettings.IniFormat, QSettings.UserScope, name, name)


def default_store():
    """The user's settings file. On the first start after the rename, the old Kubeman file is copied across.

    The old file is left in place, so nothing is lost if the user goes back to an older version.
    """
    store = _open(APP)
    target = Path(store.fileName())
    if not target.exists():
        legacy = Path(_open(LEGACY_APP).fileName())
        if legacy.is_file():
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy, target)
                store = _open(APP)  # reopen so the copied values are read
            except OSError:
                pass  # start with defaults rather than failing to launch
    return store
