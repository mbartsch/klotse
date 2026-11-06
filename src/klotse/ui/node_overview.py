"""The overview at the top of a node's tab: status, CPU, memory and pods at a glance. `summarize` is pure (no Qt) and works only
from what the tab already has: the node row (status, allocatable CPU and memory, usage when metrics-server answered) and the pods
listed in the tab (their requests). Usage is optional and unknown stays unknown; nothing is fetched here."""
from kubernetes.utils import parse_quantity
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QLabel, QProgressBar, QWidget

from . import theme
from .status_tone import status_tone
from .workload_view import badge

FINISHED = ("Succeeded", "Failed")  # pods in these phases no longer hold their requests (the scheduler ignores them too)


def active_pods(pods):
    """The pods that take room on the node: everything not Succeeded or Failed."""
    return [p for p in pods if ((getattr(p, "raw", None) or {}).get("status") or {}).get("phase") not in FINISHED]


def _taints(raw):
    out = []
    for t in ((raw or {}).get("spec") or {}).get("taints") or []:
        value = f"={t['value']}" if t.get("value") else ""
        out.append(f"{t.get('key', '?')}{value}:{t.get('effect', '')}")
    return out


def _pod_limit(raw):
    try:
        return int(parse_quantity(((raw or {}).get("status") or {}).get("allocatable", {}).get("pods", 0)))
    except (ValueError, ArithmeticError, TypeError):
        return 0


def summarize(node, pods):
    """Everything the overview shows, as plain data. CPU in cores, memory in GiB; a usage of -1 means metrics are unknown."""
    live = active_pods(pods or [])
    raw = getattr(node, "raw", None) or {}
    labels = (raw.get("metadata") or {}).get("labels") or {}
    facts = [node.roles if node.roles and node.roles != "<none>" else "", node.version, node.ip,
             labels.get("node.kubernetes.io/instance-type", ""), labels.get("topology.kubernetes.io/zone", "")]
    taints = _taints(raw)
    return {
        "status": node.status, "tone": status_tone(node.status),
        "cpu_alloc": node.cpu, "cpu_req": sum(p.cpu_req for p in live), "cpu_used": node.cpu_used,
        "mem_alloc": node.memory, "mem_req": sum(p.mem_req for p in live) / 1024, "mem_used": node.mem_used,
        "pods": len(live), "pod_limit": _pod_limit(raw), "pods_known": pods is not None,
        "facts": [f for f in facts if f], "taints": taints,
    }


def percent(part, whole):
    return part / whole * 100 if whole > 0 and part >= 0 else -1.0


def _amount(value, unit):
    text = f"{value:.2f}".rstrip("0").rstrip(".") if value < 100 else f"{value:.0f}"
    return f"{text} {unit}"


def bar_text(label, part, whole, unit):
    """'requested 3.2 / 4 cores (80%)'; 'n/a' when the node reports no allocatable amount."""
    pct = percent(part, whole)
    if pct < 0:
        return f"{label}: n/a"
    return f"{label} {_amount(part, unit)} / {_amount(whole, unit)} ({pct:.0f}%)" + (" · overcommitted" if pct > 100 else "")


class NodeOverview(QWidget):
    """Status, CPU, memory and pods of a node as a small grid: a bar of what the pods request, one of what is in use now
    (only when metrics-server answered), and the node's facts and taints underneath. Percentages are written out, so no state
    depends on a bar's colour."""

    def __init__(self):
        super().__init__()
        self.status = QLabel()
        self.status.setTextFormat(Qt.RichText)
        self.facts = QLabel()
        self.facts.setTextFormat(Qt.PlainText)
        theme.muted(self.facts)
        self.taints = QLabel()
        self.taints.setTextFormat(Qt.PlainText)
        theme.muted(self.taints)
        self.bars = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 2, 0, 2)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(2)
        grid.addWidget(self.status, 0, 0, 1, 3)
        for row, (key, title) in enumerate((("cpu", "CPU"), ("memory", "Memory"), ("pods", "Pods")), 1):
            label = QLabel(title)
            theme.muted(label)
            requested, used = self._bar(), self._bar()
            self.bars[key] = (requested, used)
            grid.addWidget(label, row, 0)
            grid.addWidget(requested, row, 1)
            grid.addWidget(used, row, 2)
        grid.addWidget(self.facts, 4, 0, 1, 3)
        grid.addWidget(self.taints, 5, 0, 1, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)

    @staticmethod
    def _bar():
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setTextVisible(True)
        bar.setFixedHeight(16)
        return bar

    def _set(self, bar, text, pct):
        bar.setValue(max(0, min(100, int(pct))) if pct >= 0 else 0)
        bar.setFormat(text)
        bar.setToolTip(text)

    def set_summary(self, s):
        self.status.setText(badge(s["status"], s["tone"]))
        for key, unit, req, used, alloc in (("cpu", "cores", s["cpu_req"], s["cpu_used"], s["cpu_alloc"]),
                                            ("memory", "GiB", s["mem_req"], s["mem_used"], s["mem_alloc"])):
            requested, in_use = self.bars[key]
            self._set(requested, bar_text("requested", req, alloc, unit) if s["pods_known"] else "requested: loading…",
                      percent(req, alloc) if s["pods_known"] else -1)
            known = used >= 0
            self._set(in_use, bar_text("in use", used, alloc, unit) if known else "in use: unknown (no metrics)",
                      percent(used, alloc) if known else -1)
        pods, limit = self.bars["pods"]
        self._set(pods, f"{s['pods']} of {s['pod_limit']} pods" if s["pod_limit"] else f"{s['pods']} pods",
                  percent(s["pods"], s["pod_limit"]))
        limit.hide()
        self.facts.setText(" · ".join(s["facts"]))
        self.facts.setVisible(bool(s["facts"]))
        shown = ", ".join(s["taints"][:3]) + (f" +{len(s['taints']) - 3}" if len(s["taints"]) > 3 else "")
        self.taints.setText(f"Taints: {shown}" if s["taints"] else "")
        self.taints.setVisible(bool(s["taints"]))
