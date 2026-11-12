"""Run the test suite for CI so that one bad test does not hide the others.

    python scripts/run_tests.py [test files ...]        (no files = everything under tests/)

* If the pytest process dies in a test (a segmentation fault, say), the tests that had not run yet are run in a new process,
  and so on, so an abort never leaves tests unrun.
* Tests that failed, and tests the process died in, are run once more at the end, each group in a process of its own.
  One that passes then is reported as flaky (a warning on GitHub) and does not fail the run; one that fails again does.
* A process that dies after every test has finished (the known crash while Qt is torn down) is reported as a warning.

tests/conftest.py writes the progress file this reads (KLOTSE_TEST_PROGRESS) and obeys the two lists it writes
(KLOTSE_TEST_SKIP: tests to leave out; KLOTSE_TEST_ONLY: the only tests to run).
"""
import os
import subprocess  # nosec B404 - runs pytest with a fixed argument list
import sys
import tempfile

MAX_PROCESSES = 8  # a suite that keeps dying is failed instead of being restarted for ever


def read_progress(path):
    """What one pytest process got through: (finished ids in order, failed ids in order, the id it died in or None)."""
    finished, failed, started = [], [], None
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        lines = []
    for line in lines:
        what, _, test = line.partition("\t")
        if what == "start":
            started = test
        elif what == "failed" and test not in failed:
            failed.append(test)
        elif what == "done":
            finished.append(test)
            if started == test:
                started = None
    return finished, failed, started


def crashed(status):
    """pytest ends with 0 (passed), 1 (tests failed) or 5 (nothing collected); anything else means it did not finish normally."""
    return status not in (0, 1, 5)


def run_pytest(files, folder, number, skip=None, only=None, runner=subprocess.run):
    progress = os.path.join(folder, f"progress-{number}.txt")
    env = {**os.environ, "KLOTSE_TEST_PROGRESS": progress}
    for name, tests in (("KLOTSE_TEST_SKIP", skip), ("KLOTSE_TEST_ONLY", only)):
        env.pop(name, None)
        if tests is not None:
            env[name] = os.path.join(folder, f"{name.lower()}-{number}.txt")
            with open(env[name], "w", encoding="utf-8") as handle:
                handle.write("\n".join(tests))
    status = runner([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *files], env=env).returncode  # nosec B603
    return status, read_progress(progress)


def run(files, runner=subprocess.run, say=print):
    """Returns the exit status for CI: 0 when every test passed, at the latest when it was run again."""
    with tempfile.TemporaryDirectory(prefix="klotse-tests-") as folder:
        seen, failed, died_in, notes, number = [], [], [], [], 0
        while True:  # the main pass, started again after each death with what has not run yet
            number += 1
            status, (finished, bad, dying) = run_pytest(files, folder, number, skip=seen if number > 1 else None, runner=runner)
            seen += finished
            failed += [test for test in bad if test not in failed]
            if not crashed(status):
                break
            if dying is None:
                if not finished and number == 1:
                    say(f"pytest ended with status {status} before any test ran")
                    return status
                notes.append(f"the test process ended with status {status} after its tests had finished")
                break
            say(f"\n==== the test process died (status {status}) in {dying}; running the remaining tests in a new process ====\n")
            died_in.append(dying)
            seen.append(dying)
            failed = [test for test in failed if test != dying]
            if number >= MAX_PROCESSES:
                say(f"the test process died {number} times; giving up")
                return 1
        still_bad = []
        if failed:  # once more, after everything else, in one fresh process
            say(f"\n==== running {len(failed)} failed test(s) again ====\n")
            number += 1
            status, (finished, bad, dying) = run_pytest(files, folder, number, only=failed, runner=runner)
            still_bad += [test for test in failed if test in bad or test == dying or test not in finished]
        for test in died_in:  # each by itself: one that dies again must not take the others with it
            say(f"\n==== running {test} again (the process died in it) ====\n")
            number += 1
            status, (finished, bad, dying) = run_pytest(files, folder, number, only=[test], runner=runner)
            if status != 0 or test not in finished:
                still_bad.append(test)
        flaky = [test for test in failed + died_in if test not in still_bad]
        say("\n==== summary ====")
        say(f"{len(seen)} test(s) run in the main pass, {len(failed)} failed, the process died in {len(died_in)}")
        for test in flaky:
            say(f"::warning::flaky: {test} " + ("made the test process die" if test in died_in else "failed") + " and passed when run again")
        for note in notes:
            say(f"::warning::{note}")
        for test in still_bad:
            say(f"::error::{test} failed again when run by itself")
        return 1 if still_bad else 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
