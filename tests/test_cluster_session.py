from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from klotse.ui.cluster_session import ClusterSession
from klotse.ui.customizations import LabelSettings
from klotse.ui.main_window import MainWindow
from test_app import FakeService, MultiService, open_active


def store(tmp_path):
    return QSettings(str(tmp_path / "klotse.ini"), QSettings.IniFormat)


def window(qtbot, tmp_path, service=FakeService):
    win = MainWindow(service, label_settings=LabelSettings(store(tmp_path)))
    qtbot.addWidget(win)
    return win


def asked(monkeypatch, answer):
    questions = []

    def question(parent, title, text, *args):
        questions.append((title, text))
        return answer

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    return questions


# ---- the saved list ----------------------------------------------------------
def test_the_session_round_trips_in_order_with_awkward_context_names(tmp_path):
    session = ClusterSession(store(tmp_path))
    assert session.saved() == ([], None)
    names = ["arn:aws:eks:us-east-1:123456789012:cluster/prod", "gke_proj_europe-west1_staging", "a, b; c=d"]
    names += [f"ctx-{i}" for i in range(12)]  # more than ten: numbered keys must keep their numeric order
    session.remember(names, names[1])
    reread = ClusterSession(store(tmp_path))
    assert reread.saved() == (names, names[1])


def test_a_shorter_list_leaves_nothing_stale_and_forget_clears_it(tmp_path):
    session = ClusterSession(store(tmp_path))
    session.remember(["a", "b", "c"], "c")
    session.remember(["a"], None)
    assert ClusterSession(store(tmp_path)).saved() == (["a"], None)
    session.forget()
    assert ClusterSession(store(tmp_path)).saved() == ([], None)


def test_forgetting_when_nothing_is_saved_writes_no_file(tmp_path):
    ClusterSession(store(tmp_path)).forget()
    assert not (tmp_path / "klotse.ini").exists()  # a test or a first start must not create settings


# ---- starting ----------------------------------------------------------------
def test_nothing_connects_by_itself_at_start(qtbot, tmp_path):
    win = window(qtbot, tmp_path)
    assert win.workspaces == {} and win.clusters.count() == 0
    assert win.contexts == ["ctx"]  # still listed in the navigator, just not opened
    assert "Double-click a cluster" in win.status.currentMessage()


def test_remembered_clusters_come_back_and_unknown_ones_are_skipped(qtbot, tmp_path):
    ClusterSession(store(tmp_path)).remember(["a", "gone", "b"], "b")
    MultiService.instances.clear()
    win = window(qtbot, tmp_path, MultiService)
    assert win.workspaces == {}  # the constructor alone still connects nothing
    assert win.restore_clusters() == ["a", "b"]
    assert [win.clusters.tabText(i) for i in range(win.clusters.count())] == ["a", "b"]
    assert win.clusters.currentWidget() is win.workspaces["b"]  # the one that was in front


def test_without_a_saved_session_restore_does_nothing(qtbot, tmp_path):
    win = window(qtbot, tmp_path)
    assert win.restore_clusters() == [] and win.workspaces == {}


# ---- exiting -----------------------------------------------------------------
def test_yes_remembers_the_open_clusters_and_the_one_in_front(qtbot, tmp_path, monkeypatch):
    MultiService.instances.clear()
    win = window(qtbot, tmp_path, MultiService)
    win.show()
    win.open_cluster("a")
    win.open_cluster("b")
    questions = asked(monkeypatch, QMessageBox.Yes)
    assert win.close() is True
    assert len(questions) == 1 and "a, b" in questions[0][1]
    assert ClusterSession(store(tmp_path)).saved() == (["a", "b"], "b")


def test_no_starts_the_next_time_with_a_clean_list(qtbot, tmp_path, monkeypatch):
    ClusterSession(store(tmp_path)).remember(["ctx"], "ctx")
    win = window(qtbot, tmp_path)
    win.show()
    open_active(win)
    asked(monkeypatch, QMessageBox.No)
    assert win.close() is True
    assert ClusterSession(store(tmp_path)).saved() == ([], None)


def test_cancel_keeps_the_app_open_and_the_saved_session_as_it_was(qtbot, tmp_path, monkeypatch):
    ClusterSession(store(tmp_path)).remember(["ctx"], "ctx")
    win = window(qtbot, tmp_path)
    win.show()
    open_active(win)
    asked(monkeypatch, QMessageBox.Cancel)
    assert win.close() is False and win.isVisible() and win._shutting_down is False
    assert ClusterSession(store(tmp_path)).saved() == (["ctx"], "ctx")


def test_exiting_with_no_cluster_open_asks_nothing_and_clears_the_session(qtbot, tmp_path, monkeypatch):
    ClusterSession(store(tmp_path)).remember(["ctx"], "ctx")
    win = window(qtbot, tmp_path)
    win.show()
    questions = asked(monkeypatch, QMessageBox.Yes)
    assert win.close() is True and questions == []
    assert ClusterSession(store(tmp_path)).saved() == ([], None)


def test_a_window_that_was_never_shown_has_nobody_to_ask(qtbot, tmp_path, monkeypatch):
    win = window(qtbot, tmp_path)
    open_active(win)
    questions = asked(monkeypatch, QMessageBox.Yes)
    assert win.confirm_exit() is True and questions == []


def test_a_settings_file_that_cannot_be_written_never_traps_the_user(qtbot, tmp_path, monkeypatch):
    win = window(qtbot, tmp_path)
    win.show()
    open_active(win)
    asked(monkeypatch, QMessageBox.Yes)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a: warned.append(a[2])))

    def broken(*_):
        raise OSError("read-only settings")

    monkeypatch.setattr(win.session, "remember", broken)
    assert win.close() is True and warned == ["read-only settings"]
