"""scripts/record_flaky.py decides, per flaky test, whether to open an issue, comment on one or reopen one."""
import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "record_flaky.py"
spec = importlib.util.spec_from_file_location("record_flaky", SCRIPT)
record_flaky = importlib.util.module_from_spec(spec)
spec.loader.exec_module(record_flaky)


def issue(number, test, state):
    return {"number": number, "title": record_flaky.title_for(test), "state": state}


def test_a_flaky_test_without_an_issue_gets_a_new_one():
    assert record_flaky.plan([], ["tests/test_a.py::test_x"]) == [("create", "tests/test_a.py::test_x", None)]


def test_an_open_issue_gets_a_comment_not_a_second_issue():
    issues = [issue(7, "tests/test_a.py::test_x", "OPEN")]
    assert record_flaky.plan(issues, ["tests/test_a.py::test_x"]) == [("comment", "tests/test_a.py::test_x", 7)]


def test_a_closed_issue_is_reopened():
    issues = [issue(7, "tests/test_a.py::test_x", "CLOSED")]
    assert record_flaky.plan(issues, ["tests/test_a.py::test_x"]) == [("reopen", "tests/test_a.py::test_x", 7)]


def test_only_the_exact_title_matches_so_similar_tests_do_not_share_an_issue():
    issues = [issue(7, "tests/test_a.py::test_x", "OPEN")]
    assert record_flaky.plan(issues, ["tests/test_a.py::test_xy"]) == [("create", "tests/test_a.py::test_xy", None)]


def test_the_first_issue_with_a_title_is_used_when_there_are_duplicates():
    issues = [issue(9, "tests/test_a.py::test_x", "OPEN"), issue(3, "tests/test_a.py::test_x", "CLOSED")]
    assert record_flaky.plan(issues, ["tests/test_a.py::test_x"]) == [("comment", "tests/test_a.py::test_x", 9)]
