"""scripts/run_tests.py ends a test process that stops writing progress, and returns the status of one that does not."""
import importlib.util
import pathlib
import sys

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "run_tests.py"
spec = importlib.util.spec_from_file_location("run_tests", SCRIPT)
run_tests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_tests)


def test_a_process_that_finishes_gives_its_status(tmp_path):
    progress = str(tmp_path / "progress.txt")
    assert run_tests.run_watched([sys.executable, "-c", "raise SystemExit(3)"], None, progress, limit=5) == 3


def test_a_process_that_stops_writing_progress_is_ended_and_reported_as_124(tmp_path):
    progress = str(tmp_path / "progress.txt")
    command = [sys.executable, "-c", "import time; time.sleep(120)"]
    assert run_tests.run_watched(command, None, progress, limit=1, poll=0.2) == 124


def test_a_process_that_keeps_writing_progress_is_left_alone(tmp_path):
    progress = str(tmp_path / "progress.txt")
    script = ("import time, pathlib\n"
              f"p = pathlib.Path({progress!r})\n"
              "for _ in range(8):\n"
              "    p.open('a').write('x' + chr(10)); time.sleep(0.3)\n")
    assert run_tests.run_watched([sys.executable, "-c", script], None, progress, limit=1, poll=0.1) == 0
