from types import SimpleNamespace

from klotse.ui.load_errors import explain
from klotse.ui.workspace import ClusterWorkspace

FORBIDDEN = ('ApiException: (403)\nReason: Forbidden\nHTTP response headers: HTTPHeaderDict({})\n'
             'HTTP response body: {"kind":"Status","message":"pods is forbidden: User \\"bob\\" cannot list resource \\"pods\\" '
             'in API group \\"\\" in the namespace \\"x\\"","reason":"Forbidden","code":403}')


def test_a_403_is_explained_with_the_servers_sentence():
    text = explain(FORBIDDEN, "Pods")
    assert "Refused: you may not list Pods" in text and "not an empty list" in text
    assert 'Server: pods is forbidden: User "bob" cannot list resource "pods"' in text
    assert "My permissions" in text


def test_a_401_says_not_signed_in():
    text = explain("ApiException: (401)\nReason: Unauthorized", "Nodes")
    assert text.startswith("Not signed in") and "kubeconfig" in text


def test_other_errors_pass_through_unchanged():
    for message in ("ReadTimeoutError", "ApiException: (500)\nReason: Internal Server Error", "MaxRetryError: connection refused"):
        assert explain(message, "Pods") == message


def test_a_refused_list_shows_the_explanation_not_the_raw_exception(qtbot):
    def refuse(namespace):
        raise RuntimeError(FORBIDDEN)

    ws = ClusterWorkspace(SimpleNamespace(), "ctx")
    qtbot.addWidget(ws)
    ws.loaders["pods"] = (refuse, True, ws.pod_table.set_rows)
    ws.load_view("pods")
    qtbot.waitUntil(lambda: "Load failed" in ws.pod_table.status.text(), timeout=5000)
    assert "Refused: you may not list Pods" in ws.pod_table.status.text()
    assert "\n" not in ws.pod_table.status.text()
    assert "My permissions" in ws.pod_table.loading_page.label.text()
