import pytest
from PySide6.QtCore import Qt

from klotse.k8s.models import PodRow
from klotse.ui import theme
from klotse.ui.status_tone import status_tone
from klotse.ui.table_model import TableModel


@pytest.mark.parametrize("text, tone", [
    ("Running", "ok"), ("Completed", "ok"), ("Active", "ok"), ("Bound", "ok"), ("Ready", "ok"),
    ("CrashLoopBackOff", "bad"), ("ImagePullBackOff", "bad"), ("Error", "bad"), ("Failed", "bad"),
    ("NotReady", "bad"), ("OOMKilled", "bad"), ("Unknown", "bad"),
    ("Pending", "warn"), ("ContainerCreating", "warn"), ("Terminating", "warn"), ("PodInitializing", "warn"),
    ("Ready,SchedulingDisabled", "warn"),  # never shown as healthy
    ("Init:CrashLoopBackOff", "bad"), ("Init:0/1", "warn"),
    ("", None), ("Something else", None),
])
def test_status_words_get_a_tone(text, tone):
    assert status_tone(text) == tone


def pod(status):
    return PodRow("p", "default", "1/1", status, 0, "n1", "1d", {"kind": "Pod"})


def test_only_status_columns_are_coloured_and_follow_the_active_theme(qtbot):
    model = TableModel([("Name", "name"), ("Status", "status")])
    model.rows = [pod("CrashLoopBackOff"), pod("Running"), pod("")]
    theme.use(theme.HARBOUR)
    try:
        assert model.data(model.index(0, 0), Qt.ForegroundRole) is None  # the name is never coloured
        bad = model.data(model.index(0, 1), Qt.ForegroundRole).color().name()
        ok = model.data(model.index(1, 1), Qt.ForegroundRole).color().name()
        assert (bad, ok) == (theme.HARBOUR.bad, theme.HARBOUR.ok)
        assert model.data(model.index(2, 1), Qt.ForegroundRole) is None  # nothing known: default text colour
        theme.use(theme.DAYLIGHT)
        assert model.data(model.index(0, 1), Qt.ForegroundRole).color().name() == theme.DAYLIGHT.bad  # live
        assert model.data(model.index(1, 1), Qt.DisplayRole) == "Running"  # the text itself is unchanged
    finally:
        theme.use(theme.HARBOUR)


def test_every_built_in_theme_keeps_status_colours_readable():
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    def ratio(a, b):
        hi, lo = sorted((lum(a), lum(b)), reverse=True)
        return (hi + 0.05) / (lo + 0.05)

    for t in theme.BUILTIN:
        for color in (t.ok, t.bad, t.warn):
            assert ratio(color, t.base) >= 4.5, (t.name, color)
