"""#497: errors read as sentences with a title that says where they came from; the full text stays in Activity."""
from datetime import datetime

from klotse.ui.activity_log import Activity, render
from klotse.ui.load_errors import brief, describe

CONFLICT = ('ApiException: (409)\nReason: Conflict\nHTTP response headers: HTTPHeaderDict({})\n'
            'HTTP response body: {"kind":"Status","message":"Operation cannot be fulfilled on deployments.apps \\"web\\": '
            'the object has been modified","code":409}')


def test_an_api_error_keeps_the_status_and_the_server_sentence_and_loses_the_headers_and_body_json():
    title, text = describe(CONFLICT)
    assert title == "Kubernetes error"
    assert "(409 Conflict)" in text and 'Server: Operation cannot be fulfilled on deployments.apps "web"' in text
    assert "HTTPHeaderDict" not in text and '"kind"' not in text
    assert "Refresh" in text  # the hint for 409


def test_an_unreachable_cluster_is_titled_as_such_with_the_network_hint():
    title, text = describe("MaxRetryError: HTTPSConnectionPool(host='x', port=443): Max retries exceeded")
    assert title == "Cannot reach the cluster" and "VPN" in text


def test_a_local_refusal_loses_the_exception_name():
    assert describe("RuntimeError: The helm command was not found. Install Helm.") == ("Cannot do that",
                                                                                      "The helm command was not found. Install Helm.")
    assert describe("nsenter requires a Linux node.") == ("Cannot do that", "nsenter requires a Linux node.")


def test_an_unknown_exception_keeps_its_name_so_it_can_be_reported():
    assert describe("KeyError: 'spec'") == ("Unexpected error", "KeyError: 'spec'")


def test_brief_is_one_line_for_lists():
    assert "\n" not in brief(CONFLICT) and brief(CONFLICT).startswith("The cluster refused this (409 Conflict).")


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 15, 9, 30, 5)

    def __call__(self):
        return self.now


def test_activity_keeps_the_complete_error_and_renders_it_with_its_time():
    activity = Activity(clock=Clock())
    activity.add(True, "Deleted Pod web")
    entry = activity.add(False, "The cluster refused this (409 Conflict).", CONFLICT)
    assert entry[0] == "09:30:05" and entry[1] is False and entry[3] == CONFLICT
    rendered = render(entry)
    assert rendered.startswith("09:30:05  ✖  The cluster refused this") and "HTTPHeaderDict" in rendered


def test_activity_does_not_repeat_a_detail_that_is_the_same_as_the_text():
    activity = Activity(clock=Clock())
    assert "\n" not in render(activity.add(True, "Deleted Pod web", "Deleted Pod web"))


def test_activity_keeps_only_the_latest_entries():
    from klotse.ui import activity_log
    activity = Activity(clock=Clock())
    for i in range(activity_log.MAX_ENTRIES + 5):
        activity.add(True, f"step {i}")
    assert len(activity.entries) == activity_log.MAX_ENTRIES and activity.entries[-1][2] == f"step {activity_log.MAX_ENTRIES + 4}"


def test_a_listener_hears_each_new_entry_while_the_panel_is_open():
    activity = Activity(clock=Clock())
    heard = []
    activity.listener = heard.append
    activity.add(True, "Saved ConfigMap a")
    assert heard and heard[0][2] == "Saved ConfigMap a"
