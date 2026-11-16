"""#501: dialogs that opened at a fixed size now go through the screen-bounded sizing helper."""
from klotse.ui import cloud_clusters_dialog, context_dialog
from klotse.ui.dialog_sizing import bounded_size


def record(monkeypatch, module):
    calls = []
    real = module.resize_dialog

    def spy(widget, width, height):
        calls.append((width, height))
        return real(widget, width, height)
    monkeypatch.setattr(module, "resize_dialog", spy)
    return calls


def test_context_entry_dialog_is_sized_through_the_bounded_helper(qtbot, monkeypatch):
    calls = record(monkeypatch, context_dialog)
    dialog = context_dialog.ContextEntryDialog("prod", ["a: b"])
    qtbot.addWidget(dialog)
    assert calls == [(680, 300)]


def test_cloud_clusters_dialog_is_sized_through_the_bounded_helper(qtbot, monkeypatch):
    calls = record(monkeypatch, cloud_clusters_dialog)
    dialog = cloud_clusters_dialog.CloudClustersDialog(lambda: ("/tmp/kc", True), [])
    qtbot.addWidget(dialog)
    assert calls == [(720, 560)]


def test_the_helper_shrinks_a_size_that_does_not_fit_the_screen():
    assert bounded_size(720, 560, 600, 400) == (568, 368)
    assert bounded_size(720, 560, 1920, 1080) == (720, 560)
