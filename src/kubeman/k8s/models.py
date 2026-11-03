from dataclasses import dataclass, field


@dataclass
class NamespaceRow:
    name: str
    status: str
    age: str
    labels: str
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class PodRow:
    name: str
    namespace: str
    ready: str
    status: str
    restarts: int
    node: str
    age: str
    raw: dict = field(default_factory=dict, repr=False)
    cpu_used: float = -1.0  # cores in use now; -1 when metrics-server is unavailable
    mem_used: float = -1.0  # MiB in use now


@dataclass
class NodeRow:
    name: str
    status: str
    roles: str
    version: str
    ip: str
    cpu: float  # allocatable cores
    memory: float  # allocatable GiB
    age: str
    raw: dict = field(default_factory=dict, repr=False)
    cpu_used: float = -1.0  # cores in use now; -1 when metrics-server is unavailable
    mem_used: float = -1.0  # GiB in use now
    pods: int = -1  # running/pending pods on the node; -1 until counted (only visible rows are counted)

    @property
    def is_ready(self):
        return self.status.split(",", 1)[0] == "Ready"


@dataclass
class ReplicaSetRow:
    name: str
    namespace: str
    desired: int
    current: int
    ready: int
    owner: str
    age: str
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class DeploymentRow:
    name: str
    namespace: str
    ready: str
    up_to_date: int
    available: int
    age: str
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class StatefulSetRow:
    name: str
    namespace: str
    ready: str
    age: str
    raw: dict = field(default_factory=dict, repr=False)
