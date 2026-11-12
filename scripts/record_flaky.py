"""Record flaky tests as GitHub issues: one issue per test, found by its exact title.

    python scripts/record_flaky.py REPORT_FILE

REPORT_FILE lists one test per line (written by scripts/run_tests.py when a test failed or died and passed when run again).
For each test: no issue yet -> open one (label flaky-test); an open issue -> add a comment; a closed issue -> reopen it and
comment. The run's link and the PR number (PR_NUMBER, when the run is for a pull request) go into the comment.
Needs the gh CLI with GH_TOKEN; without a report file it does nothing.
"""
import datetime
import json
import os
import subprocess  # nosec B404 - runs the gh CLI with a fixed argument list
import sys

LABEL = "flaky-test"
PREFIX = "Flaky test: "
PARENT = 385  # the "Flacky Test" tracker issue; new flaky-test issues become its sub-issues
MILESTONE = "1.0.0"


def title_for(test):
    return PREFIX + test


def plan(issues, tests):
    """What to do for each flaky test, given the existing issues ({number, title, state}). Returns (action, test, number)."""
    by_title = {}
    for issue in issues:
        by_title.setdefault(issue["title"], issue)  # the first one with the title wins
    actions = []
    for test in tests:
        found = by_title.get(title_for(test))
        if found is None:
            actions.append(("create", test, None))
        elif found["state"] == "OPEN":
            actions.append(("comment", test, found["number"]))
        else:
            actions.append(("reopen", test, found["number"]))
    return actions


def run_url():
    server, repo, run_id = (os.environ.get(name, "") for name in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return f"{server}/{repo}/actions/runs/{run_id}" if run_id else "(not run in GitHub Actions)"


def note(test):
    pr = os.environ.get("PR_NUMBER", "")
    when = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"Failed in a test run and passed when run again by itself ({when}).", "", f"Run: {run_url()}"]
    if pr:
        lines.append(f"Pull request: #{pr}")
    lines.append("")
    lines.append(f"Test: `{test}`")
    return "\n".join(lines)


def gh(*args, body=None):
    return subprocess.run(["gh", *args], capture_output=True, text=True, input=body, check=True).stdout  # nosec B603 B607


def attach_to_parent(number, say=print):
    """Make issue NUMBER a sub-issue of PARENT. Best effort: a failure is reported, never raised."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo or not number.isdigit():
        return
    try:
        issue_id = gh("api", f"repos/{repo}/issues/{number}", "--jq", ".id").strip()
        gh("api", "--method", "POST", f"repos/{repo}/issues/{PARENT}/sub_issues", "-F", f"sub_issue_id={issue_id}")
    except subprocess.CalledProcessError as error:
        say(f"could not add #{number} under #{PARENT}: {(error.stderr or '').strip()[:200]}")


def record(path, say=print):
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as handle:
        tests = [line.strip() for line in handle if line.strip()]
    if not tests:
        return 0
    gh("label", "create", LABEL, "--force", "--description", "A test that failed and passed when run again")
    issues = json.loads(gh("issue", "list", "--label", LABEL, "--state", "all", "--limit", "1000", "--json", "number,title,state"))
    for action, test, number in plan(issues, tests):
        if action == "create":
            body = note(test) + "\n\nThe test runner (scripts/run_tests.py) found this test flaky. Its cause is not known yet."
            args = ["issue", "create", "--label", LABEL, "--title", title_for(test), "--body", body]
            try:
                url = gh(*args, "--milestone", MILESTONE)
            except subprocess.CalledProcessError:  # no such milestone: still open the issue
                url = gh(*args)
            say(f"opened {url.strip()} for {test}")
            attach_to_parent(url.strip().rsplit("/", 1)[-1], say)
        else:
            if action == "reopen":
                gh("issue", "reopen", str(number))
            gh("issue", "comment", str(number), "--body-file", "-", body=note(test))
            say(f"{'reopened and commented' if action == 'reopen' else 'commented'} on #{number} for {test}")
    return 0


if __name__ == "__main__":
    sys.exit(record(sys.argv[1]) if len(sys.argv) > 1 else 0)
