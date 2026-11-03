from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QTreeWidget, QTreeWidgetItem

ACTIVE_STATES = ("connecting", "connected")  # shown in the Connected section
STATE_COLORS = {"idle": "#6b7280", "connecting": "#f5a524", "connected": "#2fbf71", "failed": "#e5484d"}

# Shown under the connected cluster, Lens order. A leaf is (key, label); a group is
# (label, [leaves], expanded_by_default).
VIEWS = [
    ("overview", "Overview"),
    ("nodes", "Nodes"),
    ("Workloads", [("workloads", "Overview"), ("pods", "Pods"), ("deployments", "Deployments"),
                   ("daemonsets", "DaemonSets"), ("statefulsets", "StatefulSets"),
                   ("replicasets", "ReplicaSets"), ("jobs", "Jobs"), ("cronjobs", "CronJobs")], True),
    ("Config", [("configmaps", "ConfigMaps"), ("secrets", "Secrets")], False),
    ("Network", [("services", "Services"), ("ingresses", "Ingresses"),
                 ("networkpolicies", "Network Policies")], False),
    ("Storage", [("persistentvolumeclaims", "Persistent Volume Claims"),
                 ("persistentvolumes", "Persistent Volumes"), ("storageclasses", "Storage Classes")], False),
    ("namespaces", "Namespaces"),
    ("events", "Events"),
    ("Custom Resources", [("crds", "Definitions")], False),
]


def dot_icon(color):
    pm = QPixmap(12, 12)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(QColor(color)))
    p.setPen(Qt.NoPen)
    p.drawEllipse(1, 1, 10, 10)
    p.end()
    return QIcon(pm)


class Navigator(QTreeWidget):
    """Lens-style sidebar. Clusters are split into Connected / Disconnected sections, each with a status dot;
    a connected cluster expands into its resource views. Right-click a cluster to connect or disconnect it."""

    context_clicked = Signal(str)
    view_requested = Signal(str, str)  # context name, view key
    connect_requested = Signal(str)
    disconnect_requested = Signal(str)
    reconnect_requested = Signal(str)

    ROLE_KIND = Qt.UserRole
    ROLE_KEY = Qt.UserRole + 1

    def __init__(self):
        super().__init__()
        self.setHeaderHidden(True)
        self.setIndentation(14)
        self._clusters = {}
        self._order = {}  # name -> position in the kubeconfig, so each section keeps a stable order
        self._states = {}
        self._connected_hdr = self._idle_hdr = None
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.itemClicked.connect(self._on_click)

    def set_contexts(self, names):
        self.clear()
        self._clusters, self._states = {}, {}
        self._order = {n: i for i, n in enumerate(names)}
        self._connected_hdr = self._header("Connected")
        self._idle_hdr = self._header("Disconnected")
        for n in names:
            it = QTreeWidgetItem([n])
            it.setData(0, self.ROLE_KIND, "cluster")
            it.setData(0, self.ROLE_KEY, n)
            it.setIcon(0, dot_icon(STATE_COLORS["idle"]))
            self._idle_hdr.addChild(it)
            self._clusters[n] = it
            self._states[n] = "idle"
        self._update_headers()

    def _header(self, label):
        h = QTreeWidgetItem([label])
        h.setData(0, self.ROLE_KIND, "header")
        h.setFlags(Qt.ItemIsEnabled)  # not selectable
        f = h.font(0)
        f.setBold(True)
        h.setFont(0, f)
        h.setForeground(0, QBrush(QColor("#9aa3b2")))
        self.addTopLevelItem(h)
        h.setExpanded(True)
        return h

    def _update_headers(self):
        self._connected_hdr.setText(0, f"CONNECTED ({self._connected_hdr.childCount()})")
        self._idle_hdr.setText(0, f"DISCONNECTED ({self._idle_hdr.childCount()})")

    def cluster_item(self, name):
        return self._clusters.get(name)

    def state(self, name):
        return self._states.get(name)

    def set_state(self, name, state):
        item = self._clusters.get(name)
        if item is None:
            return
        self._states[name] = state
        item.setIcon(0, dot_icon(STATE_COLORS[state]))
        want = self._connected_hdr if state in ACTIVE_STATES else self._idle_hdr
        if item.parent() is want:
            return
        was_current = self.currentItem() is item
        if item.parent() is not None:
            item.parent().removeChild(item)
        rank = self._order[name]
        idx = sum(1 for i in range(want.childCount()) if self._order[want.child(i).data(0, self.ROLE_KEY)] < rank)
        want.insertChild(idx, item)
        self._update_headers()
        if was_current:
            self.setCurrentItem(item)

    def show_views(self, name):
        """Expand `name` into its resource views. Any number of clusters can be expanded at once."""
        item = self._clusters.get(name)
        if item is None or item.childCount():
            return
        for entry in VIEWS:
            if len(entry) == 3:
                label, leaves, expanded = entry
                group = QTreeWidgetItem([label])
                item.addChild(group)
                for k, lab in leaves:
                    self._leaf(group, k, lab)
                group.setExpanded(expanded)
            else:
                self._leaf(item, *entry)
        item.setExpanded(True)

    def _leaf(self, parent, key, label):
        it = QTreeWidgetItem([label])
        it.setData(0, self.ROLE_KIND, "view")
        it.setData(0, self.ROLE_KEY, key)
        parent.addChild(it)

    def hide_views(self, name):
        item = self._clusters.get(name)
        if item is not None:
            item.takeChildren()

    def select_cluster(self, name):
        item = self._clusters.get(name)
        if item is not None:
            self.setCurrentItem(item)

    def select_view(self, name, key):
        def walk(item):
            for i in range(item.childCount()):
                c = item.child(i)
                if c.data(0, self.ROLE_KEY) == key:
                    self.setCurrentItem(c)
                    return True
                if walk(c):
                    return True
            return False

        item = self._clusters.get(name)
        if item:
            walk(item)

    @staticmethod
    def _cluster_of(item):
        """The cluster item that `item` is (or lives under); None for section headers."""
        while item is not None and item.data(0, Navigator.ROLE_KIND) != "cluster":
            item = item.parent()
        return item

    def actions_for(self, name):
        """Context-menu entries for a cluster as (label, signal) pairs."""
        if self._states.get(name) in ACTIVE_STATES:
            return [("Reconnect", self.reconnect_requested), ("Disconnect", self.disconnect_requested)]
        return [("Connect", self.connect_requested)]

    def _show_menu(self, pos):
        cluster = self._cluster_of(self.itemAt(pos))
        if cluster is None:
            return
        name = cluster.data(0, self.ROLE_KEY)
        menu = QMenu(self)
        for label, signal in self.actions_for(name):
            menu.addAction(label, lambda sig=signal: sig.emit(name))
        menu.exec(self.viewport().mapToGlobal(pos))

    def _on_click(self, item, _col):
        kind = item.data(0, self.ROLE_KIND)
        if kind == "cluster":
            self.context_clicked.emit(item.data(0, self.ROLE_KEY))
        elif kind == "view":
            cluster = self._cluster_of(item)  # views live under groups under the cluster item
            self.view_requested.emit(cluster.data(0, self.ROLE_KEY), item.data(0, self.ROLE_KEY))
