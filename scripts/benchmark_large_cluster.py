"""Offline benchmark. Run with PYTHONPATH=src QT_QPA_PLATFORM=offscreen python scripts/benchmark_large_cluster.py."""
import argparse
import json
from time import perf_counter

from kubernetes import client
from PySide6.QtWidgets import QApplication

from klotse.k8s.client import map_pod, pod_buckets
from klotse.ui.resource_table import ResourceTable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pods', type=int, default=10000)
    args = parser.parse_args()
    app = QApplication([])
    pod = client.V1Pod(
        metadata=client.V1ObjectMeta(name='pod', namespace='default'),
        spec=client.V1PodSpec(containers=[client.V1Container(
            name='app', image='example:1', env=[client.V1EnvVar(name=f'VAR{i}', value='x' * 80) for i in range(10)])]),
        status=client.V1PodStatus(phase='Running'))
    started = perf_counter()
    rows = [map_pod(pod) for _ in range(args.pods)]
    mapping = perf_counter() - started
    table = ResourceTable([('Name', 'name'), ('Namespace', 'namespace'), ('Ready', 'ready'),
                           ('Status', 'status'), ('Restarts', 'restarts'), ('Node', 'node'), ('Age', 'age')], 'Pod')
    started = perf_counter()
    table.set_rows(rows)
    app.processEvents()
    rendering = perf_counter() - started
    started = perf_counter()
    pod_buckets(map_pod(pod, include_raw=False) for _ in range(args.pods))
    overview = perf_counter() - started
    print(json.dumps({'pods': args.pods, 'mapping_s': round(mapping, 3),
                      'table_load_s': round(rendering, 3), 'overview_pod_summary_s': round(overview, 3)}))


if __name__ == '__main__':
    main()
