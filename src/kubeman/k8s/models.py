from dataclasses import dataclass, field

from kubernetes.utils import parse_quantity


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
    _resources: tuple = field(default=None, repr=False, compare=False)

    @property
    def resources(self):
        """(cpu_req, cpu_lim, mem_req, mem_lim): cores and MiB summed over regular containers, 0 = not set.

        Parsed lazily from the manifest the first time a request/limit column is read, so hidden columns
        cost nothing. A container without a value adds nothing; rows without a manifest give zeros.
        """
        if self._resources is None:
            totals = {("requests", "cpu"): 0.0, ("limits", "cpu"): 0.0,
                      ("requests", "memory"): 0.0, ("limits", "memory"): 0.0}
            for container in ((self.raw or {}).get("spec") or {}).get("containers") or []:
                for kind, name in totals:
                    value = ((container.get("resources") or {}).get(kind) or {}).get(name)
                    if value:
                        try:
                            amount = float(parse_quantity(value))
                        except (ValueError, ArithmeticError):
                            continue
                        totals[(kind, name)] += amount / 2**20 if name == "memory" else amount
            self._resources = tuple(totals.values())
        return self._resources

    cpu_req = property(lambda self: self.resources[0])
    cpu_lim = property(lambda self: self.resources[1])
    mem_req = property(lambda self: self.resources[2])
    mem_lim = property(lambda self: self.resources[3])

    @staticmethod
    def _pct(used, total):
        return used / total * 100 if used >= 0 and total > 0 else -1.0  # -1 = unknown, shown blank

    @property
    def cpu_req_pct(self):
        return self._pct(self.cpu_used, self.cpu_req)

    @property
    def cpu_lim_pct(self):
        return self._pct(self.cpu_used, self.cpu_lim)

    @property
    def mem_req_pct(self):
        return self._pct(self.mem_used, self.mem_req)

    @property
    def mem_lim_pct(self):
        return self._pct(self.mem_used, self.mem_lim)


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
