"""Build dialog footers with Qt's platform-specific button ordering and spacing."""
from PySide6.QtWidgets import QDialogButtonBox


def action_box(parent, *actions):
    """Return a native-order button box from `(button, role)` pairs.

    The buttons keep their existing text, callbacks, and default status. Use `AcceptRole` for the primary
    action, `ActionRole` for secondary work, `DestructiveRole` for irreversible local operations, and
    `RejectRole` for Cancel or Close.
    """
    box = QDialogButtonBox(parent)
    for button, role in actions:
        box.addButton(button, role)
    return box
