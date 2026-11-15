"""The status bar text: an action's outcome stays readable for a while, and loading chatter cannot replace it in that time (#497).

Pure logic over any object with `showMessage(text, timeout_ms)`; the clock is injectable for tests.
"""
import time

RESULT_HOLD_S = 10.0  # how long an action result is kept on the status bar before load messages may replace it


class StatusLine:
    def __init__(self, bar, clock=time.monotonic, hold_s=RESULT_HOLD_S):
        self.bar = bar
        self.clock = clock
        self.hold_s = hold_s
        self._held_until = 0.0

    def show_result(self, text):
        """An action's outcome (saved, deleted, 3 succeeded and 1 failed): shown now and held."""
        self._held_until = self.clock() + self.hold_s
        self.bar.showMessage(text, int(self.hold_s * 1000))

    def show_chatter(self, text):
        """Progress and load messages: dropped while an action result is held, so the result stays readable."""
        if self.clock() < self._held_until:
            return
        self.bar.showMessage(text)

    def held(self):
        return self.clock() < self._held_until
