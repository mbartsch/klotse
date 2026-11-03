"""Regressions for large-cluster loading; all data is local."""
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace

import pytest
from kubernetes import client
from kubernetes.client.exceptions import ApiException
from PySide6.QtCore import QThread, Qt

from klotse.k8s.client import KubeService
from klotse.ui.resource_table import ResourceTable
from klotse.ui.workspace import ClusterWorkspace


def test_connection_requests_compressed_responses(monkeypatch):
    from klotse.k8s import client as module

    monkeypatch.setattr(module.config, 'load_kube_config', lambda **kw: None)
    clients = []

    def core(api):
        clients.append(api)
        return SimpleNamespace(get_api_resources=lambda **kw: None)

    monkeypatch.setattr(module.client, 'CoreV1Api', core)
    svc = KubeService()
    svc.connect('test')
    assert clients[0].default_headers['Accept-Encoding'] == 'gzip'
    clients[0].close()


def test_pod_loading_and_errors_remain_visible_in_view(qtbot):
    ws = ClusterWorkspace(None, 'test')
    qtbot.addWidget(ws)
    pending = []
    ws._run = lambda fn, ok, *args, on_error=None: pending.append((ok, on_error))
    ws.loaders['pods'] = (lambda ns: [], True, ws.pod_table.set_rows)
    ws.load_view('pods')
    assert ws.pod_table.status.text() == 'Loading…'
    ws.pod_table._loading_tick()
    assert ws.pod_table.status.text().startswith('Loading… 0s')
    pending.pop()[1]('ReadTimeoutError')
    assert ws.pod_table.status.text() == 'Load failed: ReadTimeoutError'
    assert not ws.pod_table._load_timer.isActive()
    ws.load_view('pods')
    pending.pop()[0]([])
    assert ws.pod_table.status.text() == 'No resources found'
    assert not ws.pod_table._load_timer.isActive()


def test_pagination_keeps_scope_and_all_pages():
    calls = []

    def fetch(namespace, **kwargs):
        calls.append((namespace, kwargs))
        token = kwargs['_continue']
        return SimpleNamespace(items=[2] if token else [1],
                               metadata=SimpleNamespace(_continue=None if token else 'next'))

    assert list(KubeService._iter_list(fetch, 'team', field_selector='type=Warning')) == [1, 2]
    assert [kw['_continue'] for _, kw in calls] == [None, 'next']
    assert all(ns == 'team' and kw['limit'] == 500 and kw['_request_timeout'] == 30
               and kw['field_selector'] == 'type=Warning' for ns, kw in calls)


def test_expired_page_fails_instead_of_returning_partial_snapshot():
    def fetch(**kwargs):
        if kwargs['_continue']:
            raise ApiException(status=410, reason='ResourceExpired')
        return SimpleNamespace(items=[1], metadata=SimpleNamespace(_continue='expired'))

    with pytest.raises(ApiException):
        list(KubeService._iter_list(fetch))


def test_overview_only_serializes_recent_events(monkeypatch):
    from klotse.k8s import resources

    now = datetime.now(timezone.utc)
    events = [client.CoreV1Event(metadata=client.V1ObjectMeta(name=str(i), creation_timestamp=now + timedelta(seconds=i)),
                                 involved_object=client.V1ObjectReference(name='p', kind='Pod'),
                                 type='Warning' if i % 2 else 'Normal') for i in range(1000)]
    svc = KubeService()
    svc._resource_items = lambda *a: iter(events)
    serialized = []
    original = resources.to_dict
    monkeypatch.setattr(resources, 'to_dict', lambda e: (serialized.append(e), original(e))[1])
    recent, warnings, warned = svc._overview_events()
    assert warnings == 500 and len(serialized) == 25  # the 500 drill-down warnings are not serialized up front
    assert [row.name for row in recent] == [str(i) for i in range(999, 974, -1)]
    # drill-down: every warning (newest first), each manifest serialized only when its row is first read
    assert [row.name for row in warned[:3]] == ['999', '997', '995'] and len(warned) == 500
    assert warned[0].raw['metadata']['name'] == '999' and len(serialized) == 26
    assert warned[0].raw is warned[0].raw and len(serialized) == 26  # cached after the first read


def test_overview_pods_skips_manifest_serialization(monkeypatch):
    from klotse.k8s import client as module

    pod = client.V1Pod(metadata=client.V1ObjectMeta(name='p'),
                       spec=client.V1PodSpec(containers=[]), status=client.V1PodStatus(phase='Running'))
    svc = KubeService()
    svc._core = SimpleNamespace(list_pod_for_all_namespaces=lambda **kw:
        SimpleNamespace(items=[pod] * 10000, metadata=None))
    monkeypatch.setattr(module, '_raw', lambda _: pytest.fail('Overview serialized a pod manifest'))
    counts, problems = svc._overview_pods()
    assert counts == {'pods': 10000, 'running': 10000, 'pending': 0, 'failed': 0}
    assert problems == {'pending': [], 'failed': []}  # healthy pods are never kept for the drill-down


def test_large_table_initial_load_and_filter(qtbot):
    from klotse.k8s.models import PodRow

    table = ResourceTable([('Name', 'name'), ('Restarts', 'restarts')], 'Pod')
    qtbot.addWidget(table)
    table.set_rows([PodRow(f'pod-{i}', 'ns', '1/1', 'Running', i, '', '') for i in range(10000)])
    assert table.proxy.rowCount() == 10000 and table.proxy.sortColumn() == -1
    table.filter.setText('pod-9999')
    qtbot.waitUntil(lambda: table.proxy.rowCount() == 1)
    assert table.proxy.index(0, 0).data() == 'pod-9999'
    table.filter.clear()
    qtbot.waitUntil(lambda: table.proxy.rowCount() == 10000)
    table.view.sortByColumn(1, Qt.DescendingOrder)
    assert table.proxy.index(0, 1).data() == '9999'


def test_refresh_coalesces_and_ignores_stale_results(qtbot):
    ws = ClusterWorkspace(None, 'test')
    qtbot.addWidget(ws)
    calls, received, threads = [], [], []
    release = Event()

    def fetch(namespace):
        calls.append(namespace)
        if len(calls) == 1:
            assert release.wait(5)
        return namespace

    def sink(data):
        received.append(data)
        threads.append(QThread.currentThread())

    ws.ns_combo.addItems(['old', 'new'])
    ws.loaders['pods'] = (fetch, True, sink)
    ws.ns_combo.setCurrentText('old')
    ws.load_view('pods')
    try:
        qtbot.waitUntil(lambda: calls == ['old'])
        ws.ns_combo.setCurrentText('new')
        for _ in range(100):
            ws.load_view('pods')
        assert len(ws._workers) == 1
    finally:
        release.set()
    qtbot.waitUntil(lambda: received == ['new'])
    assert calls == ['old', 'new'] and threads == [ws.thread()]


def test_load_after_error_can_retry_and_closed_workspace_skips_result(qtbot):
    ws = ClusterWorkspace(None, 'test')
    qtbot.addWidget(ws)
    pending, received = [], []
    ws._run = lambda fn, ok, *args, on_error=None: pending.append((ok, on_error))
    ws.loaders['pods'] = (lambda ns: [], True, received.append)
    ws.load_view('pods')
    pending.pop()[1]('API down')
    assert 'pods' not in ws._loading
    ws.load_view('pods')
    ws.shutdown()
    pending.pop()[0](['late'])
    assert received == []


@pytest.mark.parametrize('namespace', [None, 'team'])
def test_workload_overview_counts_pages_without_mapping_manifests(namespace, monkeypatch):
    from klotse.k8s import client as module

    calls = []

    def listing(*args, **kwargs):
        calls.append((args, kwargs['_continue']))
        return SimpleNamespace(items=[object()], metadata=SimpleNamespace(
            _continue=None if kwargs['_continue'] else 'next'))

    svc = KubeService()
    svc._apps = SimpleNamespace(**{name: listing for name in (
        'list_deployment_for_all_namespaces', 'list_namespaced_deployment',
        'list_replica_set_for_all_namespaces', 'list_namespaced_replica_set',
        'list_stateful_set_for_all_namespaces', 'list_namespaced_stateful_set')})
    svc._apis = {'apps': svc._apps, 'batch': SimpleNamespace()}
    svc._apps.list_daemon_set_for_all_namespaces = listing
    svc._apps.list_namespaced_daemon_set = listing
    for kind in ('job', 'cron_job'):
        setattr(svc._apis['batch'], f'list_{kind}_for_all_namespaces', listing)
        setattr(svc._apis['batch'], f'list_namespaced_{kind}', listing)
    svc._overview_pods = lambda ns: ({'pods': 3, 'running': 2, 'pending': 1, 'failed': 0}, {'pending': [], 'failed': []})
    svc._overview_events = lambda ns: ([], 4, [])
    monkeypatch.setattr(module, '_raw', lambda _: pytest.fail('Count-only path serialized a manifest'))
    data = svc.overview_workloads(namespace)
    assert data == {'counts': {'pods': 3, 'running': 2, 'pending': 1, 'failed': 0, 'warnings': 4,
                              **{k: 2 for k in ('deployments', 'replicasets', 'statefulsets', 'daemonsets', 'jobs', 'cronjobs')}},
                    'events': [], 'lists': {'pending': [], 'failed': [], 'warnings': []}}
    assert len(calls) == 12
    assert all(args == ((namespace,) if namespace else ()) for args, _ in calls)


def test_repeated_refresh_still_displays_completed_same_scope_load(qtbot):
    ws = ClusterWorkspace(None, 'test')
    qtbot.addWidget(ws)
    pending, received = [], []
    ws._run = lambda fn, ok, *args, on_error=None: pending.append(ok)
    ws.loaders['pods'] = (lambda ns: [], True, received.append)
    ws.load_view('pods')
    for _ in range(100):
        ws.load_view('pods')
    pending.pop(0)(['first'])
    assert received == [['first']] and len(pending) == 1
    pending.pop(0)(['second'])
    assert received == [['first'], ['second']] and not ws._loading


def test_cluster_overview_uses_numeric_node_units_without_serializing(monkeypatch):
    from klotse.k8s import client as module

    node = client.V1Node(metadata=client.V1ObjectMeta(name='n'),
                         status=client.V1NodeStatus(allocatable={'cpu': '1500m', 'memory': '8Gi'}))
    svc = KubeService()
    svc._core = SimpleNamespace(
        list_node=lambda **kw: SimpleNamespace(items=[node], metadata=None),
        list_namespace=lambda **kw: SimpleNamespace(items=[object()], metadata=None))
    svc._overview_pods = lambda: ({'pods': 3, 'running': 2, 'pending': 1, 'failed': 0}, {'pending': [], 'failed': []})
    svc._overview_events = lambda: ([], 4, [])
    monkeypatch.setattr(module, '_raw', lambda _: pytest.fail('Overview serialized a node manifest'))
    counts = svc.overview_cluster()['counts']
    assert counts['cpu'] == '1.5 cores' and counts['memory'] == '8.00 GiB'
    assert counts['nodes'] == counts['namespaces'] == 1
    assert counts['pods'] == 3 and counts['warnings'] == 4
