"""#497: an action's result stays on the status bar; loading messages cannot replace it for a while."""
from klotse.ui.status_line import StatusLine


class Bar:
    def __init__(self):
        self.shown = []

    def showMessage(self, text, timeout=0):
        self.shown.append((text, timeout))


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_a_result_is_not_replaced_by_loading_text_while_it_is_held():
    bar, clock = Bar(), Clock()
    line = StatusLine(bar, clock=clock, hold_s=10)
    line.show_result("Restart 3 Deployments: 2 succeeded, 1 failed")
    clock.now += 1
    line.show_chatter("ctx / Pods: Loading…")
    line.show_chatter("ctx / Pods: 12 rows, 0.4s")
    assert [text for text, _ in bar.shown] == ["Restart 3 Deployments: 2 succeeded, 1 failed"]
    assert bar.shown[0][1] == 10000  # the bar also clears it after the hold


def test_loading_text_shows_again_once_the_hold_is_over():
    bar, clock = Bar(), Clock()
    line = StatusLine(bar, clock=clock, hold_s=10)
    line.show_result("Deleted Pod web")
    clock.now += 11
    assert not line.held()
    line.show_chatter("ctx / Pods: Loading…")
    assert bar.shown[-1][0] == "ctx / Pods: Loading…"


def test_a_new_result_replaces_the_old_one_and_restarts_the_hold():
    bar, clock = Bar(), Clock()
    line = StatusLine(bar, clock=clock, hold_s=10)
    line.show_result("Scaling Deployment a: 1 → 2")
    clock.now += 9
    line.show_result("Scaling Deployment b: 2 → 3")
    clock.now += 5
    line.show_chatter("loading")
    assert bar.shown[-1][0] == "Scaling Deployment b: 2 → 3"
